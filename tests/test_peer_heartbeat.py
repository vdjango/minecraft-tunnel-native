"""Real native peer heartbeats, isolated UDP loss, relay and direct recovery."""
import asyncio
import json
import sys
import time
import unittest

from tests import test_bridge as fixture
from tests import test_bridge_library as library
from tests.peer_udp_proxy import PeerUdpProxy


class PeerHeartbeatTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    add = fixture.BridgeTests.add
    exchange = fixture.BridgeTests.exchange
    wait_direct = fixture.BridgeTests.wait_direct

    def rows(self, session):
        return [json.loads(line) for path in self.logs.rglob(session+'.jsonl')
                for line in path.read_text(encoding='utf-8').splitlines()]

    async def state(self, guest, joined):
        return next(row for row in (await self.call(guest, 'session.list'))['sessions']
                    if row['session'] == joined['session'])

    async def pair(self):
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite']))
        joined = await self.wait_direct(guest, joined)
        self.assertEqual(joined['transport'], 'p2p')
        return host, guest, hosted, joined

    async def test_idle_peer_outlives_timeout_and_reports_real_pong(self):
        host, guest, hosted, joined = await self.pair()
        # No game traffic for longer than the application heartbeat lease.
        await asyncio.sleep(32)
        state = await self.state(guest, joined)
        self.assertEqual((state['state'], state['transport']), ('online', 'p2p'))
        await self.exchange(joined, b'after-idle' * 8192)
        rows = self.rows(joined['session'])
        samples = [row for row in rows if row['event'] == 'peer_heartbeat_sample']
        self.assertTrue(samples, rows)
        self.assertGreaterEqual(samples[-1]['replies'], 4)
        self.assertGreaterEqual(samples[-1]['rtt_ms'], 0)
        self.assertFalse(any(row['event'] in ('peer_heartbeat_timeout', 'endpoint_disconnected') for row in rows))

    async def test_one_way_udp_loss_preserves_ssh_other_player_and_recovers_direct(self):
        proxy = await PeerUdpProxy().start()
        self.addAsyncCleanup(proxy.close)
        host, guest, hosted, joined = await self.pair()
        survivor = await self.process()
        sn = await self.add(survivor)
        stable = await self.call(survivor, 'room.join', dict(node=sn, invite=hosted['invite']))
        stable = await self.wait_direct(survivor, stable)
        self.assertEqual(stable['transport'], 'p2p')
        await self.exchange(joined, b'before-packet-loss' * 4096)
        port = int(joined['address'].rsplit(':', 1)[1])
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        self.addCleanup(writer.close)
        writer.write(fixture.GAME_HANDSHAKE)
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(
            reader.readexactly(len(fixture.GAME_HANDSHAKE)), 5), fixture.GAME_HANDSHAKE)
        writer.write(b'old-stream')
        await writer.drain()
        self.assertEqual(await reader.readexactly(10), b'old-stream')
        identities = {peer.chat_id for peer in self.node.peers if peer.authenticated}
        # Host -> guest is blackholed. Guest -> host and both SSH links work.
        proxy.mute(1)
        started = time.monotonic()
        async with asyncio.timeout(27):
            while True:
                await self.exchange(stable, b'unaffected-player' * 32)
                state = await self.state(guest, joined)
                if state['transport'] == 'relay':
                    break
                await asyncio.sleep(.2)
        self.assertEqual(state['state'], 'online')
        self.assertEqual(state['address'], joined['address'])
        self.assertEqual(identities, {peer.chat_id for peer in self.node.peers if peer.authenticated})
        pong = await self.call(guest, 'room.request', dict(session=joined['session'], request={'op':'ping'}))
        self.assertIn('diagnostic_player', pong)
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        await self.exchange(joined, b'relay-without-leaving-room' * 4096)
        rows = self.rows(joined['session'])
        # The transport may detect failure before the 20-second application lease.
        self.assertTrue(any(row['event'] == 'peer_heartbeat_enabled' for row in rows))
        self.assertTrue(any(row['event'] == 'peer_fallback' and row.get('reason') in
                            ('heartbeat-timeout', 'probe-closed', 'peer-failed', 'peer-closed')
                            and row.get('ssh_preserved') for row in rows),
                        [(row['event'], row.get('reason')) for row in rows])
        self.assertFalse(any(row['event'] == 'endpoint_disconnected' for row in rows))
        self.assertGreater(proxy.dropped[1], 0)
        self.assertLess(time.monotonic()-started, 27)
        # Hold a relay game connection while the same session restores P2P.
        rr, rw = await asyncio.open_connection('127.0.0.1', port)
        self.addCleanup(rw.close)
        rw.write(fixture.GAME_HANDSHAKE)
        await rw.drain()
        self.assertEqual(await asyncio.wait_for(
            rr.readexactly(len(fixture.GAME_HANDSHAKE)), 5), fixture.GAME_HANDSHAKE)
        rw.write(b'relay-first')
        await rw.drain()
        self.assertEqual(await asyncio.wait_for(rr.readexactly(11), 5), b'relay-first')
        proxy.resume()
        # A failed ICE negotiation can use its full 12-second deadline before
        # exponential retry. Keep asserting actual direct recovery, but allow
        # enough time for multiple attempts on a busy Windows CI runner.
        try:
            async with asyncio.timeout(90):
                while (await self.state(guest, joined))['transport'] != 'p2p':
                    await self.exchange(stable, b'survivor-during-recovery')
                    rw.write(b'kept-relay-stream')
                    await rw.drain()
                    self.assertEqual(await asyncio.wait_for(rr.readexactly(17), 5), b'kept-relay-stream')
                    await asyncio.sleep(.2)
        except TimeoutError:
            events = [(row['event'], row.get('reason'), row.get('delay_seconds'))
                      for row in self.rows(joined['session']) if row['event'] in
                      ('peer_retry_scheduled', 'peer_retry_started', 'peer_recovered',
                       'peer_fallback', 'peer_answer_ignored', 'peer_heartbeat_timeout')]
            self.fail(f'P2P did not recover after relay: {events[-30:]}')
        rw.write(b'relay-after-direct-recovery')
        await rw.drain()
        self.assertEqual(await asyncio.wait_for(rr.readexactly(27), 5), b'relay-after-direct-recovery')
        await self.exchange(joined, b'direct-again' * 4096)
        self.assertEqual((await self.state(guest, joined))['address'], joined['address'])
        self.assertEqual(identities, {peer.chat_id for peer in self.node.peers if peer.authenticated})
        await asyncio.sleep(.5)
        paths = [row['path'] for row in self.rows(joined['session']) if row['event'] == 'game_path']
        self.assertIn('relay', paths)
        self.assertEqual(paths[-1], 'p2p')

    async def test_signaling_close_preserves_active_p2p_stream_and_other_player(self):
        host, guest, hosted, joined = await self.pair()
        survivor = await self.process()
        sn = await self.add(survivor)
        stable = await self.call(survivor, 'room.join', dict(node=sn, invite=hosted['invite']))
        stable = await self.wait_direct(survivor, stable)
        await self.exchange(joined, b'before-signal-close')
        identities = {peer.chat_id for peer in self.node.peers if peer.authenticated}
        session = next(row for row in self.rows(joined['session']) if row['event'] == 'player_connected')
        from tunnel.diagnostics import player_identity
        peer = next(peer for peer in self.node.peers
                    if peer.authenticated and player_identity(peer) == session['player'])
        reader, writer = await asyncio.open_connection('127.0.0.1', int(joined['address'].rsplit(':', 1)[1]))
        self.addCleanup(writer.close)
        writer.write(fixture.GAME_HANDSHAKE)
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(fixture.GAME_HANDSHAKE)), 5), fixture.GAME_HANDSHAKE)
        original_signal = peer.peer_signal
        peer.peer_signal.close()
        for _ in range(24):
            writer.write(b'kept-direct-stream')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(18), 5), b'kept-direct-stream')
            self.assertEqual((await self.state(guest, joined))['transport'], 'p2p')
            await asyncio.sleep(.25)
        self.assertIsNotNone(peer.peer_signal)
        self.assertIsNot(peer.peer_signal, original_signal)
        await self.exchange(joined, b'direct-after-signaling-close' * 4096)
        await self.exchange(stable, b'other-player-still-direct' * 4096)
        self.assertEqual((await self.state(survivor, stable))['transport'], 'p2p')
        self.assertEqual((await self.state(guest, joined))['address'], joined['address'])
        self.assertEqual(identities, {peer.chat_id for peer in self.node.peers if peer.authenticated})
        self.assertFalse(any(row['event'] == 'endpoint_disconnected' for row in self.rows(joined['session'])))
        self.assertTrue(any(row['event'] == 'peer_signal_closed' and row.get('p2p_preserved')
                            for row in self.rows(joined['session'])))
        self.assertFalse(any(row['event'] == 'peer_fallback' and row.get('reason') == 'signaling-closed'
                             for row in self.rows(joined['session'])))


@unittest.skipUnless(library.LIBRARY.is_file(), 'Build the native shared library first')
class LibraryPeerHeartbeatTests(PeerHeartbeatTests):
    async def process(self):
        self.bridge_command = [sys.executable, str(fixture.ROOT / 'tests/library_rpc_worker.py'), str(library.LIBRARY)]
        return await fixture.BridgeTests.process(self)


if __name__ == '__main__':
    unittest.main(verbosity=2)
