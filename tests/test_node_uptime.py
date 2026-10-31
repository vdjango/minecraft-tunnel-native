"""Node uptime keeps the current run separate from persisted cumulative time."""
import json
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
import asyncssh
from aiohttp.test_utils import TestClient, TestServer

from tunnel.federation import Federation
from tunnel.portal import Portal
from tunnel.relay import RelayServer


class NodeUptimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_total_days_survive_node_restart_while_current_run_resets(self):
        with tempfile.TemporaryDirectory() as directory:
            key = asyncssh.generate_private_key('ssh-ed25519')
            relay = await RelayServer('127.0.0.1', 0, host_key=key,
                                      token=secrets.token_urlsafe(48)).start()
            self.addAsyncCleanup(relay.close)
            fed = Federation(key, dict(server_host='127.0.0.1', server_port=relay.port,
                token=relay.token, server_public_key=key.export_public_key().decode()),
                network_key=secrets.token_urlsafe(48))
            relay.federation, fed.relay = fed, relay
            self.addAsyncCleanup(fed.close)
            state = Path(directory) / 'node-state.json'
            state.write_text('{}', encoding='utf-8')
            portal = Portal(relay, fed, state)
            client = TestClient(TestServer(portal.app))
            await client.start_server()
            portal.last_uptime_checkpoint -= 2 * 86400 + 3600
            response = await client.get('/api/status')
            first = await response.json()
            self.assertEqual(response.status, 200)
            self.assertGreaterEqual(first['total_uptime_seconds'], 2 * 86400 + 3600)
            await client.close()  # graceful shutdown persists uptime before an update
            persisted = json.loads(state.read_text(encoding='utf-8'))['total_uptime_seconds']
            self.assertGreaterEqual(persisted, 2 * 86400 + 3600)
            restarted = Portal(relay, fed, state)
            second = restarted.public_state()
            self.assertLess(second['uptime_seconds'], 5)
            self.assertGreaterEqual(second['total_uptime_seconds'], 2 * 86400 + 3600)

    async def test_invalid_saved_uptime_cannot_corrupt_public_status(self):
        with tempfile.TemporaryDirectory() as directory:
            key = asyncssh.generate_private_key('ssh-ed25519')
            relay = await RelayServer('127.0.0.1', 0, host_key=key,
                                      token=secrets.token_urlsafe(48)).start()
            self.addAsyncCleanup(relay.close)
            fed = Federation(key, dict(server_host='127.0.0.1', server_port=relay.port,
                token=relay.token, server_public_key=key.export_public_key().decode()),
                network_key=secrets.token_urlsafe(48))
            relay.federation, fed.relay = fed, relay
            self.addAsyncCleanup(fed.close)
            state = Path(directory) / 'node-state.json'
            for invalid in (-5, 'NaN', 'unavailable'):
                state.write_text(json.dumps({'total_uptime_seconds': invalid}), encoding='utf-8')
                portal = Portal(relay, fed, state)
                self.assertGreaterEqual(portal.public_state()['total_uptime_seconds'], 0)
                self.assertLess(portal.public_state()['total_uptime_seconds'], 5)
