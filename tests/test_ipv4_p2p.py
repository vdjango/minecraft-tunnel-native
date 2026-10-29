"""Force IPv4 and require actual game data, not only successful signaling."""
import asyncio
import json
import unittest
from tests import test_bridge as fixture


class IPv4P2PTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    add = fixture.BridgeTests.add
    wait_direct = fixture.BridgeTests.wait_direct
    exchange = fixture.BridgeTests.exchange

    async def test_ipv4_selected_pair_carries_bidirectional_game_data(self):
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', {
            'node': hn, 'game_port': self.port, 'ice_family': 'ipv4'})
        joined = await self.call(guest, 'room.join', {
            'node': gn, 'invite': hosted['invite'], 'ice_family': 'ipv4'})
        joined = await self.wait_direct(guest, joined)
        await self.exchange(joined)
        await asyncio.sleep(.2)
        await self.call(guest, 'bridge.diagnostics')
        rows = [json.loads(line) for path in guest.player_logs.glob('*/*.jsonl')
                for line in path.read_text(encoding='utf-8').splitlines()]
        selected = [row for row in rows if row['event'] == 'peer_selected_pair']
        self.assertTrue(selected, 'No selected candidate pair was journaled')
        for row in selected:
            self.assertEqual(row['local_endpoint'][0]['family'], 4)
            self.assertEqual(row['remote_endpoint'][0]['family'], 4)
        self.assertTrue(any(row['event'] == 'game_path' and row['path'] == 'p2p' for row in rows))
        self.assertTrue(any(row['event'] == 'stream_closed' and row['sent_bytes'] >= 1024 * 1024
                            and row['received_bytes'] >= 1024 * 1024 for row in rows))
