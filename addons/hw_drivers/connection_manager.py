# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.


# TODO Connection!! implement VCT iot => Done

from datetime import datetime, timedelta
import logging
import requests
from threading import Thread
import time
import urllib3

from odoo.addons.hw_drivers.main import iot_devices, manager
from odoo.addons.hw_drivers.tools import helpers

_logger = logging.getLogger(__name__)


class ConnectionManager(Thread):
    def __init__(self):
        super(ConnectionManager, self).__init__()
        self.pairing_code = False
        self.pairing_uuid = False

    def run(self):
        if not helpers.get_odoo_server_url() and not helpers.access_point():
            end_time = datetime.now() + timedelta(minutes=5)
            self.pairing_code = helpers.get_pairing_code()
            _logger.info(f'Pairing code {self.pairing_code}') 
            self._refresh_displays()
            while datetime.now() < end_time:
                self._connect_box()
                time.sleep(10)
            self.pairing_code = False
            self.pairing_uuid = False
            self._refresh_displays()

    def _connect_box(self):
        headers={}
        headers['x-vct-pairing-code'] = self.pairing_code
        url= "https://www.v-consulting.biz/vct_iot_subscription/pairing" 
        try:
            urllib3.disable_warnings() 
            req = requests.get( url, headers=headers)
            result = req.json()
            if all(key in result for key in ['odoo_url', 'odoo_token', 'odoo_uui', 'pairing_code']):
                self._connect_to_server(result['odoo_url'], result['odoo_token'], result['odoo_uui'])
        except Exception as e:
            _logger.error('Could not reach "www.v-consulting.biz/vct_iot_subscription"')
            _logger.error('A error encountered : %s ' % e)

    def _connect_to_server(self, url, token, db_uuid):
        # Save DB URL and token

        helpers.save_conf_server(url, token, db_uuid )        # Notify the DB, so that the kanban view already shows the IoT Box
        manager.send_alldevices()
        # Restart to checkout the git branch, get a certificate, load the IoT handlers...
        helpers.odoo_restart(2)

    def _refresh_displays(self):
        """Refresh all displays to hide the pairing code"""
        for d in iot_devices:
            if iot_devices[d].device_type == 'display':
                iot_devices[d].action({
                    'action': 'display_refresh'
                })


connection_manager = ConnectionManager()
connection_manager.daemon = True
connection_manager.start()
