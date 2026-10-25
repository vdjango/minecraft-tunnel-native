"""A joined P2P room must survive more than one SCTP identifier range of churn."""
import asyncio
import json
import os
import socket
import struct
import sys
import time
import unittest

from tests import test_bridge as fixture
from tests import test_bridge_library as library


class PeerReconnectTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    add = fixture.BridgeTests.add
    exchange = fixture.BridgeTests.exchange
    wait_direct = fixture.BridgeTests.wait_direct

    async def test_status_churn_then_game_reset_reconnects_without_leaving_room(self):
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite']))
        joined = await self.wait_direct(guest, joined)
        self.assertEqual(joined['transport'], 'p2p')
        churn_started = time.time()
        port = int(joined['address'].rsplit(':', 1)[1])
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        try:
            # Keep the long-lived synthetic game socket behind the host's
            # Minecraft handshake gate before exercising stream churn.
            writer.write(fixture.GAME_HANDSHAKE)
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(
                reader.readexactly(len(fixture.GAME_HANDSHAKE)), 5), fixture.GAME_HANDSHAKE)
            for index in range(640):
                with self.subTest(connection=index):
                    await self.exchange(joined, b'status-probe:' + index.to_bytes(4, 'big'))
                if index % 16 == 0:
                    payload = b'existing-game-connection:' + index.to_bytes(4, 'big')
                    writer.write(payload)
                    await writer.drain()
                    self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 5), payload)
                if (index + 1) % 128 == 0:
                    print('P2P_RECONNECT_CHURN', type(self).__name__, index + 1, flush=True)
            # Simulate the game being killed/reset, while its bridge room stays joined.
            writer.get_extra_info('socket').setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                struct.pack('hh' if os.name == 'nt' else 'ii', 1, 0))
            writer.transport.abort()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            for _ in range(3):
                await self.exchange(joined, b'reentered-the-same-room' * 4096)
            state = (await self.call(guest, 'session.list'))['sessions']
            current = next(item for item in state if item['session'] == joined['session'])
            self.assertEqual(current['state'], 'online')
            self.assertEqual(current['address'], joined['address'])
            self.assertEqual(current['transport'], 'p2p')
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        await self.call(guest, 'room.close', dict(session=joined['session']))
        await self.call(host, 'room.close', dict(session=hosted['session']))
        if isinstance(guest, asyncio.subprocess.Process):
            # A silent relay fallback must not make an exhausted P2P peer pass.
            async with asyncio.timeout(5):
                while True:
                    rows = [json.loads(line)
                            for path in guest.player_logs.glob('*/' + joined['session'] + '.jsonl')
                            for line in path.read_text(encoding='utf-8').splitlines()]
                    paths = [row for row in rows if row['event'] == 'game_path' and row['at'] >= churn_started]
                    if len(paths) >= 644:
                        break
                    await asyncio.sleep(.05)
            # The bridge may also open background game-status probes.
            self.assertGreaterEqual(len(paths), 644)
            self.assertTrue(all(row['path'] == 'p2p' for row in paths))
            identifiers = [row['sctp_stream'] for row in paths]
            self.assertNotIn(None, identifiers)
            self.assertLess(len(set(identifiers)), len(identifiers))
            self.assertFalse(any(row['event'] == 'peer_stream_fallback' and row['at'] >= churn_started for row in rows))


@unittest.skipUnless(library.LIBRARY.is_file(), 'Build the native shared library first')
class LibraryPeerReconnectTests(PeerReconnectTests):
    async def process(self):
        self.bridge_command = [sys.executable, str(fixture.ROOT / 'tests/library_rpc_worker.py'), str(library.LIBRARY)]
        return await fixture.BridgeTests.process(self)


if __name__ == '__main__':
    unittest.main(verbosity=2)
