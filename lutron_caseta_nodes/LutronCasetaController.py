from polyinterface import Controller,LOG_HANDLER,LOGGER

import asyncio
import json
import requests
import socket
import ssl
import logging
import time
from threading import Thread,Event

#import pylutron_caseta.smartbridge as smartbridge
from pylutron_caseta.smartbridge import Smartbridge

from pylutron_caseta import (FAN_MEDIUM, OCCUPANCY_GROUP_OCCUPIED,
                             OCCUPANCY_GROUP_UNOCCUPIED)

#logging.basicConfig(format='%(asctime)s %(levelname)s:%(message)s', level=logging.DEBUG)
#LOG_HANDLER.set_log_format('%(asctime)s %(threadName)-10s %(name)-18s %(levelname)-8s %(module)s:%(funcName)s: %(message)s')
LOG_HANDLER.set_basic_config(True,logging.DEBUG)

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from urllib.parse import urlencode
from syncer import sync

from lutron_caseta_nodes.LutronCasetaNodes import SerenaHoneycombShade, QsWirelessShade, Scene

# We need an event loop for  pylutron_caseta since we run in a
#  which doesn't have a loop
mainloop = asyncio.get_event_loop()

LOGIN_SERVER = "device-login.lutron.com"
APP_CLIENT_ID = ("e001a4471eb6152b7b3f35e549905fd8589dfcf57eb680b6fb37f20878c"
                 "28e5a")
APP_CLIENT_SECRET = ("b07fee362538d6df3b129dc3026a72d27e1005a3d1e5839eed5ed18"
                     "c63a89b27")
APP_OAUTH_REDIRECT_PAGE = "lutron_app_oauth_redirect"
CERT_SUBJECT = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Pennsylvania"),
    x509.NameAttribute(NameOID.LOCALITY_NAME, "Coopersburg"),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME,
                       "Lutron Electronics Co., Inc."),
    x509.NameAttribute(NameOID.COMMON_NAME, "Lutron Caseta App")
])

BASE_URL = "https://%s/" % LOGIN_SERVER
REDIRECT_URI = "https://%s/%s" % (LOGIN_SERVER, APP_OAUTH_REDIRECT_PAGE)

AUTHORIZE_URL = ("%soauth/authorize?%s" % (BASE_URL,
                                           urlencode({
                                               "client_id": APP_CLIENT_ID,
                                               "redirect_uri": REDIRECT_URI,
                                               "response_type": "code"
                                           })))

