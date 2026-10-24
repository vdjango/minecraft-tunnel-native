"""Opaque launcher profile codes and explicitly trusted CDN proxy headers."""
import base64
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
from tunnel.portal import Portal, _trusted_proxy_ranges


class ProfileExchangeTests(unittest.IsolatedAsyncioTestCase):
    def make_portal(self):
        portal = Portal.__new__(Portal)
        portal.state = {'public_join': True, 'issued_profiles': {}, 'trusted_proxy_cidrs': []}
        portal.federation = SimpleNamespace(
            profile={'server_host': '203.0.113.15', 'server_port': 54640,
                     'token': 't' * 48, 'server_public_key': 'ssh-ed25519 test-key'},
            get_fingerprint=lambda: 'SHA256:test',
            key=SimpleNamespace(get_fingerprint=lambda: 'SHA256:test'))

        class Accounts:
            def session(self, request):
                raise web.HTTPUnauthorized()

        portal.account_api = Accounts()
        portal.save = Mock()
        portal.throttle = Mock()
        return portal

    def request(self, *, remote='192.0.2.20', scheme='https', host='node.example', headers=None, data=None):
        async def read_json():
            return data or {}
        return SimpleNamespace(cookies={}, remote=remote, scheme=scheme, host=host,
                               headers=headers or {}, json=read_json)

    async def test_issued_code_is_opaque_and_redeems_saved_profile(self):
        portal = self.make_portal()
        response = await portal.profile(self.request(data={'format': 2}))
        result = json.loads(response.text)
        self.assertTrue(result['code'].startswith('buggnode2-'))
        self.assertNotIn('203.0.113.15', result['code'])
        self.assertNotIn('profile', result)
        encoded_issuer, reference = result['code'][10:].split('.', 1)
        issuer = base64.urlsafe_b64decode(encoded_issuer + '=' * (-len(encoded_issuer) % 4)).decode()
        self.assertEqual(issuer, 'https://node.example')
        self.assertEqual(len(reference), 43)
        redeemed = await portal.profile_redeem(self.request(data={'reference': reference}))
        self.assertEqual(json.loads(redeemed.text)['profile'], portal.federation.profile)
        self.assertEqual(redeemed.headers['Cache-Control'], 'no-store')

    async def test_expired_and_malformed_references_are_rejected(self):
        portal = self.make_portal()
        portal.state['issued_profiles']['A' * 43] = {
            'expires': time.time() - 1, 'profile': portal.federation.profile}
        with self.assertRaises(web.HTTPNotFound):
            await portal.profile_redeem(self.request(data={'reference': 'A' * 43}))
        with self.assertRaises(web.HTTPBadRequest):
            await portal.profile_redeem(self.request(data={'reference': '../private'}))

    async def test_legacy_default_keeps_existing_launchers_compatible(self):
        portal = self.make_portal()
        result = json.loads((await portal.profile(self.request())).text)
        self.assertTrue(result['code'].startswith('buggnode1-'))
        self.assertEqual(result['profile'], portal.federation.profile)
        self.assertEqual(portal.state['issued_profiles'], {})

    async def test_invalid_saved_expiry_cannot_redeem(self):
        portal = self.make_portal()
        portal.state['issued_profiles']['A' * 43] = {'expires':'invalid','profile':portal.federation.profile}
        with self.assertRaises(web.HTTPNotFound):
            await portal.profile_redeem(self.request(data={'reference':'A' * 43}))

    async def test_forwarded_headers_only_apply_to_configured_proxy_ranges(self):
        portal = self.make_portal()
        portal.state['trusted_proxy_cidrs'] = ['192.0.2.0/24']
        trusted = self.request(remote='192.0.2.42', scheme='http',
            headers={'X-Forwarded-Proto': 'https', 'CF-Connecting-IP': '198.51.100.9'})
        self.assertEqual(portal.external_scheme(trusted), 'https')
        self.assertEqual(portal.client_address(trusted), '198.51.100.9')
        untrusted = self.request(remote='203.0.113.9', scheme='http',
            headers={'X-Forwarded-Proto': 'https', 'CF-Connecting-IP': '198.51.100.9'})
        self.assertEqual(portal.external_scheme(untrusted), 'http')
        self.assertEqual(portal.client_address(untrusted), '203.0.113.9')

    async def test_settings_without_proxy_field_preserve_saved_ranges(self):
        portal = self.make_portal()
        portal.state.update(trusted_proxy_cidrs=['192.0.2.0/24'], capacity={})
        portal.session = Mock()
        portal.federation.region = ''
        portal.federation.routing_sample = None
        portal.federation.name = 'Node'
        portal.relay = SimpleNamespace(
            capacity=SimpleNamespace(policy={}, configure=Mock()), draining=False)
        portal.save = Mock()
        data = {'name': 'Node', 'public_join': True, 'draining': False}

        async def read_json():
            return data

        await portal.settings(SimpleNamespace(json=read_json))
        self.assertEqual(portal.state['trusted_proxy_cidrs'], ['192.0.2.0/24'])

    def test_legacy_single_proxy_addresses_migrate_to_host_ranges(self):
        self.assertEqual(_trusted_proxy_ranges({'trusted_proxy_ips': ['192.0.2.42', '2001:db8::1']}),
                         ['192.0.2.42/32', '2001:db8::1/128'])
        self.assertEqual(_trusted_proxy_ranges({'trusted_proxy_cidrs': ['0.0.0.0/0', 'bad']}), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
