# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

# TODO Implement VCT iot

import configparser
import contextlib
import datetime
from enum import Enum
from functools import cache
from importlib import util
import io
import json
import logging
import netifaces
from OpenSSL import crypto
import os
from pathlib import Path
import platform
import requests
import secrets
import socket
import subprocess
from urllib.parse import parse_qs
import urllib3
from threading import Thread, Lock
import time
import zipfile

from odoo import http, release, service
from odoo.tools.func import lazy_property
from odoo.tools.misc import file_path
from odoo.modules.module import get_resource_path
from odoo import _

lock = Lock()
_logger = logging.getLogger(__name__)

try:
    import crypt
except ImportError:
    _logger.warning('Could not import library crypt')


class CertificateStatus(Enum):
    OK = 1
    NEED_REFRESH = 2
    ERROR = 3


class IoTRestart(Thread):
    """
    Thread to restart odoo server in IoT Box when we must return a answer before
    """
    def __init__(self, delay):
        Thread.__init__(self)
        self.delay = delay

    def run(self):
        time.sleep(self.delay)
        service.server.restart()


writable = contextlib.nullcontext

def access_point():
    return get_ip() == '10.11.12.1'

def start_nginx_server():
        _logger.info('Restarting Nginx server')
        subprocess.check_call(["/sbin/service", "nginx","restart"])
        _logger.info('Restarted Nginx server')

def check_certificate():
    """
    Check if the current certificate is up to date or not authenticated
    :return CheckCertificateStatus
    """
    #server = get_odoo_server_url()

    #if not server:
    #    return {"status": CertificateStatus.ERROR,
    #            "error_code": "ERR_IOT_HTTPS_CHECK_NO_SERVER"}

 
    path = Path('/etc/certs/fullchain.pem')

    if not path.exists():
        return {"status": CertificateStatus.NEED_REFRESH}

    try:
        with path.open('r') as f:
            cert = crypto.load_certificate(crypto.FILETYPE_PEM, f.read())
    except EnvironmentError:
        _logger.exception("Unable to read certificate file")
        return {"status": CertificateStatus.ERROR,
                "error_code": "ERR_IOT_HTTPS_CHECK_CERT_READ_EXCEPTION"}

    cert_end_date = datetime.datetime.strptime(cert.get_notAfter().decode('utf-8'), "%Y%m%d%H%M%SZ") - datetime.timedelta(days=10)
    for key in cert.get_subject().get_components():
        if key[0] == b'CN':
            cn = key[1].decode('utf-8')
    if cn == 'OdooTempIoTBoxCertificate' or datetime.datetime.now() > cert_end_date:
        message = _('Your certificate %s must be updated') % (cn)
        _logger.info(message)
        return {"status": CertificateStatus.NEED_REFRESH}
    else:
        message = ('Your certificate %s is valid until %s') % (cn, cert_end_date)
        _logger.info(message)
        return {"status": CertificateStatus.OK, "message": message}


def check_git_branch():
    """
    Check if the local branch is the same than the connected Odoo DB and
    checkout to match it if needed.
    """
    _logger.info(f'check git branch ....disabled')

def check_image():
    """
    Check if the current image of IoT Box is up to date
    """
    #if valueActual == valueLastest:
    return False
    #return {'major': version[0], 'minor': version[1]}

def save_conf_server(url, token, db_uuid ):
    """
    Save server configurations in odoo.conf
    :param url: The URL of the server
    :param token: The token to authenticate the server
    :param db_uuid: The database UUID
    :param enterprise_code: The enterprise code
    """
    update_conf({
        'remote_server': url,
        'token': token,
        'db_uuid': db_uuid
    })

def generate_password():
    """
    Generate an unique code to secure raspberry pi
    """
    alphabet = 'abcdefghijkmnpqrstuvwxyz23456789'
    password = ''.join(secrets.choice(alphabet) for i in range(12))
    shadow_password = crypt.crypt(password, crypt.mksalt())
    #subprocess.call(('usermod', '-p', shadow_password, 'pi'))
    return password


def get_certificate_status(is_first=True):
    """
    Will get the HTTPS certificate details if present. Will load the certificate if missing.

    :param is_first: Use to make sure that the recursion happens only once
    :return: (bool, str)
    """
    check_certificate_result = check_certificate()
    certificateStatus = check_certificate_result["status"]

    if certificateStatus == CertificateStatus.ERROR:
        return False, check_certificate_result["error_code"]

    if certificateStatus == CertificateStatus.NEED_REFRESH and is_first:
        certificate_process = load_certificate()
        if certificate_process is not True:
            return False, certificate_process
        return get_certificate_status(is_first=False)  # recursive call to attempt certificate read
    return True, check_certificate_result.get("message",
                                              "The HTTPS certificate was generated correctly")

def get_img_name():
    major, minor = get_version().split('.')
    return 'iotboxv%s_%s.zip' % (major, minor)

def get_ip():
    interfaces = netifaces.interfaces()
    for interface in interfaces:
        if netifaces.ifaddresses(interface).get(netifaces.AF_INET):
            addr = netifaces.ifaddresses(interface).get(netifaces.AF_INET)[0]['addr']
            if addr != '127.0.0.1':
                return addr

