"""Web pairing does not need SSH configuration or a node restart."""
import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, patch

from tunnel.content_cache_admin import ContentCacheAdmin, invitation
from tunnel.content_cache import grant


class PairingTests(unittest.IsolatedAsyncioTestCase):
    def invite(self, url='https://origin.example.org', ticket='a' * 40):
        return 'buggcache1.' + base64.urlsafe_b64encode(json.dumps(dict(url=url, ticket=ticket)).encode()).decode().rstrip('=')

    async def test_one_use_invitation_is_parsed_and_private_origin_rejected(self):
        self.assertEqual(invitation(self.invite()), ('https://origin.example.org', 'a' * 40))
        for url in ('http://origin.example.org', 'https://127.0.0.1',
                    'https://user@origin.example.org', 'https://origin.example.org/path'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                invitation(self.invite(url=url))

    async def test_connect_persists_and_new_room_grants_use_web_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text('{}', encoding='utf-8')
            state = {}
            relay = Obj(content_cache_config=None, closed_rooms=set())
            portal = Obj(relay=relay, state=state, federation=Obj(id='node_1'),
                         session=lambda *_args, **_kwargs: None,
                         save=lambda: path.write_text(json.dumps(state), encoding='utf-8'))
            admin = ContentCacheAdmin(portal)
            request = Obj(method='POST', json=AsyncMock(return_value=dict(action='connect', invitation=self.invite())))
            with patch('tunnel.content_cache_admin.claim', new=AsyncMock(return_value=dict(
                    url='https://origin.example.org', issuer='web-node', secret='s' * 48,
                    name='Origin', enabled=True))) as claim:
                response = await admin.handle(request)
            self.assertTrue(json.loads(response.text)['configured'])
            self.assertNotIn('secret', response.text)
            self.assertEqual(claim.await_args.args, ('https://origin.example.org', 'a' * 40, 'node_1'))
            self.assertEqual(json.loads(path.read_text())['room_cache']['issuer'], 'web-node')
            host = Obj(role='host', authenticated=True, conn=Obj(is_closed=lambda: False),
                       room='a' * 32, member_id='host', relay=relay)
            room = Obj(owner=host, lobby=Obj(blocked={}), game=dict(mods_count=0))
            with patch.dict(os.environ, BUGGCRAFT_ROOM_CACHE_URL='https://old.example.org',
                            BUGGCRAFT_ROOM_CACHE_SECRET='x' * 40):
                signed = grant(host, room, {'share': True})
                self.assertEqual(signed['url'], 'https://origin.example.org')
                self.assertIn('token', signed)
                request.json = AsyncMock(return_value=dict(action='disconnect'))
                await admin.handle(request)
                with self.assertRaises(ValueError):
                    grant(host, room, {'share': True})
