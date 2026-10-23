"""Membership has no fixed cap; each wire exchange and worker pool has a budget."""
import asyncio
import json
from pathlib import Path
import random
import secrets
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
import asyncssh
from tunnel.federation import Federation
from tunnel.federation_schedule import Schedule, FANOUT
from tunnel import federation_wire as wire
from tunnel.directory import MAX_FRAME, RoomControlSession


class MembershipTests(unittest.TestCase):
    def setUp(self):
        network = secrets.token_urlsafe(48)
        self.nodes = []
        for i in range(130):
            key = asyncssh.generate_private_key('ssh-ed25519')
            node = Federation(key, dict(server_host='127.0.0.1', server_port=10000+i,
                token=secrets.token_urlsafe(48), server_public_key=key.export_public_key().decode()),
                network_key=network)
            node.relay = SimpleNamespace(rooms={})
            node.publish()
            self.nodes.append(node)

    def test_all_signed_members_and_invitations_survive_old_limit(self):
        receiver, *others = self.nodes
        receiver.ingest([node.records[node.id] for node in others])
        self.assertEqual(len(receiver.live()), 130)
        for node in others:
            receiver.join(node.invitation())
        self.assertEqual(len(receiver.seeds), 129)

    def test_paginated_snapshot_reconstructs_every_member_with_bounded_requests(self):
        sender, receiver, *others = self.nodes
        sender.ingest([node.records[node.id] for node in [receiver, *others]])
        after, visited, pages = '', set(), 0
        while True:
            known = wire.page_versions(receiver.live(), after, sender.id)
            request = dict(op='snapshot', record_cursor=after, record_versions=known)
            self.assertLess(len(json.dumps(request).encode()) + 1, MAX_FRAME)
            value = sender.snapshot(record_versions=known, record_cursor=after)
            self.assertLessEqual(len(value['records']), wire.PAGE_RECORDS)
            receiver.ingest(value['records'])
            pages += 1
            visited.update(receiver.live())
            after = value['record_cursor']
            if not after:
                break
            self.assertLess(pages, 10, 'Cursor failed to advance')
        self.assertEqual(visited, {node.id for node in self.nodes})
        self.assertGreater(pages, 1)
        # A second pass skips unchanged records, without losing pagination.
        count = 0
        while True:
            value = sender.snapshot(record_versions=wire.page_versions(receiver.live(), after, sender.id),
                                    record_cursor=after)
            count += len(value['records'])
            after = value['record_cursor']
            if not after:
                break
        self.assertEqual(count, 0)

    def test_invalid_cursor_and_oversized_wire_batch_are_rejected(self):
        sender = self.nodes[0]
        for value in (None, 1, [], 'x', '0'*33):
            with self.assertRaises(ValueError):
                sender.snapshot(record_cursor=value)
        with self.assertRaises(ValueError):
            sender.ingest([{}] * (wire.MAX_RECORDS_PER_MESSAGE + 1))

    def test_client_discovery_pages_large_membership_within_native_response_budget(self):
        receiver, *others = self.nodes
        receiver.ingest([node.records[node.id] for node in others])
        directory = RoomControlSession(SimpleNamespace(role='directory', room='', relay=receiver.relay))
        receiver.relay.federation = receiver
        offset, visited = 0, set()
        while True:
            value = directory.dispatch(dict(op='topology', offset=offset, limit=5))
            self.assertLess(len(json.dumps(value).encode()), 65536)
            self.assertLessEqual(len(value['nodes']), 5)
            self.assertEqual(value['total'], 129)
            visited.update(p['server_port'] for p in value['nodes'])
            offset = value['next']
            if offset is None:
                break
        self.assertEqual(visited, {node.profile['server_port'] for node in others})
        for values in (dict(offset=-1), dict(limit=0), dict(limit=33), dict(limit=True)):
            with self.assertRaises(ValueError):
                directory.dispatch(dict(op='topology', **values))


class WorkBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_thousand_nodes_do_not_increase_fanout_or_starve_discovery(self):
        schedule = Schedule(random.Random(17))
        visited = set()
        members = list(range(1000))
        for tick in range(1000):
            chosen = schedule.select(members, {}, {0, 1}, tick * 2)
            self.assertLessEqual(len(chosen), FANOUT)
            self.assertTrue({0, 1}.issubset(chosen))
            visited.update(chosen)
        self.assertEqual(visited, set(members))

    async def test_account_operations_use_three_workers_and_cancel_cleanly(self):
        key = asyncssh.generate_private_key('ssh-ed25519')
        fed = Federation(key, dict(server_host='127.0.0.1', server_port=3333,
            token=secrets.token_urlsafe(48), server_public_key=key.export_public_key().decode()),
            network_key=secrets.token_urlsafe(48))
        self.addAsyncCleanup(fed.close)
        active, peak = 0, 0
        async def rpc(profile, operation, **payload):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(.001)
                return {'reserved': True}
            finally:
                active -= 1
        with patch.object(fed, 'account_rpc', rpc):
            results = await fed.account_results(range(1000), 'username_reserve')
            self.assertEqual(len(results), 1000)
            self.assertLessEqual(peak, 3)
            task = asyncio.create_task(fed.account_results(range(1000), 'username_reserve'))
            await asyncio.sleep(.01)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(active, 0)
