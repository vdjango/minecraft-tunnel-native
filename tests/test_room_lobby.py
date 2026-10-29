"""Room preparation and management through real native core/bridge processes."""
import asyncio
import json
import time
import unittest
import asyncssh

from tests import test_bridge as fixture


class RoomLobbyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    add = fixture.BridgeTests.add
    exchange = fixture.BridgeTests.exchange

    async def request(self, process, session, op, **fields):
        return await self.call(process, 'room.request', dict(session=session, request=dict(op=op, **fields)))

    async def rejected(self, process, session, op, **fields):
        process.stdin.write((json.dumps(dict(jsonrpc='2.0', id=1, method='room.request',
            params=dict(session=session, request=dict(op=op, **fields)))) + '\n').encode())
        await process.stdin.drain()
        result = json.loads(await asyncio.wait_for(process.stdout.readline(), 35))
        self.assertIn('error', result, result)

    async def pair(self, port=0):
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=port,
            transport='relay', chat_name='Host', settings=dict(title='Preparation',
                public=True, game=dict(version='1.21.8', loader='vanilla'))))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite'],
            transport='relay', chat_name='Guest'))
        return host, guest, hosted, joined

    async def test_waiting_room_has_real_members_manifest_and_monotonic_preparation(self):
        host, guest, hosted, joined = await self.pair()
        self.assertEqual(joined['info']['availability'], 'waiting')
        snapshot = await self.request(host, hosted['session'], 'members')
        self.assertEqual(snapshot['online'], 2)
        self.assertEqual({m['name'] for m in snapshot['members']}, {'Host', 'Guest'})
        self.assertEqual(snapshot['ready'], 1)
        manifest = await self.call(guest, 'mods.manifest', dict(session=joined['session']))
        async with asyncio.timeout(10):
            while manifest['state'] not in ('complete', 'error'):
                await asyncio.sleep(.05)
                manifest = await self.call(guest, 'mods.status', dict(session=joined['session'], transfer=manifest['transfer']))
        self.assertEqual(manifest['state'], 'complete', manifest)
        fields = dict(config_id=snapshot['config_id'], seq=10, task='install-1',
            state='downloading', stage='assets', done=4096, total=8192, speed=1024)
        self.assertTrue((await self.request(guest, joined['session'], 'member_update', **fields))['accepted'])
        fields['seq'] = 9
        self.assertFalse((await self.request(guest, joined['session'], 'member_update', **fields))['accepted'])
        row = next(m for m in (await self.request(host, hosted['session'], 'members'))['members'] if m['name'] == 'Guest')
        self.assertEqual((row['stage'], row['done'], row['total']), ('assets', 4096, 8192))
        await self.rejected(guest, joined['session'], 'member_update', **dict(fields, seq=11, config_id='obsolete'))
        fields.update(seq=12, task='install-2', state='ready')
        await self.request(guest, joined['session'], 'member_update', **fields)
        self.assertFalse((await self.request(guest, joined['session'], 'member_update',
            **dict(fields, seq=13, task='install-1', state='failed')))['accepted'])

    async def test_transfer_requires_acceptance_and_preserves_real_game_streams(self):
        host, guest, hosted, joined = await self.pair(self.port)
        hs, gs = hosted['session'], joined['session']
        snapshot = await self.request(guest, gs, 'members')
        await self.rejected(guest, gs, 'member_kick', member=snapshot['manager_id'])
        offer = (await self.request(host, hs, 'owner_offer', member=snapshot['self_id']))['transfer']
        self.assertEqual((await self.request(guest, gs, 'members'))['manager_id'], snapshot['manager_id'])
        await self.request(guest, gs, 'owner_answer', offer=offer['id'], accept=False)
        offer = (await self.request(host, hs, 'owner_offer', member=snapshot['self_id']))['transfer']
        await self.request(guest, gs, 'owner_answer', offer=offer['id'], accept=True)
        await self.rejected(host, hs, 'close')
        await self.exchange(joined, b'world remains on original provider' * 2048)
        await self.request(guest, gs, 'configure', title='New manager')
        self.assertEqual((await self.request(host, hs, 'describe'))['title'], 'New manager')
        self.assertEqual((await self.request(host, hs, 'describe'))['game']['version'], '1.21.8')
        await self.call(host, 'room.close', dict(session=hs))
        description = await self.request(guest, gs, 'describe')
        self.assertEqual(description['availability'], 'waiting')
        self.assertEqual((await self.request(guest, gs, 'members'))['manager_id'], snapshot['self_id'])

    async def test_kick_disconnects_target_and_rejects_same_identity_without_affecting_others(self):
        host, guest, hosted, joined = await self.pair()
        hs, gs = hosted['session'], joined['session']
        snapshot = await self.request(guest, gs, 'members')
        target = snapshot['self_id']
        peer = next(p for p in self.node.peers if p.member_id == target)
        await self.request(host, hs, 'member_kick', member=target)
        self.assertFalse(peer.authenticated)
        async with asyncio.timeout(8):
            while not peer.conn.is_closed():
                await asyncio.sleep(.05)
        state = await self.request(host, hs, 'members')
        self.assertEqual(state['online'], 1)
        self.assertEqual(state['blocked'][0]['id'], target)
        self.assertFalse(next(m for m in state['members'] if m['id'] == target)['online'])
        await self.request(host, hs, 'member_unblock', member=target)
        self.assertEqual((await self.request(host, hs, 'members'))['blocked'], [])

    async def test_install_log_access_requires_player_consent_and_is_scoped_revocable_and_redacted(self):
        host, guest, hosted, joined = await self.pair()
        hs, gs = hosted['session'], joined['session']
        target = (await self.request(guest, gs, 'members'))['self_id']
        other = await self.process()
        on = await self.add(other)
        os = (await self.call(other, 'room.join', dict(node=on, invite=hosted['invite'], transport='relay')))['session']
        await self.rejected(host, hs, 'logs_read', member=target)
        await self.rejected(guest, gs, 'logs_append', entries=[])
        await self.rejected(other, os, 'logs_request', member=target)
        await self.request(host, hs, 'logs_request', member=target)
        request = (await self.request(guest, gs, 'members'))['logs']['request']
        await self.rejected(other, os, 'logs_answer', request=request['id'], allow=True)
        await self.request(guest, gs, 'logs_answer', request=request['id'], allow=False)
        await self.rejected(host, hs, 'logs_read', member=target)
        await self.request(host, hs, 'logs_request', member=target)
        request = (await self.request(guest, gs, 'members'))['logs']['request']
        await self.request(guest, gs, 'logs_answer', request=request['id'], allow=True)
        text = r'sodium.jar C:\Users\Alice\mods\private.jar password=secret https://example.test/private buggnode1-private'
        await self.rejected(guest, gs, 'logs_append', entries=[
            dict(seq=0, text='Must not partially publish', category='mods', level='info'),
            dict(seq=1, text='Invalid batch', category='unknown', level='info')])
        self.assertEqual((await self.request(host, hs, 'logs_read', member=target))['entries'], [])
        rows = [dict(seq=i, text=text, category='mods', level='error') for i in range(20)]
        for offset in (0, 10):
            await self.request(guest, gs, 'logs_append', entries=rows[offset:offset+10])
        first = await self.request(host, hs, 'logs_read', member=target)
        self.assertEqual(len(first['entries']), 16)
        second = await self.request(host, hs, 'logs_read', member=target, offset=first['next'])
        self.assertEqual(len(second['entries']), 4)
        for row in first['entries']:
            self.assertIn('sodium.jar', row['text'])
            for value in ('Alice', 'secret', 'example.test', 'buggnode1-private'):
                self.assertNotIn(value, row['text'])
        await self.rejected(other, os, 'logs_read', member=target)
        self.assertNotIn(target, (await self.request(other, os, 'members'))['logs']['allowed'])
        await self.request(guest, gs, 'logs_revoke')
        await self.rejected(host, hs, 'logs_read', member=target)
        self.assertFalse(self.node.rooms[hosted['room']].lobby.logs.logs)

    async def test_transferring_manager_discards_previous_log_permissions(self):
        host, guest, hosted, joined = await self.pair()
        hs, gs = hosted['session'], joined['session']
        target = (await self.request(guest, gs, 'members'))['self_id']
        await self.request(host, hs, 'logs_request', member=target)
        request = (await self.request(guest, gs, 'members'))['logs']['request']
        await self.request(guest, gs, 'logs_answer', request=request['id'], allow=True)
        offer = (await self.request(host, hs, 'owner_offer', member=target))['transfer']
        await self.request(guest, gs, 'owner_answer', offer=offer['id'], accept=True)
        self.assertEqual((await self.request(guest, gs, 'members'))['logs']['allowed'], [])
        await self.rejected(host, hs, 'logs_read', member=target)

    async def test_kicked_resume_identity_cannot_reauthenticate_until_unblocked(self):
        from tunnel.room_lobby import identity
        host, _, hosted, _ = await self.pair()
        hs, room = hosted['session'], hosted['room']
        proof = 'known-private-test-proof-1234567890'
        target = identity(room, proof)
        credentials = json.dumps(dict(token=self.node.token, secret=self.node.rooms[room].secret, member_token=proof))
        async def connect():
            return await asyncssh.connect('127.0.0.1', port=self.node.port,
                username='client:' + room, password=credentials, known_hosts=None)
        connection = await connect()
        self.addAsyncCleanup(connection.wait_closed)
        self.addCleanup(connection.close)
        self.assertEqual((await self.request(host, hs, 'members'))['online'], 3)
        await self.request(host, hs, 'member_kick', member=target)
        await asyncio.wait_for(connection.wait_closed(), 5)
        with self.assertRaises(asyncssh.PermissionDenied):
            await connect()
        await self.request(host, hs, 'member_unblock', member=target)
        resumed = await connect()
        self.addAsyncCleanup(resumed.wait_closed)
        self.addCleanup(resumed.close)
        self.assertEqual((await self.request(host, hs, 'members'))['online'], 3)
