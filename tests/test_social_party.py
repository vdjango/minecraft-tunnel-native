"""Real signed party actions and multiple native invitees on a local relay."""
import asyncio
import base64
import json
from pathlib import Path
import secrets
import sys
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from cryptography.hazmat.primitives.asymmetric import ec
from tunnel.accounts import Accounts, decode
from tunnel.federation import Federation
from tests import test_bridge as fixture
from tests.account_fixture import enroll, sign


class PartyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    exchange = fixture.BridgeTests.exchange

    async def setup_accounts(self):
        fed = Federation(self.key, self.profile, network_key=secrets.token_urlsafe(48), name='Local party test')
        fed.relay, self.node.federation = self.node, fed
        self.node.account_authenticate = lambda value: fed.accounts.authenticate(value)
        fed.accounts = Accounts(fed, self.logs / 'accounts.json')
        fed.publish()
        self.addAsyncCleanup(fed.close)
        self.social = fed.accounts.social
        users = [enroll(fed.accounts, name, delegated=True) for name in ('party_host', 'party_alice', 'party_bob', 'stranger')]
        self.users = users
        for index, (account, _) in enumerate(users[1:3], 1):
            request = self.event(0, 'request', account)
            self.social.apply(users[0][0], request)
            self.social.apply(account, self.event(index, 'accept', users[0][0], ref=request['action']['id']))

    def event(self, index, operation, target, **fields):
        _, credential = self.users[index]
        envelope, scalar = credential.split('~')
        claims = json.loads(decode(envelope.split('.')[1]))
        key = ec.derive_private_key(int.from_bytes(decode(scalar), 'big'), ec.SECP256R1())
        # Windows clock ticks can repeat within the same direct test call burst.
        issued = max(time.time(), getattr(self, '_last_event_at', 0) + .001)
        self._last_event_at = issued
        action = dict(op=operation, target=target, at=issued, id=secrets.token_hex(16), **fields)
        return dict(grant=claims['g'], proof=claims['s'], action=action, signature=sign(key, action))

    def apply(self, index, operation, target, **fields):
        event = self.event(index, operation, target, **fields)
        return self.social.apply(self.users[index][0], event)

    async def native_user(self, index):
        process = await self.process()
        code = 'buggnode1-' + base64.urlsafe_b64encode(json.dumps(dict(self.profile, token=self.users[index][1])).encode()).decode().rstrip('=')
        node = (await self.call(process, 'node.add', dict(code=code)))['node']
        return process, node

    async def test_two_native_invitees_keep_both_invites_and_can_transfer_game_data(self):
        await self.setup_accounts()
        host, hn = await self.native_user(0)
        alice, an = await self.native_user(1)
        bob, bn = await self.native_user(2)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port, transport='relay'))
        # State publication uses generic signed native fields, no room secret.
        await self.call(host, 'social.operate', dict(node=hn, operation='presence', fields=dict(
            room=hosted['room'], room_role='host', room_title='Friends world', room_state='ready')))
        state = await self.call(alice, 'social.operate', dict(node=an, operation='presence'))
        self.assertTrue(state['friends'][0]['room']['can_request'])
        self.assertNotIn(hosted['invite'], json.dumps(state))
        for account in (self.users[1][0], self.users[2][0]):
            await self.call(host, 'social.operate', dict(node=hn, operation='invite', target=account, fields=dict(room=hosted['room'])))
        joined = []
        for process, node in ((alice, an), (bob, bn)):
            state = await self.call(process, 'social.operate', dict(node=node, operation='presence'))
            self.assertEqual(len(state['invitations']), 1)
            ticket = await self.call(process, 'social.operate', dict(node=node, operation='join', target=self.users[0][0], fields=dict(ref=state['invitations'][0]['id'])))
            joined.append(await self.call(process, 'room.join', dict(node=node, invite=ticket['invite'], transport='relay')))
        await asyncio.gather(*(self.exchange(connection, b'friends can play together' * 300) for connection in joined))
        self.assertEqual(len(self.social.invitations), 2)

    async def test_join_request_accept_decline_duplicate_and_expiry(self):
        await self.setup_accounts()
        host, alice = self.users[0][0], self.users[1][0]
        room_id = 'a' * 32
        room = Mock(owner=Mock(account=host), lobby=None, available=True, port=25565, secret='local-test-secret')
        self.node.rooms[room_id] = room
        self.addCleanup(self.node.rooms.pop, room_id, None)
        state = self.apply(1, 'request_join', host, room=room_id)
        request = state['joinRequests'][0]
        self.assertFalse(request['incoming'])
        self.assertTrue(self.social.state(host)['joinRequests'][0]['incoming'])
        with self.assertRaisesRegex(ValueError, '已申请'):
            self.apply(1, 'request_join', host, room=room_id)
        self.apply(0, 'decline_join', alice, room=room_id, ref=request['id'])
        self.assertFalse(self.social.state(host)['joinRequests'])
        self.assertEqual(self.social.state(alice)['joinRequests'][0]['status'], 'declined')
        request = self.apply(1, 'request_join', host, room=room_id)['joinRequests'][0]
        self.apply(0, 'accept_join', alice, room=room_id, ref=request['id'])
        self.assertFalse(self.social.state(host)['joinRequests'])
        self.assertFalse(self.social.state(alice)['joinRequests'])
        invite = self.social.state(alice)['invitations'][0]
        self.assertEqual(self.apply(1, 'join', host, ref=invite['id'])['invite'], 'bugg1-local-test-secret')
        with self.assertRaisesRegex(ValueError, '已处理'):
            self.apply(0, 'accept_join', alice, room=room_id, ref=request['id'])
        with self.assertRaisesRegex(ValueError, '已处理'):
            self.apply(0, 'decline_join', alice, room=room_id, ref=request['id'])
        # A fresh request after acceptance is independent and expires cleanly.
        request = self.apply(1, 'request_join', host, room=room_id)['joinRequests'][0]
        from unittest.mock import patch
        with patch('tunnel.social.time.time', return_value=time.time() + 121):
            self.assertFalse(self.social.state(host)['joinRequests'])
        with self.assertRaises(ValueError):
            self.apply(0, 'accept_join', alice, room=room_id, ref=request['id'])

    async def test_native_request_accept_and_join_returns_usable_invitation(self):
        await self.setup_accounts()
        host, hn = await self.native_user(0)
        guest, gn = await self.native_user(1)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port, transport='relay'))
        request = await self.call(guest, 'social.operate', dict(node=gn, operation='request_join',
                          target=self.users[0][0], fields=dict(room=hosted['room'])))
        request_id = request['joinRequests'][0]['id']
        accepted = await self.call(host, 'social.operate', dict(node=hn, operation='accept_join',
                          target=self.users[1][0], fields=dict(room=hosted['room'], ref=request_id)))
        self.assertFalse(accepted['joinRequests'])
        state = await self.call(guest, 'social.operate', dict(node=gn, operation='presence'))
        self.assertFalse(state['joinRequests'])
        ticket = await self.call(guest, 'social.operate', dict(node=gn, operation='join',
                          target=self.users[0][0], fields=dict(ref=state['invitations'][0]['id'])))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=ticket['invite'], transport='relay'))
        await self.exchange(joined, b'approved friend joined' * 100)

    async def test_join_requires_friendship_owner_live_room_and_no_forged_presence(self):
        await self.setup_accounts()
        host, alice = self.users[0][0], self.users[1][0]
        room_id = 'a' * 32
        with self.assertRaises(ValueError):
            self.apply(1, 'request_join', host, room=room_id)
        room = Mock(owner=Mock(account=alice), lobby=None, available=True, port=25565)
        self.node.rooms[room_id] = room
        self.addCleanup(self.node.rooms.pop, room_id, None)
        with self.assertRaises(ValueError):
            self.apply(1, 'request_join', host, room=room_id)
        room.owner.account, room.port = host, 0
        with self.assertRaises(ValueError):
            self.apply(1, 'request_join', host, room=room_id)
        room.port = 25565
        with self.assertRaisesRegex(ValueError, '双向好友'):
            self.apply(3, 'request_join', host, room=room_id)

    async def test_signed_network_profile_is_only_returned_to_online_friends(self):
        await self.setup_accounts()
        host, hn = await self.native_user(0)
        alice, an = await self.native_user(1)
        network = dict(type='direct_easier', direct_successes=3, relay_successes=1)
        await self.call(host, 'social.operate', dict(node=hn, operation='presence',
                         fields=dict(network_profile=network)))
        state = await self.call(alice, 'social.operate', dict(node=an, operation='presence'))
        self.assertEqual(state['friends'][0]['network_profile'], network)
        self.assertEqual(state['networkProfile'], None)
        stranger, sn = await self.native_user(3)
        state = await self.call(stranger, 'social.operate', dict(node=sn, operation='presence'))
        self.assertTrue(all('network_profile' not in friend for friend in state['friends']))
        with self.assertRaisesRegex(ValueError, 'Invalid network profile'):
            self.apply(0, 'presence', self.users[0][0], network_profile={
                'type': 'direct_easier', 'direct_successes': True, 'relay_successes': 0})

    async def test_private_room_join_request_routes_from_another_node(self):
        await self.setup_accounts()
        import asyncssh
        from tunnel.native_relay import RelayServer
        key = asyncssh.generate_private_key('ssh-ed25519')
        remote = await RelayServer('127.0.0.1', 0, host_key=key, token=secrets.token_urlsafe(48), workers=1).start()
        self.addAsyncCleanup(remote.close)
        profile = dict(server_host='127.0.0.1', server_port=remote.port, token=remote.token,
                       server_public_key=key.export_public_key().decode())
        origin = self.node.federation
        fed = Federation(key, profile, network_key=origin.network_key, name='Remote party node')
        fed.relay, remote.federation = remote, fed
        fed.accounts = Accounts(fed, self.logs / 'remote-accounts.json')
        remote.account_authenticate = lambda value: fed.accounts.authenticate(value)
        self.addAsyncCleanup(fed.close)
        fed.publish()
        for first, second in ((origin, fed), (fed, origin)):
            first.ingest([second.records[second.id]])
            first.seeds[second.id] = second.profile
        fed.accounts.replication.ingest(origin.accounts.replication.snapshot())
        fed.accounts.social.ingest(self.social.snapshot())
        host, hn = await self.native_user(0)
        guest = await self.process()
        code = 'buggnode1-' + base64.urlsafe_b64encode(json.dumps(dict(profile, token=self.users[1][1])).encode()).decode().rstrip('=')
        gn = (await self.call(guest, 'node.add', dict(code=code)))['node']
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port, transport='relay'))
        origin.publish()
        fed.ingest([origin.records[origin.id]])
        self.assertIsNotNone(fed.locate(hosted['room']))
        result = await self.call(guest, 'social.operate', dict(node=gn, operation='request_join',
                         target=self.users[0][0], fields=dict(room=hosted['room'])))
        request_id = result['joinRequests'][0]['id']
        self.assertTrue(self.social.state(self.users[0][0])['joinRequests'][0]['incoming'])
        await self.call(host, 'social.operate', dict(node=hn, operation='accept_join',
                         target=self.users[1][0], fields=dict(room=hosted['room'], ref=request_id)))
        fed.accounts.social.ingest(self.social.snapshot())
        invitation = fed.accounts.social.state(self.users[1][0])['invitations'][0]
        ticket = await self.call(guest, 'social.operate', dict(node=gn, operation='join',
                         target=self.users[0][0], fields=dict(ref=invitation['id'])))
        self.assertEqual(ticket['invite'], hosted['invite'])


if __name__ == '__main__':
    unittest.main()
