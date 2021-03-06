"""Copyright 2015 Google Inc. All Rights Reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.


A class to hold device attributes.

This class is used by the Cloud Print Logo Certification tool, to hold the
attributes of a device. Before the device attributes are fully populated,
the methods GetDeviceDetails and GetDeviceCDD must be run.
"""

import json

from _cloudprintmgr import CloudPrintMgr
from _common import Extract
from _config import Constants
from _jsonparser import JsonParser
from _privet import Privet
from _transport import Transport


class Device(object):
  """The basic device object."""

  def __init__(self, logger, chromedriver, model=None, privet_port=None):
    """Initialize a device object.

    Args:
      logger: initialized logger object.
      chromedriver: initialized chromedriver object.
      model: string, unique model or name of device.
      privet_port: integer, tcp port devices uses for Privet protocol.
    """
    if model:
      self.model = model
    else:
      self.model = Constants.PRINTER['MODEL']
    self.logger = logger
    self.cd = chromedriver
    self.cloudprintmgr = CloudPrintMgr(logger, chromedriver)
    self.ipv4 = Constants.PRINTER['IP']
    if privet_port:
      self.port = privet_port
    else:
      self.port = Constants.PRINTER['PORT']
    self.name = Constants.PRINTER['NAME']
    self.status = None
    self.messages = {}
    self.details = {}
    self.error_state = False
    self.warning_state = False
    self.cdd = {}
    self.info = None

    self.url = 'http://%s:%s' % (self.ipv4, self.port)
    self.logger.info('Device URL: %s', self.url)
    self.transport = Transport(logger)
    self.jparser = JsonParser(logger)
    self.headers = None
    self.privet = Privet(logger)
    self.privet_url = self.privet.SetPrivetUrls(self.ipv4, self.port)
    self.GetPrivetInfo()

  def GetPrivetInfo(self):
    self.privet_info = {}
    response = self.transport.HTTPReq(self.privet_url['info'],
                                      headers=self.privet.headers_empty)
    info = self.jparser.Read(response['data'])
    if info['json']:
      for key in info:
        self.privet_info[key] = info[key]
        self.logger.debug('Privet Key: %s', key)
        self.logger.debug('Value: %s', info[key])
        self.logger.debug('--------------------------')
      if 'x-privet-token' in info:
        self.headers = {'X-Privet-Token': str(info['x-privet-token'])}
    else:
      if response['code']:
        self.logger.info('HTTP device return code: %s', response['code'])
      if response['headers']:
        self.logger.debug('HTTP Headers:  ')
        for key in response['headers']:
          self.logger.debug('%s: %s', key, response['headers'][key])
      if response['data']:
        self.logger.info('Data from response: %s', response['data'])

  def GetDeviceDetails(self):
    """Get the device details from our management page.

    This will populate a Device object with device name, status, state messages,
    and device details.
    """

    RETRY_COUNT = 3

    self.error_state = None
    self.warning_state = None
    self.status = None
    self.messages = None
    self.details = None

    for i in range(-1, RETRY_COUNT):
      self.cd.page_id = None
      if not self.error_state:
        self.error_state = self.cloudprintmgr.GetPrinterErrorState(self.name)
      if not self.warning_state:
        self.warning_state = self.cloudprintmgr.GetPrinterWarningState(self.name)
      if not self.status:
        self.status = self.cloudprintmgr.GetPrinterState(self.name)
      if not self.messages:
        self.messages = self.cloudprintmgr.GetPrinterStateMessages(self.name)
      if not self.details:
        self.details = self.cloudprintmgr.GetPrinterDetails(self.name)

  def GetDeviceCDD(self, device_id):
    """Get device cdd and populate device object with the details.

    Args:
      device_id: string, Cloud Print device id.
    Returns:
      boolean: True = cdd details populated, False = cdd details not populated.
    """
    self.cd.Get(Constants.GCP['SIMULATE'])

    printer_lookup = self.cd.FindID('printer_printerid')
    if not printer_lookup:
      return False
    if not self.cd.SendKeys(device_id, printer_lookup):
      return False
    printer_submit = self.cd.FindID('printer_submit')
    if not self.cd.ClickElement(printer_submit):
      return False
    printer_info = self.cd.FindXPath('html')
    if not printer_info:
      return False
    self.info = printer_info.text
    self.ParseCDD()
    return True

  def ParseCDD(self):
    """Parse the CDD json string into a logical dictionary.

    Returns:
      boolean: True = CDD parsed, False = CDD not parsed.
    """

    cdd = {}
    if self.info:
      cdd = json.loads(self.info)
    else:
      self.logger.warning('Device info is empty.')
      return False
    if 'printers' in cdd:
      for k in cdd['printers'][0]:
        if k == 'capabilities':
          self.cdd['caps'] = {}
        else:
          self.cdd[k] = cdd['printers'][0][k]
    else:
      self.logger.error('Could not find printers in cdd.')
      return False
    for k in cdd['printers'][0]['capabilities']['printer']:
      self.cdd['caps'][k] = cdd['printers'][0]['capabilities']['printer'][k]
    return True

  def CancelRegistration(self):
    """Cancel Privet Registration that is in progress.

    Returns:
      return code from HTTP request.
    """
    cancel_url = self.privet_url['register']['cancel']
    self.logger.debug('Sending request to cancel Privet Registration.')
    response = self.transport.HTTPReq(cancel_url, data='',
                                      headers=self.headers, user=Constants.USER['EMAIL'])
    return response['code']

  def StartPrivetRegister(self):
    """Start a device registration using the Privet protocol.

    Returns:
      boolean: True = success, False = errors.
    """

    self.logger.debug('Registering device %s with Privet', self.ipv4)
    response = self.transport.HTTPReq(
        self.privet_url['register']['start'], data='',
        headers=self.headers, user=Constants.USER['EMAIL'])
    return self.transport.LogData(response)

  def GetPrivetClaimToken(self):
    """Attempt to get a Privet Claim Token.

    Returns:
      boolean: True = success, False = errors.
    """
    self.logger.debug('Getting Privet Claim Token.')
    counter = 0
    max_cycles = 5  # Don't loop more than this number of times.
    while counter < max_cycles:
      response = self.transport.HTTPReq(
          self.privet_url['register']['getClaimToken'], data='',
          headers=self.headers, user=Constants.USER['EMAIL'])
      self.transport.LogData(response)
      if 'token' in response['data']:
        self.claim_token = self.jparser.GetValue(response['data'], key='token')
        self.automated_claim_url = self.jparser.GetValue(
            response['data'], key='automated_claim_url')
        self.claim_url = self.jparser.GetValue(
            response['data'], key='claim_url')
        return True

      if 'error' in response['data']:
        self.logger.warning(response['data'])
        if 'pending_user_action' in response['data']:
          counter += 1
        else:
          return False

    return False  # If here, means unexpected condition, so return False.

  def SendClaimToken(self, auth_token):
    """Send a claim token to the Cloud Print service.

    Args:
      auth_token: string, auth token of user registering printer.
    Returns:
      boolean: True = success, False = errors.
    """
    if not self.claim_token:
      self.logger.error('Error: device does not have claim token.')
      self.logger.error('Cannot send empty token to Cloud Print Service.')
      return False
    if not self.automated_claim_url:
      self.logger.error('Error: expected automated_claim_url.')
      self.logger.error('Aborting SendClaimToken()')
      return False
    response = self.transport.HTTPReq(self.automated_claim_url,
                                      auth_token=auth_token, data='',
                                      user=Constants.USER['EMAIL'])
    self.transport.LogData(response)
    info = self.jparser.Read(response['data'])
    if info['json']:
      if info['success']:
        return True
      else:
        return False
    else:
      return False

  def FinishPrivetRegister(self):
    """Complete printer registration using Privet.

    Returns:
      boolean: True = success, False = errors.
    """

    self.logger.debug('Finishing printer registration.')
    response = self.transport.HTTPReq(
        self.privet_url['register']['complete'], data='',
        headers=self.headers, user=Constants.USER['EMAIL'])
    # Add the device id from the Cloud Print Service.
    info = self.jparser.Read(response['data'])
    if info['json']:
      for k in info:
        if 'device_id' in k:
          self.id = info[k]
          self.logger.debug('Registered with device id: %s', self.id)
    return self.transport.LogData(response)

  def UnRegister(self, auth_token):
    """Remove device from Google Cloud Service.

    Args:
      auth_token: string, auth token of device owner.
    Returns:
      boolean: True = success, False = errors.
    """
    if self.id:
      delete_url = '%s/delete?printerid=%s' % (Constants.AUTH['URL']['GCP'],
                                               self.id)
      response = self.transport.HTTPReq(delete_url, auth_token=auth_token,
                                        data='')
    else:
      self.logger.warning('Cannot delete device, not registered.')
      return False

    result = self.jparser.Validate(response['data'])
    if result:
      self.logger.debug('Successfully deleted printer from service.')
      self.id = None
      return True
    else:
      self.logger.error('Unable to delete printer from service.')
      return False

  def GetPrinterInfo(self, auth_token):
    """Get the printer capabilities stored on the service.

    Args:
      auth_token: string, auth token of device owner.
    Returns:
      boolean: True = success, False = errors.
    """
    if self.id:
      printer_url = '%s/printer?printerid=%s&usecdd=True' % (
          Constants.AUTH['URL']['GCP'], self.id)
      response = self.transport.HTTPReq(printer_url, auth_token=auth_token)
    else:
      self.logger.warning('Cannot get printer info, device not registered.')
      return False

    info = self.jparser.Read(response['data'])
    Extract(info, self.info)
    for k, v in self.info.iteritems():
      self.logger.debug('%s: %s', k, v)
      self.logger.debug('=============================================')
    return True