class LutronCasetaController(Controller):
    def __init__(self, polyglot):
        super().__init__(polyglot)
        self.name = 'LutronCaseta Controller'
        self.connecting = False
        #self.poly.onConfig(self.process_config)

    def get_priv_key(self):
        LOGGER.info("Getting private key")
        try:
            with open('./caseta.key', 'rb') as f:
                private_key = load_pem_private_key(f.read(), None, default_backend())
            LOGGER.info("Loaded private key from disk")
        except FileNotFoundError:
            LOGGER.info("Generating private key...")
            private_key = rsa.generate_private_key(public_exponent=65537,
                                                   key_size=2048,
                                                   backend=default_backend())
            LOGGER.info("saving private key to disk")
            with open('./caseta.key', 'wb') as f:
                f.write(private_key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.PKCS8,
                        encryption_algorithm=serialization.NoEncryption()
                        ))

        return private_key

    def get_certificate(self):
        LOGGER.info("Getting certificate")
        try:
            with open('./caseta.crt', 'rb') as f:
                certificate = x509.load_pem_x509_certificate(f.read(),
                                                             default_backend())
            LOGGER.info("Loaded cert from disk")
        except FileNotFoundError:
            LOGGER.info("Generating certificate request")
            csr = (x509.CertificateSigningRequestBuilder()
                   .subject_name(CERT_SUBJECT)
                   .sign(self.private_key, hashes.SHA256(), default_backend()))

            if not self.oauth_code:
                LOGGER.error('No OAUTH code stored, exiting')
                return None

            LOGGER.info("requesting token...")
            token = requests.post("%soauth/token" % BASE_URL, data={
                "code": self.oauth_code,
                "client_id": APP_CLIENT_ID,
                "client_secret": APP_CLIENT_SECRET,
                "redirect_uri": REDIRECT_URI,
                "grant_type": "authorization_code"}).json()

            if token["token_type"] != "bearer":
                raise ("Received invalid token %s. Try generating a new code "
                       "(one time use).") % token

            access_token = token["access_token"]

            pairing_request_content = {
                "remote_signs_app_certificate_signing_request":
                csr.public_bytes(serialization.Encoding.PEM).decode('ASCII')
            }

            LOGGER.info("sending pairing request")
            pairing_response = requests.post(
                "%sapi/v1/remotepairing/application/user" % BASE_URL,
                json=pairing_request_content,
                headers={
                    "X-DeviceType": "Caseta,RA2Select",
                    "Authorization": "Bearer %s" % access_token
                }
            ).json()

            app_cert = pairing_response["remote_signs_app_certificate"]
            remote_cert = pairing_response["local_signs_remote_certificate"]

            LOGGER.info("storing certificate to disk")
            with open('caseta.crt', 'wb') as f:
                f.write(app_cert.encode('ASCII'))
                f.write(remote_cert.encode('ASCII'))

            # TODO Don't open new filehandle to read cert back
            LOGGER.info("reading certificate back from disk")
            with open('caseta.crt', 'rb') as f:
                certificate = x509.load_pem_x509_certificate(f.read(),
                                                             default_backend())

        return certificate

    def get_bridge_cert(self, ssl_socket):
        ssl_socket.connect((self.lutron_bridge_ip, 8081))

        ca_der = ssl_socket.getpeercert(True)
        ca_cert = x509.load_der_x509_certificate(ca_der, default_backend())
        with open('./caseta-bridge.crt', 'wb') as f:
            f.write(ca_cert.public_bytes(serialization.Encoding.PEM))
        return True

    def ping_bridge(self, ssl_socket):
        ssl_socket.send(("%s\r\n" % json.dumps({
            "CommuniqueType": "ReadRequest",
            "Header": {"Url": "/server/1/status/ping"}
        })).encode('UTF-8'))

        while True:
            buffer = b''
            while not buffer.endswith(b'\r\n'):
                buffer += ssl_socket.read()

            leap_response = json.loads(buffer.decode('UTF-8'))
            if leap_response['CommuniqueType'] == 'ReadResponse':
                break

        return leap_response['Body']['PingResponse']['LEAPVersion']

    # Did it this way based on "Async Fron Sync" here
    # https://www.aeracode.org/2018/02/19/python-async-simplified/
    async def _bridge_connect(self):
        self.sb = Smartbridge.create_tls(hostname=self.lutron_bridge_ip,
                                         keyfile='./caseta.key',
                                         certfile='./caseta.crt',
                                         ca_certs='caseta-bridge.crt',
                                         )
        await self.sb.connect()
        if self.sb.is_connected():
            LOGGER.info("Successfully connected to bridge!")
        else:
            LOGGER.error("Could not connect to bridge")
        self.connecting = False

    def bridge_connect(self):
        self.connecting = True
        self.connect_thread = Thread(target=mainloop.run_forever)
        self.connect_thread.start()
        #mainloop.call_soon_threadsafe(self._bridge_connect)
        #mainloop.run_until_complete(self._bridge_connect)
        asyncio.run_coroutine_threadsafe(self._bridge_connect(), mainloop)

    def is_connected(self):
        i = 0 # 2 minutes
        while self.connecting and i < 24:
            LOGGER.info("Waiting for connection to initialize...")
            time.sleep(5)
            i += 1
        if self.connecting:
            LOGGER.error("Timed out waiting for connectiont to startup.")
            return False
        if not self.sb.is_connected():
            LOGGER.info("Not connected to bridge, reconnecting...")
            self.bridge_connect()
        return self.sb.is_connected()

    def start(self):
        LOGGER.info('Started LutronCaseta NodeServer')
        self.poly.add_custom_config_docs("<b>To obtain oauth code, follow <a href='{}' target='_blank'>this link</a> and copy the 'code' portion of the error page url</b>".format(AUTHORIZE_URL))
        # This grabs the server.json data and checks profile_version is up to date
        serverdata = self.poly.get_server_data(check_profile=True)
        self.setDriver('ST', 1)
        LOGGER.info('Started Lutron Caseta NodeServer {}'.format(serverdata['version']))
        self.mainloop = mainloop
        asyncio.set_event_loop(mainloop)
        self.hb = 0
        self.devices = dict()
        self.heartbeat()
        self.check_params()
        if not self.lutron_bridge_ip and not self.oauth_code:
            return

        # Get or generate private key
        self.private_key = self.get_priv_key()
        LOGGER.info("Private key loaded")
        # get or generate certifiate
        self.certificate = self.get_certificate()
        LOGGER.info("Certificate loaded")
        # Create an ssl socket to smartbridge
        raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ssl_socket = ssl.wrap_socket(raw_socket, keyfile='./caseta.key',
                                     certfile='./caseta.crt',
                                     ssl_version=ssl.PROTOCOL_TLSv1_2)
        # User socket to get smartbridge certificate
        if self.get_bridge_cert(ssl_socket):
            LOGGER.info("Bridge certificate saved")
            self.bridge_connect()

        ssl_socket.close()
        self.discover()

    def shortPoll(self):
        """
        Optional.
        This runs every 10 seconds. You would probably update your nodes either here
        or longPoll. No need to Super this method the parent version does nothing.
        The timer can be overriden in the server.json.
        """
        #LOGGER.debug("shoftPoll")
        # Call update  to update the status
        # No longer needed since we get callback's from the brdge :)
        #self.update()

    def longPoll(self):
        """
        Optional.
        This runs every 30 seconds. You would probably update your nodes either here
        or shortPoll. No need to Super this method the parent version does nothing.
        The timer can be overriden in the server.json.
        """
        self.heartbeat()

    def query(self):
        """
        Optional.
        By default a query to the control node reports the FULL driver set for ALL
        nodes back to ISY. If you override this method you will need to Super or
        issue a reportDrivers() to each node manually.
        """
        self.check_params()
        for node in self.nodes:
            if node != self.address:
                self.nodes[node].query()

    def update(self):
        for node in self.nodes:
            if node != self.address:
                self.nodes[node].update()

    def heartbeat(self):
        """
        Optional.
        Sends a DON/DOF called by start and longPoll which allow users to
        monitor that the nodeserver is still running
        """
        #LOGGER.debug("heartbeat: {}".format(self.hb))
        if self.hb == 0:
            self.reportCmd("DON",2)
            self.hb = 1
        else:
            self.reportCmd("DOF",2)
            self.hb = 0

    def discover(self, *args, **kwargs):
        """
        Example
        Do discovery here. Does not have to be called discovery. Called from example
        controller start method and from DISCOVER command recieved from ISY as an exmaple.
        """
        if not self.is_connected():
            return False

        # self.addNode(LutronCasetaSmartBridge(self, self.address, 'smartbridgeaddr', 'Caseta Smart Bridge'))
        devices = self.sb.get_devices()
        scenes = self.sb.get_scenes()

        for device_id, device in devices.items():
            """
            '1': {'device_id': '1', 'name': 'Smart Bridge 2', 'type': 'SmartBridge', 'zone': None, 'current_state': -1},
            '3': {'device_id': '3', 'name': 'Living Room_Left Window', 'type': 'SerenaHoneycombShade', 'zone': '2', 'current_state': -1}
            """
            NodeType = None
            if device.get('type') == "SerenaHoneycombShade":
                NodeType = SerenaHoneycombShade
            elif device.get('type') == "QsWirelessShade":
                NodeType = QsWirelessShade
            if not NodeType:
                LOGGER.error("Unsupported Node Type: {}".format(device))
                continue

            address = 'device' + str(device.get('device_id'))
            LOGGER.info("Adding node: '{}' {}".format(device.get('name'),address))
            self.devices[device_id] = self.addNode(
                NodeType(
                    self,
                    self.address,
                    address,
                    device.get('name'),
                    self.sb,
                    device.get('device_id'),
                    device.get('type'),
                    device.get('zone'),
                    device.get('current_state')
                )
            )

        for scene_id, scene in scenes.items():
            """
            '1': {'scene_id': '1', 'name': 'All Close'},
            '2': {'scene_id': '2', 'name': 'All Open'},
            '3': {'scene_id': '3', 'name': 'All Halfway'}
            """
            self.addNode(
                Scene(
                    self,
                    self.address,
                    'scene' + str(scene.get('scene_id')),
                    scene.get('name'),
                    self.sb
                )
            )

    def delete(self):
        """
        Example
        This is sent by Polyglot upon deletion of the NodeServer. If the process is
        co-resident and controlled by Polyglot, it will be terminiated within 5 seconds
        of receiving this message.
        """
        LOGGER.info('Oh God I\'m being deleted. Nooooooooooooooooooooooooooooooooooooooooo.')

    def stop(self):
        LOGGER.debug('NodeServer stopped.')

    def process_config(self, config):
        # this seems to get called twice for every change, why?
        # What does config represent?
        LOGGER.info("process_config: Enter config={}".format(config))
        LOGGER.info("process_config: Exit")

    def check_params(self):
        default_lutron_bridge_ip = None
        default_oauth_code = None
        if 'lutron_bridge_ip' in self.polyConfig['customParams']:
            self.lutron_bridge_ip = self.polyConfig['customParams']['lutron_bridge_ip']
        else:
            self.lutron_bridge_ip = default_lutron_bridge_ip
            LOGGER.error('check_params: lutron_bridge_ip not defined in customParams, please add it.  Using {}'.format(self.lutron_bridge_ip))
            st = False

        if 'oauth_code' in self.polyConfig['customParams']:
            self.oauth_code = self.polyConfig['customParams']['oauth_code']
        else:
            self.oauth_code = default_oauth_code
            LOGGER.error('check_params: oauth_code not defined.')
            st = False

        # Make sure they are in the params
        self.addCustomParam({'oauth_code': self.oauth_code, 'lutron_bridge_ip': self.lutron_bridge_ip})

        # Add a notice if they need to change the user/password from the default.
        if self.lutron_bridge_ip == default_lutron_bridge_ip or self.oauth_code == default_oauth_code:
            # This doesn't pass a key to test the old way.
            self.addNotice('Please set proper lutron_bridge_ip and oauth_code in configuration page, and restart this nodeserver', 'addconfig')
        else:
            self.removeNotice('addconfig')

    def remove_notice_test(self,command):
        LOGGER.info('remove_notice_test: notices={}'.format(self.poly.config['notices']))
        # Remove all existing notices
        self.removeNotice('test')

    def remove_notices_all(self,command):
        LOGGER.info('remove_notices_all: notices={}'.format(self.poly.config['notices']))
        # Remove all existing notices
        self.removeNoticesAll()

    def update_profile(self,command):
        LOGGER.info('update_profile:')
        st = self.poly.installprofile()
        return st

    """
    Optional.
    Since the controller is the parent node in ISY, it will actual show up as a node.
    So it needs to know the drivers and what id it will use. The drivers are
    the defaults in the parent Class, so you don't need them unless you want to add to
    them. The ST and GV1 variables are for reporting status through Polyglot to ISY,
    DO NOT remove them. UOM 2 is boolean.
    The id must match the nodeDef id="controller"
    In the nodedefs.xml
    """
    id = 'controller'
    commands = {
        'QUERY': query,
        'DISCOVER': discover,
        'UPDATE_PROFILE': update_profile,
        'REMOVE_NOTICES_ALL': remove_notices_all,
        'REMOVE_NOTICE_TEST': remove_notice_test
    }
    drivers = [{'driver': 'ST', 'value': 1, 'uom': 2}]
