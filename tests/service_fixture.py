"""Minimal production-role fixture; no legacy portal or federation task is started."""
import configparser
import json
from pathlib import Path
import ssl
import tempfile

import asyncssh
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer

from tunnel.native_relay import RelayServer
from tunnel.provisioning import initialize
from tunnel.service_portal import RelayPortal
from main import account_portal


def create_identity(relay, directory, settings):
    initialize(directory, '127.0.0.1', 3333)
    config = configparser.ConfigParser(interpolation=None)
    config.read(directory/'server.conf', encoding='utf-8')
    config['identity'] = settings
    portal, fed = account_portal(relay, config, directory, directory/'node-state.json')
    portal.state['registration_open'] = True
    return portal, fed


class ServiceFixture:
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        (self.directory/'admin-token').write_text('a'*64)
        self.settings = dict(origin='https://127.0.0.1:8443', ca_file='web-cert.pem',
                             signing_public_key='00'*32, admin_token_file='admin-token')
        key = asyncssh.generate_private_key('ssh-ed25519')
        self.relay = RelayServer('127.0.0.1', 0, host_key=key, token='x'*48, workers=1)
        self.addAsyncCleanup(self.relay.close)
        self.authority, self.fed = create_identity(self.relay, self.directory, self.settings)
        self.authority.account_api.mail.configure(dict(required=False))
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(self.directory/'web-cert.pem', self.directory/'web-key.pem')
        self.server = TestServer(self.authority.app)
        await self.server.start_server(ssl=self.context)
        self.addAsyncCleanup(self.authority.close)
        self.addAsyncCleanup(self.server.close)
        from aiohttp import TCPConnector
        self.client = TestClient(self.server, connector=TCPConnector(ssl=False),
                                 cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def node_surface(self):
        return RelayPortal(self.relay, self.fed.key, self.directory/'node-state.json',
            dict(self.settings, credential_file='admin-token', endpoint='127.0.0.1:3333',
                 proof_endpoint='127.0.0.1:18443'), self.directory)
