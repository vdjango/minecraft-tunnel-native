"""Late ICE answers from timed-out negotiations must not poison retries."""
import asyncio
import json
import unittest
from unittest.mock import patch

from tunnel.peer_signal import PeerSignalSession
from tests import test_bridge as fixture


class PeerSignalingAttemptTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    add = fixture.BridgeTests.add
    wait_direct = fixture.BridgeTests.wait_direct
    exchange = fixture.BridgeTests.exchange

    async def test_legacy_node_retries_without_attempt_field(self):
        original = PeerSignalSession.send
        dropped = False

        def legacy(session, value):
            nonlocal dropped
            value = dict(value)
            value.pop('attempt', None)
            if value.get('type') == 'answer' and not dropped:
                dropped = True
                return
            original(session, value)

        signal_patch = patch.object(PeerSignalSession, 'send', legacy)
        signal_patch.start()
        self.addCleanup(signal_patch.stop)
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite']))
        direct = await self.wait_direct(guest, joined)
        self.assertTrue(dropped)
        self.assertEqual(direct['transport'], 'p2p')
        await self.exchange(direct)
        rows = [json.loads(line)
                for path in guest.player_logs.glob('*/' + joined['session'] + '.jsonl')
                for line in path.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(row['event'] == 'peer_retry_started' for row in rows))
        self.assertTrue(any(row['event'] == 'peer_answer_received' and
                            row.get('legacy') for row in rows))

    async def test_late_answer_is_ignored_after_retry_connects(self):
        original = PeerSignalSession.send
        first_attempt = None

        def delayed(session, value):
            nonlocal first_attempt
            if value.get('type') == 'answer' and first_attempt is None:
                first_attempt = value.get('attempt')
                asyncio.get_running_loop().call_later(18, original, session, value)
                return
            original(session, value)

        signal_patch = patch.object(PeerSignalSession, 'send', delayed)
        signal_patch.start()
        self.addCleanup(signal_patch.stop)

        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite']))
        direct = await self.wait_direct(guest, joined)
        self.assertEqual(direct['transport'], 'p2p')
        self.assertTrue(first_attempt)

        deadline = asyncio.get_running_loop().time() + 6
        while asyncio.get_running_loop().time() < deadline:
            rows = [json.loads(line)
                    for path in guest.player_logs.glob('*/' + joined['session'] + '.jsonl')
                    for line in path.read_text(encoding='utf-8').splitlines()]
            ignored = [row for row in rows if row['event'] == 'peer_answer_ignored']
            if any(row.get('reason') == 'stale-attempt' and
                   row.get('attempt') == first_attempt for row in ignored):
                break
            await asyncio.sleep(0.1)
        else:
            self.fail(f'late answer was not rejected as stale: {rows}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
