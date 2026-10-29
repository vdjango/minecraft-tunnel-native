"""Authentication evidence uses fixed reasons and cannot leak across tasks."""
import asyncio
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
import asyncssh
from tunnel.accounts import Accounts, AUTHENTICATION_FAILURE, encode
from tunnel.diagnostics import Diagnostics
from tunnel.federation import Federation
from tunnel.relay import RoomPeer
from tests.account_fixture import enroll


class AuthenticationDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='buggcraft-auth-logs-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        key = asyncssh.generate_private_key('ssh-ed25519')
        fed = Federation(key, dict(server_host='127.0.0.1', server_port=3333,
            token=secrets.token_urlsafe(48), server_public_key=key.export_public_key().decode()),
            network_key=secrets.token_urlsafe(48), name='test')
        self.addAsyncCleanup(fed.close)
        self.store = Accounts(fed, self.root / 'accounts.json')
        fed.accounts = self.store
        fed.publish()
        self.account, self.credential = enroll(self.store, 'diagnostic_alice')
        self.relay = SimpleNamespace(token=secrets.token_urlsafe(48), federation=fed,
            account_authenticate=self.store.authenticate, closing=False)
        self.relay.diagnostics = Diagnostics(self.root / 'players', self.relay)

    async def authenticate(self, token=None):
        peer = RoomPeer(self.relay)
        accepted = await peer.validate_password('directory:rooms', json.dumps(
            dict(token=token or self.credential)))
        return peer, accepted

    async def test_expiry_signature_and_block_rejections_are_retained_without_credentials(self):
        _, accepted = await self.authenticate()
        self.assertTrue(accepted)
        self.assertFalse(self.relay.diagnostics.queue)
        with patch('tunnel.accounts.time.time', return_value=time.time() + 8 * 86400):
            peer, accepted = await self.authenticate()
        self.assertFalse(accepted)
        # Failed claims are not proof that the bearer had a valid signed token.
        self.assertEqual(self.relay.diagnostics.queue[-1][2]['reason'], 'account-expired-claim')
        prefix = self.credential.rsplit('.', 1)[0]
        _, accepted = await self.authenticate(prefix + '.' + encode(secrets.token_bytes(64)))
        self.assertFalse(accepted)
        self.assertEqual(self.relay.diagnostics.queue[-1][2]['reason'], 'account-node-signature-invalid')
        self.relay.account_authenticate = lambda token: self.store.authenticate(token, blocked=[self.account])
        _, accepted = await self.authenticate()
        self.assertFalse(accepted)
        self.assertEqual(self.relay.diagnostics.queue[-1][2]['reason'], 'account-blocked')
        await self.relay.diagnostics.flush()
        events = [json.loads(line) for path in (self.root / 'players').glob('*/*.events.jsonl')
                  for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(events), 3)
        self.assertEqual(len(self.relay.diagnostics.sessions()), 3)
        self.assertTrue(all(row['role'] == 'directory' for row in events))
        self.assertTrue(all(row['player'] and row['session'] for row in events))
        serialized = json.dumps(events)
        self.assertNotIn(self.credential, serialized)
        self.assertNotIn(self.account, serialized)

    async def test_concurrent_authentication_reasons_are_task_local_and_reset(self):
        async def authenticate(token):
            result = self.store.authenticate(token)
            await asyncio.sleep(0)
            return result
        self.relay.account_authenticate = authenticate
        context = AUTHENTICATION_FAILURE.set('outer-task-marker')
        try:
            results = await asyncio.gather(self.authenticate(), self.authenticate('invalid-private-credential'))
            self.assertEqual([accepted for _, accepted in results], [True, False])
            self.assertEqual(AUTHENTICATION_FAILURE.get(), 'outer-task-marker')
            self.assertEqual(len(self.relay.diagnostics.queue), 1)
            self.assertEqual(self.relay.diagnostics.queue[0][2]['reason'], 'credential-format')
            self.assertNotIn('invalid-private-credential', str(self.relay.diagnostics.queue))
        finally:
            AUTHENTICATION_FAILURE.reset(context)

    async def test_repeated_authentication_attempts_have_bounded_diagnostic_rows(self):
        peer = RoomPeer(self.relay)
        for _ in range(20):
            self.assertFalse(await peer.validate_password('invalid:rooms', '{"token":"private-value"}'))
        self.assertEqual(len(self.relay.diagnostics.queue), 3)
        self.assertNotIn('private-value', str(self.relay.diagnostics.queue))


if __name__ == '__main__':
    unittest.main()
