"""Friend aliases over authenticated HTTP, preserving full signed identities."""
import secrets
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
import asyncssh
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from tunnel.federation import Federation
from tunnel.friend_codes import friend_code
from tunnel.portal import Portal
from tunnel.relay import RelayServer
from tests.account_fixture import enroll


class FriendCodeHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_me_and_lookup_share_code_without_changing_account(self):
        with tempfile.TemporaryDirectory() as directory:
            key = asyncssh.generate_private_key('ssh-ed25519')
            relay = await RelayServer('127.0.0.1', 0, host_key=key, token=secrets.token_urlsafe(48)).start()
            self.addAsyncCleanup(relay.close)
            fed = Federation(key, dict(server_host='127.0.0.1', server_port=relay.port,
                token=relay.token, server_public_key=key.export_public_key().decode()),
                network_key=secrets.token_urlsafe(48))
            relay.federation, fed.relay = fed, relay
            state = Path(directory) / 'state.json'
            state.write_text('{}', encoding='utf-8')
            portal = Portal(relay, fed, state)
            self.addAsyncCleanup(fed.close)
            account, _ = enroll(fed.accounts, 'Alice')
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            fed.accounts.sessions[token] = dict(account=account, csrf=csrf, expires=time.monotonic() + 60)
            client = TestClient(TestServer(portal.app), cookie_jar=CookieJar(unsafe=True))
            await client.start_server()
            self.addAsyncCleanup(client.close)
            client.session.cookie_jar.update_cookies({'account_session': token})
            response = await client.get('/api/account/me')
            self.assertEqual(response.status, 200)
            me = await response.json()
            self.assertEqual(me['account'], account)
            self.assertEqual(me['friendCode'], friend_code(account))
            for identifier in (me['friendCode'].lower(), me['friendCode'].replace('-', ''), account, 'ALICE'):
                response = await client.post('/api/account/lookup', json={'username': identifier},
                                             headers={'X-Account-CSRF': csrf})
                self.assertEqual(response.status, 200, await response.text())
                self.assertEqual((await response.json())['users'], [dict(account=account, username='Alice')])