def get_mac_address():
    interfaces = netifaces.interfaces()
    for interface in interfaces:
        if netifaces.ifaddresses(interface).get(netifaces.AF_INET):
            addr = netifaces.ifaddresses(interface).get(netifaces.AF_LINK)[0]['addr']
            if addr != '00:00:00:00:00:00':
                return addr

def get_path_nginx():
    return str(list(Path().absolute().parent.glob('*nginx*'))[0])

def get_ssid():
    return

@cache
def get_odoo_server_url():
    """Get the URL of the linked Odoo database.
    If the IoT Box is in access point mode, it will return ``None`` to avoid
    connecting to the server.

    :return: The URL of the linked Odoo database.
    :rtype: str or None
    """
    return get_conf('remote_server')

def get_token():
    """:return: The token to authenticate the server"""
    return get_conf('token')

def lines_that_start_with(string, fp):
    return [line for line in fp if line.startswith(string)]

def read_file_line_match(file_name,match_string):
    with open(file_name, "r") as fp:
        for line in lines_that_start_with(match_string, fp):
            return line

def get_pairing_code():
    return read_file_first_line('/etc/vct-iot/PAIRING_CODE')

def get_iotdomain_code():
    return read_file_first_line('/etc/vct-iot/IOT_DOMAIN')

def get_version(detailed_version=False):
    if not detailed_version:
        return read_file_first_line('iotbox_version')
    else:
        box_version="???"
        pretty_name=read_file_line_match("/etc/os-release",'PRETTY_NAME')
        if pretty_name:
            _logger.debug(f'Pretty: {pretty_name}')
            box_version=pretty_name.split('"')[1]
        version=read_file_first_line('iotbox_version')
        return f"{box_version} - {version}"

def get_wifi_essid():
    wifi_options = []
    #process_iwlist = subprocess.Popen(['sudo', 'iwlist', 'wlan0', 'scan'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    #process_grep = subprocess.Popen(['grep', 'ESSID:"'], stdin=process_iwlist.stdout, stdout=subprocess.PIPE).stdout.readlines()
    #for ssid in process_grep:
    #    essid = ssid.decode('utf-8').split('"')[1]
    #    if essid not in wifi_options:
    #        wifi_options.append(essid)
    return wifi_options

def load_certificate():
    """
    Send a request to Odoo with customer db_uuid and enterprise_code to get a true certificate
    """
    _logger.info("getting certificates for ssl is handled by the system.")
    return True


def load_iot_handlers():
    """
    This method loads local files: 'odoo/addons/hw_drivers/iot_handlers/drivers' and
    'odoo/addons/hw_drivers/iot_handlers/interfaces'
    And execute these python drivers and interfaces
    """
    for directory in ['interfaces', 'drivers']:
        path = get_resource_path('hw_drivers', 'iot_handlers', directory)
        filesList = list_file_by_os(path)
        for file in filesList:
            spec = util.spec_from_file_location(file, str(Path(path).joinpath(file)))
            if spec:
                module = util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(module)
                except Exception as e:
                    _logger.error('Unable to load file: %s ', file)
                    _logger.error('An error encountered : %s ', e)
    lazy_property.reset_all(http.root)


def compute_iot_handlers_addon_name(handler_kind, handler_file_name):
    return "odoo.addons.hw_drivers.iot_handlers.{handler_kind}.{handler_name}".\
        format(handler_kind=handler_kind, handler_name=handler_file_name.removesuffix('.py'))

def load_iot_handlers():
    """
    This method loads local files: 'odoo/addons/hw_drivers/iot_handlers/drivers' and
    'odoo/addons/hw_drivers/iot_handlers/interfaces'
    And execute these python drivers and interfaces
    """
    for directory in ['interfaces', 'drivers']:
        path = file_path(f'hw_drivers/iot_handlers/{directory}')
        filesList = list_file_by_os(path)
        for file in filesList:
            spec = util.spec_from_file_location(compute_iot_handlers_addon_name(directory, file), str(Path(path).joinpath(file)))
            if spec:
                module = util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    #_logger.exception('Unable to load handler file: %s', file)
                    _logger.error('Unable to load file: %s ', file)
    lazy_property.reset_all(http.root)

def list_file_by_os(file_list):
    return [x.name for x in Path(file_list).glob('*[!W].*')]



def odoo_restart(delay=0):
    """
    Restart Odoo service
    :param delay: Delay in seconds before restarting the service (Default: 0)
    """
    IR = IoTRestart(delay)
    IR.start()


def path_file(filename):
    platform_os = platform.system()
    return Path.home() / filename

def read_file_first_line(filename):
    path = path_file(filename)
    if path.exists():
        with path.open('r') as f:
            return f.readline().strip('\n')

def unlink_file(filename):
    path = path_file(filename)
    _logger.info('Deleting file : %s ', path)
    if path.exists():
        path.unlink()
    else:
        _logger.warning('File : %s does not exists', path)

