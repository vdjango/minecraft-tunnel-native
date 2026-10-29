"""Signed global counts, bounded authenticated presence and private-room privacy."""
import asyncio
import copy
from pathlib import Path
import secrets
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
import asyncssh
from tunnel.federation import Federation, TTL
from tunnel.directory import RoomControlSession


class NetworkPresenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.nodes = []
        secret = secrets.token_urlsafe(48)
        for index in range(3):
            key = asyncssh.generate_private_key('ssh-ed25519')
            node = Federation(key, dict(server_host='127.0.0.1', server_port=20000 + index,
                token=secrets.token_urlsafe(48), server_public_key=key.export_public_key().decode()),
                network_key=secret, name=f'Node {index}')
            node.relay = SimpleNamespace(rooms={'a' * 32: SimpleNamespace(public=False)},
                capacity=SimpleNamespace(members=lambda room, count=index + 1: count))
            # Empty policy is only used by these minimal signing fixtures.
            node.routing_sample = dict(region='', accepting=True, utilization=None, remaining_visitors=None)
            node.routing_sample_at = time.monotonic()
            node.publish()
            self.nodes.append(node)
        self.nodes[0].ingest([node.records[node.id] for node in self.nodes[1:]])

    async def asyncTearDown(self):
        await asyncio.gather(*(node.close() for node in self.nodes))

    async def test_private_rooms_contribute_to_signed_global_counts(self):
        node = self.nodes[0]
        self.assertEqual(node.online_players(), 6)
        self.assertTrue(all('room' not in item for item in node.records[node.id]['body']['rooms']))
        node.seen[self.nodes[1].id] = time.monotonic() - TTL - 1
        self.assertEqual(node.online_players(), 4)

    async def test_tampered_counts_cannot_replace_signed_record(self):
        receiver, source = self.nodes[:2]
        record = copy.deepcopy(source.records[source.id])
        record['body']['statistics']['players'] = 90000
        record['body']['issued'] += 1
        receiver.ingest([record])
        self.assertEqual(receiver.record_player_count(receiver.records[source.id]), 2)

    async def test_network_list_reports_partial_failure_and_caches_success(self):
        node = self.nodes[0]
        node.relay.diagnostics = SimpleNamespace(online_players=lambda: [{'username': 'local'}])
        async def remote(profile, operation, **payload):
            if profile['server_port'] == 20002:
                raise OSError('offline')
            self.assertEqual(operation, 'online_players')
            return dict(players=[{'username': 'remote'}], more=False)
        node.account_rpc = AsyncMock(side_effect=remote)
        result = await node.network_players()
        self.assertEqual([p['username'] for p in result['players']], ['local', 'remote'])
        self.assertTrue(result['partial'])
        self.assertEqual(result['unavailable_nodes'], [self.nodes[2].id])
        self.assertIs(await node.network_players(), result)
        self.assertEqual(node.account_rpc.await_count, 2)


if __name__ == '__main__':
    unittest.main()
