"""Seventeen real native nodes: long discovery chain, H crash, Q joins A directly."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
import asyncssh
from tunnel.accounts import Accounts
from tunnel.federation import Federation, TTL
from tunnel.federation_schedule import FANOUT
from tunnel.native_relay import RelayServer
from tests.account_fixture import enroll
from tests import test_bridge as bridge_tests

NAMES = ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'Z', 'K', 'M', 'L', 'N', 'O', 'P', 'Q')


class FederationChainTests(unittest.IsolatedAsyncioTestCase):
    names = NAMES
    middle = 7
    asyncSetUp = bridge_tests.BridgeTests.asyncSetUp
    process = bridge_tests.BridgeTests.process
    call = bridge_tests.BridgeTests.call
    add = bridge_tests.BridgeTests.add
    exchange = bridge_tests.BridgeTests.exchange

    async def test_middle_crash_keeps_direct_lookup_and_new_room_join(self):
        # Seventeen SSH management servers share this one test event loop.
        # Avoid unittest's per-callback debug tracing distorting scale timing.
        asyncio.get_running_loop().set_debug(False)
        relays, feds = [self.node], []
        network = secrets.token_urlsafe(48)
        started = time.monotonic()
        for _ in self.names[1:]:
            key = asyncssh.generate_private_key('ssh-ed25519')
            relay = await RelayServer('127.0.0.1', 0, host_key=key,
                token=secrets.token_urlsafe(48), workers=1).start()
            self.addAsyncCleanup(relay.close)
            relays.append(relay)
        for name, relay in zip(self.names, relays):
            key = relay.host_key
            fed = Federation(key, dict(server_host='127.0.0.1', server_port=relay.port,
                token=relay.token, server_public_key=key.export_public_key().decode()),
                network_key=network, name=name)
            fed.relay, relay.federation = relay, fed
            folder = self.logs / ('node-' + name)
            folder.mkdir()
            fed.accounts = Accounts(fed, folder / 'accounts.json')
            relay.account_authenticate = fed.accounts.authenticate
            fed.publish()
            feds.append(fed)
            self.addAsyncCleanup(fed.close)
        # Exact invitation direction from the reported topology. H is the
        # initial seed in the middle; A and Q do not initially know each other.
        for index, fed in enumerate(feds):
            if index != self.middle:
                fed.join(feds[index + 1 if index < self.middle else index - 1].invitation())
        self.assertNotIn(feds[-1].id, feds[0].seeds)
        self.assertNotIn(feds[0].id, feds[-1].seeds)
        a_account, a_token = enroll(feds[0].accounts, 'chain_player_A')
        q_account, q_token = enroll(feds[-1].accounts, 'chain_player_Q')
        for fed in feds:
            fed.task = asyncio.create_task(fed.run())
        async with asyncio.timeout(180):
            while (any(len(f.live()) != len(feds) for f in feds) or
                   any(a_account not in f.accounts.replication.directory or
                       q_account not in f.accounts.replication.directory for f in feds)):
                await asyncio.sleep(.1)
        convergence = time.monotonic() - started
        print('CHAIN_CONVERGED ' + json.dumps(dict(nodes=len(feds), seconds=round(convergence, 3))), flush=True)
        a, h, q = feds[0], feds[self.middle], feds[-1]
        self.assertIn(a.id, q.seeds)
        self.assertIn(q.id, a.seeds)
        host, guest = await self.process(), await self.process()
        self.profile = dict(a.profile, token=a_token)
        hn = await self.add(host)
        self.profile = dict(q.profile, token=q_token)
        gn = await self.add(guest)
        fallback_guest = await self.process()
        self.profile = dict(h.profile, token=q_token)
        fallback_node = await self.add(fallback_guest)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port,
            transport='relay', settings=dict(title='A before H loss', public=True)))
        async with asyncio.timeout(30):
            while q.locate(hosted['room']) != a.profile:
                await asyncio.sleep(.05)
        before = await self.call(guest, 'node.request', dict(node=gn, request=dict(op='locate', id=hosted['room'])))
        self.assertEqual(before['profile']['server_port'], a.profile['server_port'])
        self.assertNotEqual(before['profile']['token'], a.profile['token'])
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite'], transport='relay'))
        self.assertFalse(relays[-1].rooms, 'Q must not create an intermediate relay room')
        self.assertEqual(self.node.capacity.members(hosted['room']), 2)
        reader, writer = await asyncio.open_connection('127.0.0.1', int(joined['address'].rsplit(':', 1)[1]))
        self.addCleanup(writer.close)
        payload = secrets.token_bytes(65536)
        latencies = []

        async def block():
            before = time.perf_counter()
            writer.write(payload)
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 3), payload)
            latencies.append((time.perf_counter() - before) * 1000)

        await block()
        await h.close()
        relays[self.middle].process.kill()
        async with asyncio.timeout(5):
            await relays[self.middle].failed.wait()
        failed_at = time.monotonic()
        active = [f for f in feds if f is not h]
        samples = []
        while time.monotonic() - failed_at < TTL + 5:
            await block()
            self.assertTrue(all(f.task and not f.task.done() for f in active))
            self.assertTrue(all(len(f.inflight) <= FANOUT and len(f.connections) <= FANOUT for f in active))
            samples.append(min(len(f.live()) for f in active))
            await asyncio.sleep(.02)
        expected = {f.id for f in active}
        for fed in active:
            self.assertEqual(set(fed.live()), expected, fed.name)
            self.assertEqual(len(fed.seeds), len(feds) - 1, fed.name)
        timings = []
        probes_before = a.account_lookup.stats['probes'] + q.account_lookup.stats['probes']
        for process, node, identifier, account in ((host, hn, 'CHAIN_PLAYER_Q', q_account),
                                                 (guest, gn, 'CHAIN_PLAYER_A', a_account)):
            for _ in range(4):
                tick = time.perf_counter()
                result = await self.call(process, 'node.request', dict(node=node,
                    request=dict(op='account_lookup', identifier=identifier)))
                timings.append((time.perf_counter() - tick) * 1000)
                self.assertEqual([r['account'] for r in result['accounts']], [account])
        self.assertEqual(a.account_lookup.stats['probes'] + q.account_lookup.stats['probes'], probes_before)
        # A new room must propagate after H is gone, not just remain cached.
        fresh = await self.call(host, 'room.host', dict(node=hn, game_port=self.port,
            transport='relay', settings=dict(title='A after H loss', public=True)))
        async with asyncio.timeout(20):
            while any(f.locate(fresh['room']) != a.profile or not f.public_room(fresh['room'])
                      for f in active if f is not a):
                await asyncio.sleep(.05)
        referred = await self.call(guest, 'node.request', dict(node=gn,
            request=dict(op='resolve', id=fresh['room'])))
        self.assertEqual(referred['profile']['server_port'], a.profile['server_port'])
        new_joined = await self.call(guest, 'room.join', dict(node=gn, invite=fresh['invite'], transport='relay'))
        await self.exchange(new_joined, b'new Q to A stream after H loss' * 8192)
        fallback_started = time.perf_counter()
        fallback_rooms = await self.call(fallback_guest, 'room.list', dict(node=fallback_node))
        fallback_seconds = time.perf_counter() - fallback_started
        self.assertIn(fresh['room'], [r['id'] for r in fallback_rooms['rooms']])
        fallback_started = time.perf_counter()
        fallback_joined = await self.call(fallback_guest, 'room.join', dict(node=fallback_node,
            invite=fresh['invite'], transport='relay'))
        after_failover_join_seconds = time.perf_counter() - fallback_started
        await self.exchange(fallback_joined, b'cached alternate directory survives selected H loss' * 4096)
        runtime_log = fallback_guest.player_logs / 'bridge' / 'runtime.jsonl'
        events = [json.loads(line) for line in runtime_log.read_text(encoding='utf-8').splitlines()]
        # A fresh native probe can replace H before the directory request;
        # both proactive selection and an in-request fallback are recoveries.
        self.assertTrue(any(r['event'] == 'directory_failover' or
                            r['event'] == 'node_route_changed' and r.get('reason') == 'unavailable'
                            for r in events))
        selected=await self.call(fallback_guest,'node.selected',dict(node=fallback_node))
        self.assertNotEqual(selected['profile']['server_port'],h.profile['server_port'])
        self.assertFalse(any(r['event'] == 'room_directory_unavailable' for r in events),
                         'A successful directory failover must be retained for subsequent joins')
        self.assertFalse(relays[-1].rooms)
        await block()
        writer.write_eof()
        self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'')
        writer.close()
        await writer.wait_closed()
        ordered = sorted(latencies)
        topology = '->'.join(self.names[:self.middle+1]) + '<-' + '<-'.join(self.names[self.middle+1:])
        report = dict(passed=True, topology=topology,
            nodes=len(feds), failed=self.names[self.middle], survivors=len(active), convergence_seconds=round(convergence, 3),
            failure_observation_seconds=round(time.monotonic()-failed_at, 3),
            continuous_blocks=len(latencies), continuous_bytes=len(latencies)*len(payload),
            block_rtt_p99_ms=round(ordered[min(len(ordered)-1, int(len(ordered)*.99))], 3),
            block_rtt_max_ms=round(max(ordered), 3),
            friend_lookup_wire_max_ms=round(max(timings), 3), friend_remote_probes=0,
            new_room_join_after_failure=True, owner_referral='Q directly to A',
            failed_selected_directory_fallback=True,
            initial_failed_directory_fallback_seconds=round(fallback_seconds, 3),
            subsequent_join_seconds=round(after_failover_join_seconds, 3),
            fanout_limit=FANOUT, min_live_during_lease=min(samples),
            scope='Real C++ cores and bridges on one host; echo TCP payload, not a Minecraft client')
        destination = Path(os.environ.get('BUGGCRAFT_CHAIN_REPORT', self.logs/'chain.json'))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('FEDERATION_CHAIN ' + json.dumps(report), flush=True)


@unittest.skipUnless(os.environ.get('BUGGCRAFT_FEDERATION_SCALE') == '1', 'Opt in to 65 real native nodes')
class FederationLargeChainTests(FederationChainTests):
    names = ('A',) + tuple(f'S{i:02d}' for i in range(1, 32)) + ('H',) + tuple(f'S{i:02d}' for i in range(33, 64)) + ('Q',)
    middle = 32


if __name__ == '__main__':
    unittest.main()