def write_file(filename, text, mode='w'):
    """
    This function writes 'text' to 'filename' file for classic files.
    :param filename: The name of the file to write to.
    :param text: The text to write to the file, OR the ConfigParser object to write to the file.
    :param mode: The mode to open the file in (Default: 'w').
    """
    path = path_file(filename)
    with open(path, mode) as f:
        if path.suffix == '.conf' and isinstance(text, configparser.ConfigParser):
            # As we are dealing with conf files, :filename: is a Path object, as it was created by path_file for
            # configparser. More, :text: is assumed to be a ConfigParser object.
            text.write(f)
        else:
            f.write(text)

def download_from_url(download_url, path_to_filename):
    """
    This function downloads from its 'download_url' argument and
    saves the result in 'path_to_filename' file
    The 'path_to_filename' needs to be a valid path + file name
    (Example: 'C:\\Program Files\\Odoo\\downloaded_file.zip')
    """
    try:
        request_response = requests.get(download_url, timeout=60)
        request_response.raise_for_status()
        write_file(path_to_filename, request_response.content, 'wb')
        _logger.info('Downloaded %s from %s', path_to_filename, download_url)
    except Exception:
        _logger.exception('Failed to download from %s', download_url)

def unzip_file(path_to_filename, path_to_extract):
    """
    This function unzips 'path_to_filename' argument to
    the path specified by 'path_to_extract' argument
    and deletes the originally used .zip file
    Example: unzip_file('C:\\Program Files\\Odoo\\downloaded_file.zip', 'C:\\Program Files\\Odoo\\new_folder'))
    Will extract all the contents of 'downloaded_file.zip' to the 'new_folder' location)
    """
    try:
        with writable():
            path = path_file(path_to_filename)
            with zipfile.ZipFile(path) as zip_file:
                zip_file.extractall(path_file(path_to_extract))
            Path(path).unlink()
        _logger.info('Unzipped %s to %s', path_to_filename, path_to_extract)
    except Exception:
        _logger.exception('Failed to unzip %s', path_to_filename)


@cache
def get_hostname():
    """Cache the hostname to avoid multiple calls to socket.gethostname()"""
    return socket.gethostname()


def update_conf(values, section='iot.box'):
    """
    Update odoo.conf with the given key and value.
    :param values: The dictionary of key-value pairs to update the config with.
    :param section: The section to update the key-value pairs in (Default: iot.box).
    """
    _logger.debug("Updating odoo.conf with values: %s", values)
    conf = get_conf()
    get_conf.cache_clear()  # Clear the cache to get the updated config

    if not conf.has_section(section):
        _logger.debug("Creating new section '%s' in odoo.conf", section)
        conf.add_section(section)

    for key, value in values.items():
        conf.set(section, key, value) if value else conf.remove_option(section, key)

    write_file("odoo.conf", conf)


@cache
def get_conf(key=None, section='iot.box'):
    """
    Get the value of the given key from odoo.conf, or the full config if no key is provided.
    :param key: The key to get the value of.
    :param section: The section to get the key from (Default: iot.box).
    :return: The value of the key provided or None if it doesn't exist, or full conf object if no key is provided.
    """
    conf = configparser.ConfigParser()
    conf.read(path_file("odoo.conf"))

    return conf.get(section, key, fallback=None) if key else conf  # Return the key's value or the configparser object


def disconnect_from_server():
    """Disconnect the IoT Box from the server, clears associated caches"""
    get_odoo_server_url.cache_clear()
    update_conf({
        'remote_server': '',
        'token': '',
        'db_uuid': ''
    })


def url_is_valid(url):
    """
    Checks whether the provided url is a valid one or not
    :param url: the URL to check
    :return: boolean indicating if the URL is valid.
    """
    try:
        result = urllib3.util.parse_url(url.strip())
        return all([result.scheme in ["http", "https"], result.netloc, result.host != 'localhost'])
    except urllib3.exceptions.LocationParseError:
        return False


def parse_url(url):
    """
    Parses URL params and returns them as a dictionary starting by the url.

    Does not allow multiple params with the same name (e.g. <url>?a=1&a=2 will return the same as <url>?a=1)
    :param url: the URL to parse
    :return: the dictionary containing the URL and params
    """
    if not url_is_valid(url):
        raise ValueError("Invalid URL provided.")

    url = urllib3.util.parse_url(url.strip())
    search_params = {
        key: value[0]
        for key, value in parse_qs(url.query, keep_blank_values=True).items()
    }
    return {
        "url": f"{url.scheme}://{url.netloc}",
        **search_params,
    }


def reset_log_level():
    """Reset the log level to the default one if the reset timestamp is reached
    This timestamp is set by the log controller in `hw_posbox_homepage/homepage.py` when the log level is changed
    """
    log_level_reset_timestamp = get_conf('log_level_reset_timestamp')
    if log_level_reset_timestamp and float(log_level_reset_timestamp) <= time.time():
        _logger.info("Resetting log level to default.")
        update_conf({
            'log_level_reset_timestamp': '',
            'log_handler': ':WARNING',
            'log_level': 'warn',
        })
