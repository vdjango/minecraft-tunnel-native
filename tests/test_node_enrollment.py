"""Actual TLS onboarding, trust failures, payload bounds and identity preservation."""
import configparser
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from aiohttp import ClientError, web
from aiohttp.test_utils import TestServer

from tests.service_fixture import ServiceFixture
from tunnel.enrollment import fetch_profile, join
from tunnel.managed_control import control_origin


class NodeEnrollmentTests(ServiceFixture, unittest.IsolatedAsyncioTestCase):
    async def profile_server(self, response=None, status=200):
        async def handle(request):
            self.assertEqual(await request.json(), {})
            self.assertNotIn('Authorization', request.headers)
            return web.Response(body=response or json.dumps(dict(protocol=1,
                signing_public_key='11'*32, credential='public-enrollment-only-'+'x'*40)).encode(),
                status=status, headers={'Location': str(self.server.make_url('/'))} if status == 302 else {})
        app = web.Application()
        app.router.add_post('/v1/enrollment/profile', handle)
        server = TestServer(app)
        await server.start_server(ssl=self.context)
        self.addAsyncCleanup(server.close)
        return str(server.make_url('/')).rstrip('/')

    async def test_private_control_join_creates_only_managed_state_and_preserves_identity(self):
        origin = await self.profile_server()
        target = self.directory/'joined'
        await join(target, origin, '127.0.0.1', ca_file=self.directory/'web-cert.pem', port=5555)
        config = configparser.ConfigParser(interpolation=None)
        config.read(target/'server.conf', encoding='utf-8')
        self.assertEqual(config['control']['origin'], origin)
        self.assertEqual(config['control']['proof_endpoint'], '127.0.0.1:18443')
        self.assertEqual(config['relay']['compression'], 'off')
        self.assertNotIn('admin_hash', (target/'node-state.json').read_text())
        self.assertFalse((target/'admin-credentials.txt').exists())
        key = (target/'relay_host_key').read_bytes()
        with self.assertRaises(ValueError):
            await join(target, origin, '127.0.0.1', ca_file=self.directory/'web-cert.pem')
        self.assertEqual(key, (target/'relay_host_key').read_bytes())

    async def test_untrusted_tls_redirect_and_oversize_never_write_configuration(self):
        origin = await self.profile_server()
        with self.assertRaises(ClientError):
            await join(self.directory/'untrusted', origin, '127.0.0.1')
        self.assertFalse((self.directory/'untrusted').exists())
        for response, status in ((b'{}', 302), (b'x'*4097, 200),
                                 (b'{"protocol":true,"signing_public_key":"x","credential":"x"}', 200)):
            origin = await self.profile_server(response, status)
            with self.assertRaises(ValueError):
                await fetch_profile(origin, self.directory/'web-cert.pem')

    async def test_numeric_ipv6_endpoint_and_early_validation(self):
        origin = await self.profile_server()
        await join(self.directory/'ipv6', origin, '2001:db8::5', ca_file=self.directory/'web-cert.pem')
        config = configparser.ConfigParser(interpolation=None)
        config.read(self.directory/'ipv6/server.conf', encoding='utf-8')
        self.assertEqual(config['control']['endpoint'], '[2001:db8::5]:3333')
        self.assertEqual(config['web']['host'], '::')
        for host in ('relay.example.org', '0.0.0.0', '::', 'ff02::1', '::ffff:127.0.0.1'):
            with self.assertRaises(ValueError):
                await join(self.directory/'invalid', origin, host)
        for origin in ('http://example.org', 'https://user:password@example.org', 'https://example.org/path',
                       'https://example.org:99999', 'https://example.org/?token=secret'):
            with self.assertRaises(ValueError):
                control_origin(origin)
