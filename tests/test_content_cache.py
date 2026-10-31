import base64
import hmac
import json
import os
import unittest
from types import SimpleNamespace as Obj
from unittest.mock import patch
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from tunnel.content_cache import grant


class ContentCacheGrantTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, BUGGCRAFT_ROOM_CACHE_URL='https://mirror.bugg.icu',
            BUGGCRAFT_ROOM_CACHE_SECRET='node-shared-secret-01234567890123456789', BUGGCRAFT_ROOM_CACHE_ISSUER='default')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.host = Obj(role='host', authenticated=True, conn=Obj(is_closed=lambda: False), room='a' * 32,
                        member_id='host', relay=Obj(closed_rooms=set()))
        self.room = Obj(owner=self.host, lobby=Obj(blocked={}), game=dict(mods_count=1, mods_hash='b' * 64))
        self.guest = Obj(**{**vars(self.host), 'role': 'client', 'member_id': 'guest'})

    def test_only_host_can_receive_signed_upload_grant_and_signature_matches(self):
        result = grant(self.host, self.room, {'share': True})
        body, signature = result['token'].encode().split(b'.')
        expected = base64.urlsafe_b64encode(hmac.digest(os.environ['BUGGCRAFT_ROOM_CACHE_SECRET'].encode(), body, 'sha256')).rstrip(b'=')
        self.assertEqual(signature, expected)
        claims = json.loads(base64.urlsafe_b64decode(body + b'=' * (-len(body) % 4)))
        self.assertTrue(claims['write'])
        self.assertEqual(claims['exp'] - claims['iat'], 300)
        self.assertEqual(claims['mods_hash'], 'b' * 64)
        self.assertNotIn('token', grant(self.guest, self.room, {}))

    def test_disabled_sharing_closed_or_kicked_session_cannot_upload(self):
        with self.assertRaises(ValueError):
            grant(self.host, self.room, {})
        grant(self.host, self.room, {'share': True})
        with self.assertRaises(ValueError):
            grant(self.host, self.room, {'share': False})
        self.assertFalse(self.room.content_sharing)
        self.host.authenticated = False
        with self.assertRaises(ValueError):
            grant(self.host, self.room, {'share': True})
        self.host.authenticated = True
        self.room.lobby.blocked['host'] = True
        with self.assertRaises(ValueError):
            grant(self.host, self.room, {'share': True})
