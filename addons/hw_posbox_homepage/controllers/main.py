# Part of Odoo. See LICENSE file for full copyright and licensing details.

import logging
import threading

from odoo import http
from odoo.http import Response
from odoo.addons.hw_drivers.tools import helpers
from odoo.addons.web.controllers.home import Home

_logger = logging.getLogger(__name__)


class IoTboxHomepage(Home):
    def __init__(self):
        super(IoTboxHomepage,self).__init__()
        self.updating = threading.Lock()

    @http.route('/hw_proxy/get_version', type='http', auth='none')
    def check_version(self):
        return helpers.get_version()

