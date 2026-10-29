"""Persistent per-player metadata, including headless nodes and storage failures."""
import asyncio
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
from tunnel.diagnostics import Diagnostics, player_identity


class PlayerLogTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_keeps_numeric_ssh_evidence_without_arbitrary_text(self):
        for reason, expected in (('heartbeat-timeout', 'heartbeat_timeout'),
                                 ('control-rpc-timeout', 'control_rpc_timeout')):
            peer = self.peer(reason)
            self.logs.connected(peer)
            peer.disconnect_reason = reason
            peer.transport_diagnostic = dict(schema=1, rx_age_ms=20001, poll_gap_ms=51,
                probe_attempts=3, ssh_connected=True, ssh_error_kind='socket-reset',
                ssh_error_code=2, ssh_rx_packets=80, ssh_tx_packets=90,
                password='never-log-me', error_text='private-disconnect-message',
                ssh_rx_bytes='private-value', ssh_tx_bytes=float('inf'), ssh_status=True)
            self.logs.disconnected(peer, None)
            await self.logs.flush()
            session = next(s for s in self.logs.sessions() if s['player'] == player_identity(peer))
            event = self.logs.read(session['player'], session['session'])['events'][-1]
            self.assertEqual(event['reason'], expected)
            self.assertEqual(event['transport']['rx_age_ms'], 20001)
            self.assertEqual(event['transport']['ssh_error_kind'], 'socket-reset')
            self.assertNotIn('ssh_rx_bytes', event['transport'])
            self.assertNotIn('ssh_tx_bytes', event['transport'])
            self.assertNotIn('ssh_status', event['transport'])
            self.assertNotIn('private', json.dumps(event))
            self.assertNotIn('never-log-me', json.dumps(event))

    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='buggcraft-player-logs-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'players'
        self.relay = SimpleNamespace(federation=None, closing=False)
        self.logs = Diagnostics(self.root, self.relay)

    def peer(self, identity='one', account=None):
        # RoomPeer instances use identity hashing, not equality by their fields.
        class Peer:
            pass
        peer = Peer()
        peer.account, peer.chat_id = account, identity
        peer.role, peer.room, peer.streams = 'client', 'same-room', set()
        return peer

    async def test_account_reconnect_is_stable_anonymous_invite_users_are_separate(self):
        account = 'acct-private-identity'
        first, second = self.peer('one', account), self.peer('two', account)
        self.assertEqual(player_identity(first), 'account-' + hashlib.sha256(account.encode()).hexdigest()[:32])
        self.assertEqual(player_identity(first), player_identity(second))
        anonymous = [self.peer('anonymous-' + str(i)) for i in range(3)]
        self.assertEqual(len({player_identity(peer) for peer in anonymous}), 3)
        for peer in (first, second, *anonymous):
            self.logs.connected(peer)
            self.logs.disconnected(peer, None)
        await self.logs.flush()
        self.assertEqual(len(list((self.root / player_identity(first)).glob('*.events.jsonl'))), 2)
        self.assertEqual(len([p for p in self.root.iterdir() if p.is_dir()]), 4)
        content = ''.join(path.read_text(encoding='utf-8') for path in self.root.glob('*/*.jsonl'))
        self.assertNotIn(account, content)
        self.assertEqual(len(Diagnostics(self.root, self.relay).sessions()), 5)

    async def test_sample_flood_cannot_evict_connection_and_disconnect_evidence(self):
        self.logs.queue = deque(maxlen=3)
        peer = self.peer()
        self.logs.connected(peer)
        self.logs.emit(peer, 'sample', upload_bytes=1)
        self.logs.emit(peer, 'sample', upload_bytes=2)
        peer.disconnect_reason = 'heartbeat-timeout'
        self.logs.disconnected(peer, None)
        # An unrelated still-connected player continues producing samples.
        other = self.peer('other')
        self.logs.connected(other)
        for _ in range(100):
            self.logs.emit(other, 'sample')
        await self.logs.flush()
        events = [json.loads(line) for path in self.root.glob('*/*.events.jsonl')
                  for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(row['event'] == 'disconnected' and row['reason'] == 'heartbeat_timeout' for row in events))
        self.assertGreater(self.logs.dropped, 0)
        health = json.loads((self.root / 'journal-health.json').read_text(encoding='utf-8'))
        self.assertEqual(health['dropped_events'], self.logs.dropped)
        self.assertEqual(health['pending_events'], 0)

    async def test_incident_and_identity_survive_bulk_rotation_and_restart(self):
        peer = self.peer()
        self.logs.connected(peer)
        session = dict(self.logs.active[peer])
        await self.logs.flush()
        self.logs._append(session['player'], session['session'],
                          [dict(event='sample', padding='x' * 250) for _ in range(12)], 400)
        self.logs.disconnected(peer, ConnectionResetError('private-message'))
        await self.logs.flush()
        restarted = Diagnostics(self.root, self.relay)
        self.assertEqual(len(restarted.sessions()), 1)
        critical = restarted.read(session['player'], session['session'])['critical_events']
        self.assertTrue(any(row['event'] == 'connected' for row in
                            restarted.read(session['player'], session['session'])['events']))
        self.assertEqual([row['event'] for row in critical], ['connected', 'disconnected'])
        self.assertLess(critical[0]['sequence'], critical[1]['sequence'])
        self.assertLessEqual(critical[0]['monotonic_ms'], critical[1]['monotonic_ms'])
        self.assertNotIn('private-message', json.dumps(critical))

    async def test_storage_budget_reserves_incidents_before_newer_samples(self):
        directory = self.root / 'anonymous-one'
        directory.mkdir(parents=True)
        incident = directory / 'session.events.jsonl'
        incident.write_text('{"event":"disconnected"}\n', encoding='utf-8')
        sample = directory / 'session.jsonl'
        sample.write_text('x' * 5000, encoding='utf-8')
        self.logs.max_bytes = 4096
        self.logs._prune()
        self.assertTrue(incident.exists())
        self.assertFalse(sample.exists())

    async def test_critical_rotation_preserves_offline_identity_and_deduplicates_headers(self):
        peer = self.peer()
        self.logs.connected(peer)
        session = dict(self.logs.active[peer])
        await self.logs.flush()
        self.logs._append(session['player'], session['session'] + '.events',
            [dict(at=10000000000 + index, event='stream_closed', index=index, padding='x' * 200)
             for index in range(20)], 1200)
        self.logs.active.clear()
        restarted = Diagnostics(self.root, self.relay)
        self.assertEqual(len(restarted.sessions()), 1)
        events = restarted.read(session['player'], session['session'])['events']
        self.assertEqual(sum(row['event'] == 'connected' for row in events), 1)
        self.assertTrue(any(row.get('index') == 19 for row in events))

    async def test_headless_lifecycle_flushes_on_shutdown(self):
        lifecycle = self.logs.lifecycle(None)
        await anext(lifecycle)
        peer = self.peer()
        self.logs.connected(peer)
        self.logs.traffic(peer, 'upload', 12345)
        self.logs.disconnected(peer, ConnectionResetError('private-payload-and-secret'))
        await lifecycle.aclose()
        session = self.logs.sessions()[0]
        events = self.logs.read(session['player'], session['session'])['events']
        self.assertEqual(events[-1]['event'], 'disconnected')
        self.assertEqual(events[-1]['upload_bytes'], 12345)
        self.assertEqual(events[-1]['error_type'], 'ConnectionResetError')
        self.assertNotIn('private-payload-and-secret', json.dumps(events))

    async def test_report_allowlist_queue_and_disk_failure(self):
        peer = self.peer()
        self.logs.connected(peer)
        self.logs.report(peer, {'path': 'p2p', 'game_rtt_ms': 5, 'password': 'never-log-me', 'payload': 'game-data'})
        await self.logs.flush()
        journal = ''.join(path.read_text(encoding='utf-8') for path in self.root.glob('*/*.jsonl'))
        self.assertNotIn('never-log-me', journal)
        self.assertNotIn('game-data', journal)
        self.logs.queue = deque(maxlen=2)
        for _ in range(10):
            self.logs.emit(peer, 'sample', sent_bytes=100)
        self.assertEqual(self.logs.dropped, 8)
        with patch.object(Path, 'mkdir', side_effect=OSError('Disk full')):
            await self.logs.flush()
        self.assertEqual(self.logs.write_errors, 1)
        self.logs.emit(peer, 'recovered')
        await self.logs.flush()
        session = self.logs.active[peer]
        self.assertEqual(self.logs.read(session['player'], session['session'])['events'][-1]['event'], 'recovered')

    async def test_online_player_reports_expose_path_and_rtt(self):
        self.relay.rooms = {'same-room': SimpleNamespace(title='Vanilla survival', public=True)}
        self.relay.federation = SimpleNamespace(accounts=SimpleNamespace(
            social=SimpleNamespace(name=lambda account: account)))
        peer = self.peer('player', account='alice')
        self.logs.connected(peer)
        self.logs.report(peer, {'path': 'p2p', 'peer_rtt_ms': 18.25,
                                'control_rtt_ms': 7, 'game_rtt_ms': 12})
        player, = self.logs.online_players()
        self.assertEqual(player['username'], 'alice')
        self.assertEqual(player['room'], 'Vanilla survival')
        self.assertEqual(player['path'], 'p2p')
        self.assertEqual(player['peer_rtt_ms'], 18.25)
        self.assertEqual(player['control_rtt_ms'], 7)
        self.assertGreater(player['last_report_at'], 0)
        self.assertEqual(self.logs.transport_counts(), {'p2p':1,'relay':0,'unknown':0})
        self.logs.active[peer]['last_report'] -= 21
        self.assertEqual(self.logs.transport_counts(), {'p2p':0,'relay':0,'unknown':1})
        stale, = self.logs.online_players()
        self.assertEqual(stale['path'], 'unknown')
        self.assertIsNone(stale['peer_rtt_ms'])

    async def test_disk_unavailable_does_not_prevent_headless_lifecycle(self):
        self.root.write_text('not a directory', encoding='utf-8')
        lifecycle = self.logs.lifecycle(None)
        await anext(lifecycle)
        self.logs.connected(self.peer())
        await lifecycle.aclose()
        self.assertGreaterEqual(self.logs.write_errors, 2)

    async def test_previous_rotation_is_read_and_not_listed_as_duplicate_session(self):
        peer = self.peer()
        self.logs.connected(peer)
        session = self.logs.active[peer]
        await self.logs.flush()
        path = self.root / session['player'] / (session['session'] + '.jsonl')
        path.replace(path.with_suffix('.1.jsonl'))
        path.write_text(json.dumps({'event': 'connected', **self.logs.public_session(session)}) + '\n' +
                        json.dumps({'event': 'after-rotation'}) + '\n', encoding='utf-8')
        result = self.logs.read(session['player'], session['session'])
        self.assertEqual(len(result['events']), 3)
        self.assertEqual(result['events'][-1]['event'], 'after-rotation')
        self.assertEqual(len(self.logs.sessions()), 1)

    async def test_age_and_total_budget_remove_only_journals(self):
        directory = self.root / 'anonymous-old'
        directory.mkdir(parents=True)
        expired = directory / 'old.jsonl'
        expired.write_text('{}\n', encoding='utf-8')
        os.utime(expired, (1, 1))
        retained = directory / 'operator.txt'
        retained.write_text('keep', encoding='utf-8')
        self.logs._prune()
        self.assertFalse(expired.exists())
        self.assertEqual(retained.read_text(), 'keep')
        (directory / 'large.jsonl').write_text('x' * 100, encoding='utf-8')
        self.logs.max_bytes = 32
        self.logs._prune()
        self.assertFalse((directory / 'large.jsonl').exists())

    @unittest.skipIf(os.name == 'nt', 'Creating symlinks requires additional Windows privileges')
    async def test_symlink_journals_cannot_be_read_or_written(self):
        external = Path(self.temporary.name) / 'outside'
        external.mkdir()
        self.root.mkdir()
        (self.root / 'anonymous-one').symlink_to(external, target_is_directory=True)
        peer = self.peer()
        self.logs.connected(peer)
        await self.logs.flush()
        self.assertTrue(self.logs.write_errors)
        self.assertFalse(list(external.iterdir()))
        self.assertFalse(self.logs._paths())
        with self.assertRaises(ValueError):
            self.logs.read('anonymous-one', self.logs.active[peer]['session'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
