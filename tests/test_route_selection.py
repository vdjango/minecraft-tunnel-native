"""Native RPC route selection against independent real SSH nodes."""
import asyncio
import base64
import json
import os
import secrets
import sys
import unittest
from unittest.mock import patch
from tests import test_bridge as fixtures
from tunnel.federation import Federation


class RouteSelectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.BridgeTests.asyncSetUp
    process = fixtures.BridgeTests.process
    call = fixtures.BridgeTests.call

    async def network(self):
        key = fixtures.asyncssh.generate_private_key('ssh-ed25519')
        other = await fixtures.RelayServer('127.0.0.1', 0, host_key=key,
            token=secrets.token_urlsafe(48), workers=1).start()
        self.addAsyncCleanup(other.close)
        secondary = dict(server_host='127.0.0.1', server_port=other.port,
            token=other.token, server_public_key=key.export_public_key().decode())
        network = secrets.token_urlsafe(48)
        federations = []
        for relay, hostkey, profile, name in [(self.node, self.key, self.profile, 'A'),
                                              (other, key, secondary, 'B')]:
            fed = Federation(hostkey, profile, network_key=network, name=name)
            relay.federation, fed.relay = fed, relay
            relay.capacity.configure(dict(bandwidth_mb_s=5))
            fed.publish()
            self.addAsyncCleanup(fed.close)
            federations.append(fed)
        for fed in federations:
            fed.ingest([f.records[f.id] for f in federations])
        return other, secondary

    def code(self):
        return 'buggnode1-' + base64.urlsafe_b64encode(json.dumps(self.profile).encode()).decode().rstrip('=')

    async def test_full_entry_selects_capacity_and_hosts_on_selected_node(self):
        other, _ = await self.network()
        self.node.capacity.configure(dict(bandwidth_mb_s=.001))
        bridge = await self.process()
        node = (await self.call(bridge, 'node.add', {'code': self.code()}))['node']
        selected = await self.call(bridge, 'node.selected', {'node': node})
        self.assertEqual(selected['name'], 'B')
        self.assertEqual(selected['profile']['server_port'], other.port)
        self.assertTrue(selected['accepting'])
        events = (await self.call(bridge, 'bridge.events'))['events']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['reason'], 'capacity_unavailable')
        self.assertEqual((await self.call(bridge, 'bridge.events'))['events'], [])
        hosted = await self.call(bridge, 'room.host', {'node': node, 'game_port': self.port, 'transport': 'relay'})
        for _ in range(20):
            if hosted['room'] in other.rooms:
                break
            await asyncio.sleep(.05)
        self.assertIn(hosted['room'], other.rooms)
        self.assertNotIn(hosted['room'], self.node.rooms)
        await self.call(bridge, 'room.close', {'session': hosted['session']})

    async def test_dead_imported_entry_bootstraps_using_pinned_backup(self):
        other, secondary = await self.network()
        await self.node.close()
        bridge = await self.process()
        node = (await self.call(bridge, 'node.add', {'code': self.code(), 'alternatives': [secondary]}))['node']
        selected = await self.call(bridge, 'node.selected', {'node': node})
        self.assertEqual(selected['profile']['server_port'], other.port)
        self.assertEqual((await self.call(bridge, 'node.request', {'node': node, 'request': {'op': 'ping'}}))['name'], 'B')

    async def test_personal_identity_survives_anonymous_topology_route_switch(self):
        from tunnel.accounts import Accounts
        from tests.account_fixture import enroll
        other, _ = await self.network()
        for index, relay in enumerate((self.node, other)):
            fed = relay.federation
            fed.accounts = Accounts(fed, self.logs / ('accounts-' + str(index) + '.json'))
            relay.account_authenticate = lambda token, fed=fed: fed.accounts.authenticate(token)
        primary, backup = self.node.federation.accounts, other.federation.accounts
        account, credential = enroll(primary, 'route_alice', delegated=True)
        target, _ = enroll(primary, 'route_bob')
        backup.replication.ingest(primary.replication.snapshot())
        self.node.capacity.configure(dict(bandwidth_mb_s=.001))
        self.profile['token'] = credential
        bridge = await self.process()
        result = await self.call(bridge, 'node.add', {'code': self.code()})
        self.assertEqual(result['identity'], 'account')
        selected = await self.call(bridge, 'node.selected', {'node': result['node']})
        self.assertEqual(selected['profile']['server_port'], other.port)
        self.assertEqual(selected['profile']['token'], credential)
        await self.call(bridge, 'social.operate', dict(node=result['node'], operation='request', target=target))
        state = await self.call(bridge, 'node.request', dict(node=result['node'], request={'op': 'social'}))
        self.assertEqual(state['account'], account)
        self.assertTrue(any(event['grant']['a'] == account and event['action']['target'] == target
                            for event in backup.social.snapshot()))

    async def test_room_list_latency_excludes_slow_directory_listing(self):
        from tunnel.directory import RoomControlSession as DirectorySession
        await self.network()
        bridge = await self.process()
        node = (await self.call(bridge, 'node.add', {'code': self.code()}))['node']
        original = DirectorySession.data_received
        def delayed(session, data, datatype):
            if b'"list"' in data:
                asyncio.get_running_loop().call_later(.35, original, session, data, datatype)
            else:
                original(session, data, datatype)
        with patch.object(DirectorySession, 'data_received', delayed):
            result = await self.call(bridge, 'room.list', {'node': node})
        self.assertEqual(result['node_latency_method'], 'application-echo')
        self.assertIsInstance(result['node_latency_ms'], (float, int))
        self.assertGreater(result['connect_ms'] - result['node_latency_ms'], 200)

    async def test_maintenance_bypasses_hold_down_but_does_not_close_existing_room(self):
        other, _ = await self.network()
        self.node.capacity.configure(dict(bandwidth_mb_s=.001))
        bridge = await self.process()
        node = (await self.call(bridge, 'node.add', {'code': self.code()}))['node']
        hosted = await self.call(bridge, 'room.host', {'node': node, 'game_port': self.port, 'transport': 'relay'})
        guest=await self.process()
        guest_node=(await self.call(guest,'node.add',{'code':self.code()}))['node']
        joined=await self.call(guest,'room.join',{'node':guest_node,'invite':hosted['invite'],'transport':'relay'})
        reader,writer=await asyncio.open_connection('127.0.0.1',int(joined['address'].rsplit(':',1)[1]))
        self.addAsyncCleanup(writer.wait_closed)
        self.addCleanup(writer.close)
        # Pass the production Minecraft handshake gate before testing routing.
        writer.write(fixtures.GAME_HANDSHAKE);await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(fixtures.GAME_HANDSHAKE)),5), fixtures.GAME_HANDSHAKE)
        writer.write(b'before route change');await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(19),5),b'before route change')
        self.node.capacity.configure(dict(bandwidth_mb_s=5))
        other.draining = True
        await self.call(bridge, 'node.routes', {'node': node, 'refresh': True})
        self.assertEqual((await self.call(bridge, 'node.selected', {'node': node}))['name'], 'A')
        sessions = (await self.call(bridge, 'session.list'))['sessions']
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]['session'], hosted['session'])
        self.assertIn(hosted['room'], other.rooms)
        writer.write(b'after route change');await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(18),5),b'after route change')
        writer.close();await writer.wait_closed()
        await self.call(guest,'room.close',{'session':joined['session']})
        await self.call(bridge, 'room.close', {'session': hosted['session']})

    async def test_network_full_keeps_directory_readable_and_rejects_host_admission(self):
        other, _ = await self.network()
        for relay in (self.node, other):
            relay.capacity.configure(dict(bandwidth_mb_s=.001))
        bridge = await self.process()
        node = (await self.call(bridge, 'node.add', {'code': self.code()}))['node']
        self.assertFalse((await self.call(bridge, 'node.selected', {'node': node}))['accepting'])
        self.assertIn('name', await self.call(bridge, 'node.request', {'node': node, 'request': {'op': 'ping'}}))
        bridge.stdin.write((json.dumps(dict(jsonrpc='2.0', id=17, method='room.host', params={'node': node})) + '\n').encode())
        await bridge.stdin.drain()
        result = json.loads(await asyncio.wait_for(bridge.stdout.readline(), 5))
        self.assertEqual(result['error']['code'], -32022)

    async def test_probe_set_deduplicates_pinned_profiles(self):
        _, secondary = await self.network()
        bridge = await self.process()
        node = (await self.call(bridge, 'node.add', {'code': self.code(), 'alternatives': [secondary] * 5}))['node']
        routes = (await self.call(bridge, 'node.routes', {'node': node}))['routes']
        self.assertEqual(len(routes), 2)

    async def test_cached_entry_failure_recovers_and_emits_one_tip(self):
        other, _ = await self.network()
        self.node.capacity.configure(dict(bandwidth_mb_s=.001))
        bridge = await self.process()
        node = (await self.call(bridge, 'node.add', {'code': self.code()}))['node']
        self.assertEqual((await self.call(bridge, 'node.selected', {'node': node}))['name'], 'B')
        await self.call(bridge, 'bridge.events')
        await other.close()
        result = await self.call(bridge, 'node.request', {'node': node, 'request': {'op': 'ping'}})
        self.assertEqual(result['name'], 'A')
        events = (await self.call(bridge, 'bridge.events'))['events']
        self.assertEqual([(event['from'], event['to'], event['reason']) for event in events],
                         [('B', 'A', 'unavailable')])
        self.assertEqual((await self.call(bridge, 'bridge.events'))['events'], [])

    @unittest.skipUnless(os.environ.get('BUGGCRAFT_LAUNCHER_SDK'),'Set BUGGCRAFT_LAUNCHER_SDK to exercise launcher integration')
    async def test_launcher_adapter_uses_real_native_choice_and_one_nonmodal_event(self):
        sys.path.insert(0,os.environ['BUGGCRAFT_LAUNCHER_SDK'])
        from hookup.native_routes import NativeRoutes
        from hookup.profiles import RelayProfile
        from hookup.nodes import remember
        other, secondary=await self.network()
        self.node.capacity.configure(dict(bandwidth_mb_s=.001))
        profile=RelayProfile(**self.profile)
        remember(profile,[secondary])
        tips=[]
        object.__setattr__(profile,'_route_notice',tips.append)
        manager=NativeRoutes()
        self.addAsyncCleanup(manager.close)
        ordered=await manager.choose(profile)
        self.assertEqual(ordered[0].server_port,other.port)
        self.assertEqual(tips,['B'])
        self.assertEqual(await manager.choose(profile),ordered)
        self.assertEqual(tips,['B'])
        self.assertIsNotNone(manager.process)
        self.assertIsNone(manager.process.returncode)
