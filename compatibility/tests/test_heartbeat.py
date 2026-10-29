"""Continuous gossip must survive local failures and service home isolation."""
import asyncio
from pathlib import Path
import secrets
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import asyncssh
from tunnel.federation import Federation
from tunnel.relay import RelayServer


class HeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.relays, self.feds = [], []
        network = secrets.token_urlsafe(48)
        for _ in range(3):
            key = asyncssh.generate_private_key('ssh-ed25519')
            relay = await RelayServer('127.0.0.1', 0, host_key=key, token=secrets.token_urlsafe(48)).start()
            fed = Federation(key, dict(server_host='127.0.0.1', server_port=relay.port,
                token=relay.token, server_public_key=key.export_public_key().decode()), network_key=network)
            fed.relay, relay.federation = relay, fed
            fed.publish()
            self.relays.append(relay)
            self.feds.append(fed)

    async def asyncTearDown(self):
        await asyncio.gather(*(f.close() for f in self.feds))
        await asyncio.gather(*(r.close() for r in self.relays))

    async def test_three_background_nodes_converge_with_inaccessible_ssh_home(self):
        a, b, c = self.feds
        b.join(a.invitation())
        c.join(b.invitation())
        is_dir = Path.is_dir
        def restricted(path):
            if '.ssh' in path.parts:
                raise PermissionError('Service home is inaccessible')
            return is_dir(path)
        with patch.object(Path, 'is_dir', restricted):
            for fed in self.feds:
                fed.task = asyncio.create_task(fed.run())
            async with asyncio.timeout(8):
                # Receiving a signed announcement precedes completing the
                # outgoing exchange. Wait for both observable outcomes.
                while any(len(f.live()) != 3 or not f.last_success for f in self.feds):
                    await asyncio.sleep(.05)
        self.assertTrue(all(f.last_success for f in self.feds))

    async def test_local_failure_does_not_permanently_stop_heartbeat(self):
        fed = self.feds[0]
        fed.sync_once = AsyncMock(side_effect=[OSError('Write unavailable'), None])
        with self.assertLogs('tunnel.federation', level='ERROR'):
            fed.task = asyncio.create_task(fed.run())
            async with asyncio.timeout(4):
                while fed.sync_once.await_count < 2:
                    await asyncio.sleep(.02)
        await fed.close()
        self.assertTrue(fed.task.cancelled())


if __name__ == '__main__':
    unittest.main()
