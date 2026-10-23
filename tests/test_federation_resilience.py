"""Real C++ nodes and bridges survive loss of the node which introduced them."""
import asyncio
from pathlib import Path
import secrets
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
import asyncssh
from tunnel.federation import Federation, TTL
from tunnel.native_relay import RelayServer
from tests import test_bridge as bridge_tests


class FederationResilienceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = bridge_tests.BridgeTests.asyncSetUp
    process = bridge_tests.BridgeTests.process
    call = bridge_tests.BridgeTests.call
    add = bridge_tests.BridgeTests.add
    exchange = bridge_tests.BridgeTests.exchange

    async def topology(self):
        relays = []
        for _ in range(2):
            key = asyncssh.generate_private_key('ssh-ed25519')
            relay = await RelayServer('127.0.0.1', 0, host_key=key,
                token=secrets.token_urlsafe(48), workers=1).start()
            self.addAsyncCleanup(relay.close)
            relays.append(relay)
        relays.append(self.node)
        network = secrets.token_urlsafe(48)
        feds = []
        for name, relay in zip(('B', 'A', 'C'), relays):
            key = relay.host_key
            fed = Federation(key, dict(server_host='127.0.0.1', server_port=relay.port,
                token=relay.token, server_public_key=key.export_public_key().decode()),
                network_key=network, name=name)
            fed.relay, relay.federation = relay, fed
            fed.publish()
            self.addAsyncCleanup(fed.close)
            feds.append(fed)
        b, a, c = feds
        a.join(b.invitation())
        c.join(a.invitation())
        for fed in feds:
            fed.task = asyncio.create_task(fed.run())
        async with asyncio.timeout(15):
            while any(len(fed.live()) != 3 for fed in feds):
                await asyncio.sleep(.05)
        # Each survivor must have independently learned and persisted the
        # other's pinned profile before the bootstrap node disappears.
        self.assertIn(b.id, c.seeds)
        self.assertIn(c.id, b.seeds)
        return relays, feds

    async def exercise_loss(self, graceful):
        relays, feds = await self.topology()
        b, a, c = feds
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', {'node':hn, 'game_port':self.port,
            'transport':'relay', 'settings':{'title':'surviving C room', 'public':True}})
        joined = await self.call(guest, 'room.join', {'node':gn, 'invite':hosted['invite'], 'transport':'relay'})
        reader, writer = await asyncio.open_connection('127.0.0.1', int(joined['address'].rsplit(':',1)[1]))
        self.addCleanup(writer.close)
        payload = secrets.token_bytes(65536)
        async def verify():
            writer.write(payload)
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 3), payload)
        await verify()
        await a.close()
        if graceful:
            # Leaving changes only A's local authority, not C or B membership.
            a.leave()
            await relays[1].close()
        else:
            relays[1].process.kill()
            async with asyncio.timeout(5):
                await relays[1].failed.wait()
        started = time.monotonic()
        blocks = 0
        deadline = started + TTL + 3
        while time.monotonic() < deadline:
            await verify()
            blocks += 1
            await asyncio.sleep(.02)
        self.assertNotIn(a.id, b.live())
        self.assertNotIn(a.id, c.live())
        self.assertIn(c.id, b.live())
        self.assertIn(b.id, c.live())
        self.assertEqual(b.locate(hosted['room']), c.profile)
        self.assertIn(hosted['room'], self.node.rooms)
        self.assertEqual((await self.call(host,'session.list'))['sessions'][0]['state'], 'online')
        await self.exchange(joined, b'new stream after bootstrap node loss')
        self.assertTrue(a.id in c.seeds and b.id in c.seeds)
        self.assertLessEqual(len(c.connections), 3)
        print('FEDERATION_FAILURE ' + str(dict(graceful=graceful, continuous_blocks=blocks,
              seconds=round(time.monotonic()-started,2), surviving_members=len(c.live()))), flush=True)
        writer.write_eof()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        writer.close()
        await writer.wait_closed()

    async def test_bootstrap_node_crash_does_not_disconnect_survivors(self):
        await self.exercise_loss(False)

    async def test_bootstrap_node_leave_does_not_disconnect_survivors(self):
        await self.exercise_loss(True)


if __name__ == '__main__':
    unittest.main()
