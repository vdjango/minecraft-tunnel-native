"""Joining adopts the inviter's resource settings and never writes them back."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import AsyncMock, Mock, patch

import asyncssh

from tunnel.content_cache import grant
from tunnel.content_cache_admin import ContentCacheAdmin
from tunnel.federation import Federation
from tunnel.portal import Portal


class FederationResourceSettingsTests(unittest.IsolatedAsyncioTestCase):
    def portal(self, directory, name, config):
        key = asyncssh.generate_private_key('ssh-ed25519')
        fed = Federation(key, dict(server_host=name + '.example.org', server_port=3333,
            token='t' * 48, server_public_key=key.export_public_key().decode()),
            network_key=name * 48, name=name)
        portal = Portal.__new__(Portal)
        portal.federation = fed
        portal.relay = Obj(content_cache_config=config, closed_rooms=set())
        portal.state = {} if config is None else dict(room_cache=config)
        portal.session = lambda *_args, **_kwargs: None
        path = Path(directory) / (name + '.json')
        def save():
            path.write_text(json.dumps(dict(portal.state, network_key=fed.network_key,
                                           seeds=list(fed.seeds))), encoding='utf-8')
        portal.save = fed.on_change = save
        portal.content_cache_admin = ContentCacheAdmin(portal)
        return portal, path

    def config(self, name):
        return dict(enabled=True, url='https://' + name + '.example.org',
                    issuer=name, secret=name * 48, name=name)

    async def test_join_overwrites_joiner_only_and_persists_active_grant_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            inviter, source = self.portal(directory, 'a', self.config('a'))
            joiner, destination = self.portal(directory, 'b', self.config('b'))
            inviter.save()
            before = source.read_bytes()
            invitation = await inviter.invite(Obj())
            self.assertEqual(invitation.headers['Cache-Control'], 'no-store')
            data = json.loads(invitation.text)
            await joiner.join(Obj(json=AsyncMock(return_value=data)))
            self.assertEqual(joiner.state['room_cache'], inviter.state['room_cache'])
            self.assertEqual(json.loads(destination.read_text())['room_cache'], self.config('a'))
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(inviter.federation.seeds, {})
            self.assertIn(inviter.federation.id, joiner.federation.seeds)
            host = Obj(role='host', authenticated=True, conn=Obj(is_closed=lambda: False),
                       room='f' * 32, member_id='host', relay=joiner.relay)
            room = Obj(owner=host, lobby=Obj(blocked={}), game={})
            self.assertEqual(grant(host, room, {'share': True})['url'], self.config('a')['url'])
            # Persistent settings are also the configuration used after restart.
            restored = Obj(state=json.loads(destination.read_text()), relay=Obj())
            ContentCacheAdmin(restored)
            self.assertEqual(restored.relay.content_cache_config, self.config('a'))

    async def test_disabled_inviter_overrides_joiners_environment_and_saved_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            inviter, _ = self.portal(directory, 'a', dict(enabled=False))
            joiner, path = self.portal(directory, 'b', self.config('b'))
            data = json.loads((await inviter.invite(Obj())).text)
            with patch.dict(os.environ, BUGGCRAFT_ROOM_CACHE_URL='https://old.example.org',
                            BUGGCRAFT_ROOM_CACHE_SECRET='x' * 48):
                await joiner.join(Obj(json=AsyncMock(return_value=data)))
                self.assertEqual(json.loads(path.read_text())['room_cache'], dict(enabled=False))
                status = await joiner.content_cache_admin.handle(Obj(method='GET'))
                self.assertFalse(json.loads(status.text)['configured'])

    async def test_invalid_old_or_rejected_invitation_preserves_local_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            inviter, _ = self.portal(directory, 'a', self.config('a'))
            joiner, _ = self.portal(directory, 'b', self.config('b'))
            original = dict(joiner.state)
            data = json.loads((await inviter.invite(Obj())).text)
            cases = [dict(data, room_cache=dict(self.config('a'), url='https://127.0.0.1')),
                     inviter.federation.invitation(),
                     dict(data, network_key='bad')]
            for value in cases:
                with self.subTest(value=list(value)), self.assertRaises(ValueError):
                    await joiner.join(Obj(json=AsyncMock(return_value=value)))
                self.assertEqual(joiner.state, original)
                self.assertEqual(joiner.relay.content_cache_config, self.config('b'))
                self.assertEqual(joiner.federation.seeds, {})

    async def test_legacy_environment_origin_is_included_in_admin_invitation(self):
        with tempfile.TemporaryDirectory() as directory:
            inviter, _ = self.portal(directory, 'a', None)
            with patch.dict(os.environ, BUGGCRAFT_ROOM_CACHE_URL='https://origin.example.org/',
                            BUGGCRAFT_ROOM_CACHE_SECRET='x' * 48, BUGGCRAFT_ROOM_CACHE_ISSUER='old'):
                data = json.loads((await inviter.invite(Obj())).text)
            self.assertEqual(data['room_cache']['url'], 'https://origin.example.org')
            self.assertEqual(data['room_cache']['issuer'], 'old')

    async def test_save_failure_rolls_back_membership_and_resource_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            inviter, _ = self.portal(directory, 'a', self.config('a'))
            joiner, _ = self.portal(directory, 'b', self.config('b'))
            key = joiner.federation.network_key
            original = dict(joiner.state)
            joiner.federation.on_change = Mock(side_effect=OSError('disk full'))
            data = json.loads((await inviter.invite(Obj())).text)
            with self.assertRaises(OSError):
                await joiner.join(Obj(json=AsyncMock(return_value=data)))
            self.assertEqual(joiner.state, original)
            self.assertEqual(joiner.relay.content_cache_config, self.config('b'))
            self.assertEqual(joiner.federation.seeds, {})
            self.assertEqual(joiner.federation.network_key, key)
