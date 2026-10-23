"""Heartbeat fan-out, retry fairness and signed compact-refresh contracts."""
import copy
import asyncio
from pathlib import Path
import random
import secrets
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
import asyncssh
from tunnel.federation import Federation, TTL, canonical
from tunnel.federation_schedule import Schedule, FANOUT, RETRY_MAX
from tunnel import federation_wire as wire
from tunnel.federation_transport import Transport


class BudgetTests(unittest.TestCase):
    def test_thirty_two_members_have_fixed_fanout_and_fair_discovery(self):
        schedule = Schedule(random.Random(37))
        members = list(range(31))
        visited = set()
        for second in range(11):
            chosen = schedule.select(members, {}, set(), second)
            self.assertLessEqual(len(chosen), FANOUT)
            visited.update(chosen)
        self.assertEqual(visited, set(members))

    def test_inflight_cap_and_retry_backoff(self):
        schedule = Schedule(random.Random(37))
        self.assertFalse(schedule.select(range(31), {0,1,2}, set(), 0))
        for attempt in range(15):
            now = attempt * 100
            schedule.failure(0, now)
            delay = schedule.retry_at[0] - now
            self.assertGreaterEqual(delay, 1.6)
            self.assertLessEqual(delay, RETRY_MAX)
            self.assertNotIn(0, schedule.select([0], {}, set(), now + .1))
        schedule.success(0)
        self.assertNotIn(0, schedule.retry_at)
        self.assertNotIn(0, schedule.failures)

    def test_unreachable_members_do_not_starve_established_links(self):
        schedule = Schedule(random.Random(37))
        selected = schedule.select(range(31), {30}, {0,1}, 0)
        self.assertEqual(set(selected), {0,1})
        selected = schedule.select(range(31), {}, {0,1}, 2)
        self.assertTrue({0,1}.issubset(selected))
        self.assertEqual(len(selected), 3)
        self.assertTrue(.8 <= schedule.delay() <= 1.2)


class RefreshTests(unittest.TestCase):
    def setUp(self):
        network = secrets.token_urlsafe(48)
        self.nodes = []
        for port in (3333, 3334):
            key = asyncssh.generate_private_key('ssh-ed25519')
            fed = Federation(key, dict(server_host='127.0.0.1', server_port=port,
                token=secrets.token_urlsafe(48), server_public_key=key.export_public_key().decode()),
                network_key=network)
            fed.relay = SimpleNamespace(rooms={})
            fed.publish()
            self.nodes.append(fed)
        self.a, self.b = self.nodes

    def test_unchanged_state_uses_signed_refresh_and_replay_does_not_extend_lease(self):
        self.a.ingest([self.b.records[self.b.id]])
        known = wire.versions(self.a.live())
        issued = self.b.records[self.b.id]['body']['issued']
        with patch('tunnel.federation.time.time', return_value=issued + 3):
            self.b.publish()
            compact = wire.compact(self.b.live(), known)
            self.assertEqual(len(compact), 1)
            self.assertIn('refresh', compact[0])
            self.a.ingest(compact)
            self.assertEqual(self.a.records[self.b.id], self.b.records[self.b.id])
            seen = self.a.seen[self.b.id]
            with patch('tunnel.federation.time.monotonic', return_value=seen + TTL + 1):
                self.a.ingest(compact)
                self.assertEqual(self.a.seen[self.b.id], seen)
                self.assertNotIn(self.b.id, self.a.live())

    def test_unknown_base_and_forged_refresh_are_rejected(self):
        self.a.ingest([self.b.records[self.b.id]])
        original = copy.deepcopy(self.a.records[self.b.id])
        refresh = dict(refresh=dict(id=self.b.id, issued=time.time() + 1,
                                    state=wire.state_digest(original['body'])),
                       signature=original['signature'], proof=original['proof'])
        self.a.ingest([refresh])
        self.assertEqual(self.a.records[self.b.id], original)
        refresh['refresh']['state'] = '0' * 64
        self.a.ingest([refresh])
        self.assertEqual(self.a.records[self.b.id], original)

    def test_full_directory_transfers_only_when_state_changes(self):
        # A representative large signed record; rooms must still validate.
        self.b.records[self.b.id]['body']['name'] = 'x' * 80
        self.b.publish()
        self.a.ingest([self.b.records[self.b.id]])
        known = wire.versions(self.a.live())
        self.assertEqual(wire.compact(self.b.live(), known), [])
        self.b.name = 'new name'
        self.b.publish()
        changed = wire.compact(self.b.live(), known)
        self.assertIn('body', changed[0])
        self.a.ingest(changed)
        self.assertEqual(self.a.records[self.b.id]['body']['name'], 'new name')

    def test_unchanged_replication_sections_are_not_sent_again(self):
        initial = self.a.snapshot()
        delta = self.a.snapshot(snapshot_versions=initial['snapshot_versions'])
        self.assertEqual(delta['accounts'], [])
        self.assertEqual(delta['account_routes'], [])
        self.assertEqual(delta['snapshot_versions'], initial['snapshot_versions'])


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_connects_reserve_capacity_before_await(self):
        class Connection:
            def close(self): pass
            def is_closed(self): return False
            async def wait_closed(self): pass
        key = asyncssh.generate_private_key('ssh-ed25519')
        profile = dict(server_host='127.0.0.1', server_port=3333,
                       server_public_key=key.export_public_key().decode())
        pool = Transport(key, 'local')
        pool.connections = {str(i):(Connection(), (), 0) for i in range(2)}
        self.addAsyncCleanup(pool.close)
        peak = 0
        async def connect(*args, **kwargs):
            await asyncio.sleep(.01)
            return Connection()
        async def use(node):
            nonlocal peak
            async with pool.connection(profile, node):
                peak = max(peak, len(pool.connections) + len(pool.connecting))
                await asyncio.sleep(.01)
        with patch('tunnel.federation_transport.asyncssh.connect', connect):
            await asyncio.gather(*(use('new-'+str(i)) for i in range(3)))
        self.assertLessEqual(peak, FANOUT)
        self.assertFalse(pool.connecting)

    async def test_cancelled_handshake_releases_reservation(self):
        key = asyncssh.generate_private_key('ssh-ed25519')
        profile = dict(server_host='127.0.0.1', server_port=3333,
                       server_public_key=key.export_public_key().decode())
        pool = Transport(key, 'local')
        self.addAsyncCleanup(pool.close)
        entered = asyncio.Event()
        async def connect(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        async def use():
            async with pool.connection(profile, 'cancelled'):
                self.fail('Cancelled handshake completed')
        with patch('tunnel.federation_transport.asyncssh.connect', connect):
            task = asyncio.create_task(use())
            await entered.wait()
            self.assertEqual(pool.connecting, {'cancelled'})
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(pool.connecting)
        self.assertFalse(pool.connections)


if __name__ == '__main__':
    unittest.main()
