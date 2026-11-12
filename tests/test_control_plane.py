"""Pinned enrollment, durable social recovery and real native data-plane isolation."""
import asyncio
import base64
import json
from pathlib import Path
import secrets
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
import asyncssh
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import ec
from tunnel.accounts import decode, encode
from tunnel.control_plane import ControlPlane
from tunnel.federation import Federation, canonical
from tunnel.native_relay import RelayServer
from tunnel.portal import Portal, atomic_json, password_hash
from tunnel.social import Social
from tunnel.social_delivery import retry_pending, submit
from tests import test_bridge as fixture
from tests.account_fixture import enroll, sign
from tests import test_social_party as party_fixture


class ControlTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    exchange = fixture.BridgeTests.exchange
    event = party_fixture.PartyTests.event

    async def portal(self, name, relay=None):
        if relay is None:
            relay = await RelayServer('127.0.0.1', 0, host_key=asyncssh.generate_private_key('ssh-ed25519'),
                                       token=secrets.token_urlsafe(48), workers=1).start()
            self.addAsyncCleanup(relay.close)
        profile = dict(server_host='127.0.0.1', server_port=relay.port, token=relay.token,
                       server_public_key=relay.host_key.export_public_key().decode())
        fed = Federation(relay.host_key, profile, network_key=secrets.token_urlsafe(48), name=name)
        fed.relay, relay.federation = relay, fed
        self.addAsyncCleanup(fed.close)
        folder = self.logs / name
        folder.mkdir()
        state = folder / 'node-state.json'
        atomic_json(state, {'admin_hash': password_hash('correct-test-password'), 'registration_open': True})
        portal = Portal(relay, fed, state)
        fed.publish()
        self.addAsyncCleanup(portal.control.close)
        return portal

    async def center(self, secure=False):
        portal = await self.portal('center', self.node)
        portal.control.configure({'mode': 'center'}, portal.path.parent)
        options = {}
        if secure:
            from tunnel.onboarding import setup_tls
            folder = portal.path.parent / 'test-tls'
            folder.mkdir()
            options['ssl'], self.certificate_pin = setup_tls(folder)
        server = TestServer(portal.app)
        client_options = {}
        if secure:
            from aiohttp import TCPConnector, Fingerprint
            client_options['connector'] = TCPConnector(ssl=Fingerprint(bytes.fromhex(self.certificate_pin)))
        client = TestClient(server, cookie_jar=CookieJar(unsafe=True), **client_options)
        self.addAsyncCleanup(client.close)
        await server.start_server(**options)
        await client.start_server()
        login = await client.post('/api/login', json={'username': 'admin', 'password': 'correct-test-password'})
        self.assertEqual(login.status, 200)
        self.csrf = {'X-CSRF-Token': (await login.json())['csrf']}
        return portal, client

    async def ticket(self, client):
        response = await client.post('/api/control/invitations', json={}, headers=self.csrf)
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def edge(self, name, bundle):
        portal = await self.portal(name)
        path = portal.path.with_name('enrollment.json')
        atomic_json(path, bundle)
        portal.control.configure({'mode': 'edge', 'enrollment_file': path.name}, path.parent)
        await portal.control.bootstrap()
        await portal.federation.exchange(bundle['center'])
        return portal

    async def native_user(self, fed, credential):
        process = await self.process()
        code = 'buggnode1-' + base64.urlsafe_b64encode(json.dumps(dict(fed.profile, token=credential)).encode()).decode().rstrip('=')
        node = (await self.call(process, 'node.add', {'code': code}))['node']
        return process, node

    async def test_enrollment_auth_pin_single_use_and_persisted_restart(self):
        center, client = await self.center()
        denied = await client.post('/api/control/invitations', json={})
        self.assertEqual(denied.status, 403)
        bundle = await self.ticket(client)
        self.assertNotIn('network_key', bundle)
        edge = await self.edge('edge', bundle)
        self.assertEqual(edge.federation.network_key, center.federation.network_key)
        self.assertIn(edge.federation.id, center.state['control_members'])
        # A lost enrollment reply may be recovered only by the same bound profile.
        edge.control.bundle = bundle
        await edge.control.bootstrap()
        resumed = ControlPlane(edge)
        edge.path.with_name('enrollment.json').unlink()
        resumed.configure({'mode': 'edge'}, edge.path.parent)
        self.assertEqual(resumed.profiles(), [center.federation.profile])
        other = await self.portal('other')
        other.control.bundle = bundle
        with self.assertRaisesRegex(ValueError, 'Enrollment rejected'):
            await other.control.bootstrap()
        forged = dict(bundle, center=dict(bundle['center'], server_public_key=other.federation.profile['server_public_key']))
        other.control.bundle = forged
        with self.assertRaises(asyncssh.HostKeyNotVerifiable):
            await other.control.bootstrap()
        self.assertNotIn('control_centers', other.state)
        status = await client.get('/api/control/status')
        self.assertEqual((await status.json())['members'], 1)
        client.session.cookie_jar.clear()
        self.assertEqual((await client.get('/api/control/status')).status, 401)

    async def test_center_invites_cross_nodes_and_existing_game_survives_center_crash(self):
        center, client = await self.center()
        first = await self.edge('first', await self.ticket(client))
        second = await self.edge('second', await self.ticket(client))
        self.users = [enroll(p.federation.accounts, name, delegated=True)
                      for p, name in ((first, 'host_user'), (second, 'guest_user'))]
        for p in (first, second):
            await center.federation.exchange(p.federation.profile)
        for p in (first, second):
            await p.federation.exchange(center.federation.profile)
        host, hn = await self.native_user(first.federation, self.users[0][1])
        guest, gn = await self.native_user(second.federation, self.users[1][1])
        request = await self.call(host, 'social.operate', dict(node=hn, operation='request', target=self.users[1][0]))
        self.assertEqual(request['delivery']['state'], 'accepted')
        pending = await self.call(guest, 'node.request', dict(node=gn, request={'op': 'social'}))
        request_id = pending['requests'][0]['id']
        await self.call(guest, 'social.operate', dict(node=gn, operation='accept', target=self.users[0][0], fields=dict(ref=request_id)))
        self.assertTrue(center.federation.accounts.social.friends(self.users[0][0], self.users[1][0]))
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port, transport='relay'))
        first.federation.publish()
        await first.federation.exchange(center.federation.profile)
        await self.call(host, 'social.operate', dict(node=hn, operation='invite', target=self.users[1][0], fields=dict(room=hosted['room'])))
        state = await self.call(guest, 'node.request', dict(node=gn, request={'op': 'social'}))
        restored = Social(center.federation.accounts)
        self.assertEqual(restored.state(self.users[1][0])['invitations'], state['invitations'])
        invitation = await self.call(guest, 'social.operate', dict(node=gn, operation='join', target=self.users[0][0], fields=dict(ref=state['invitations'][0]['id'])))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=invitation['invite'], transport='relay'))
        await self.exchange(joined)
        self.node.process.kill()
        await asyncio.wait_for(self.node.failed.wait(), 5)
        # Real payload and EOF use the room's edge, without the failed center.
        await self.exchange(joined, secrets.token_bytes(1024 * 1024))

    async def test_outbox_survives_restart_then_retries_once_and_expires(self):
        center, client = await self.center()
        edge = await self.edge('edge', await self.ticket(client))
        fed = edge.federation
        self.users = [enroll(fed.accounts, name, delegated=True) for name in ('alice', 'bob')]
        await center.federation.exchange(fed.profile)
        event = self.event(0, 'request', self.users[1][0])
        with patch.object(fed, 'account_rpc', AsyncMock(side_effect=OSError('test disconnect'))):
            result = await submit(fed, self.users[0][0], event)
        self.assertEqual(result['delivery']['state'], 'pending')
        fed.accounts.social = Social(fed.accounts)
        self.assertIn(event['action']['id'], fed.accounts.social.pending)
        await retry_pending(fed)
        self.assertFalse(fed.accounts.social.pending)
        await submit(fed, self.users[0][0], event)
        self.assertEqual(len(center.federation.accounts.social.edges), 1)
        later = self.event(1, 'request', self.users[0][0])
        fed.accounts.social.queue(later)
        with patch('tunnel.social.time.time', return_value=time.time() + 301):
            await retry_pending(fed)
        self.assertFalse(fed.accounts.social.pending)

    async def test_expired_forged_tickets_and_failed_durable_commit_are_rejected(self):
        center, client = await self.center()
        bundle = await self.ticket(client)
        edge = await self.portal('edge')
        request = dict(token=bundle['token'], profile=edge.federation.profile)
        request['signature'] = base64.b64encode(edge.federation.key.sign(canonical(request), b'ssh-ed25519')).decode()
        with patch.object(center, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                center.control.enroll(request)
        self.assertFalse(center.state['control_members'])
        with patch('tunnel.control_plane.time.time', return_value=time.time() + 601):
            with self.assertRaisesRegex(ValueError, 'expired'):
                center.control.enroll(request)
        request['signature'] = base64.b64encode(center.federation.key.sign(canonical({k: v for k, v in request.items() if k != 'signature'}), b'ssh-ed25519')).decode()
        with self.assertRaisesRegex(ValueError, 'signature'):
            center.control.enroll(request)

    async def test_two_devices_logout_and_stale_presence_becomes_unknown(self):
        center, _ = await self.center()
        store = center.federation.accounts
        actor, credential, master = enroll(store, 'alice', delegated=True, return_key=True)
        other = enroll(store, 'bob', delegated=True)
        self.users = [(actor, credential), other]
        request = self.event(0, 'request', other[0])
        store.social.apply(actor, request)
        store.social.apply(other[0], self.event(1, 'accept', actor, ref=request['action']['id']))
        store.social.apply(actor, self.event(0, 'presence', actor))
        delegate = ec.generate_private_key(ec.SECP256R1())
        numbers = delegate.public_key().public_numbers()
        public = dict(kty='EC', crv='P-256', x=encode(numbers.x.to_bytes(32, 'big')),
                      y=encode(numbers.y.to_bytes(32, 'big')))
        challenge = store.challenge('login', actor, 'https://test.invalid', public)
        grant = json.loads(challenge['message'])
        second = store.credential(actor, (grant, sign(master, grant))) + '~' + encode(delegate.private_numbers().private_value.to_bytes(32, 'big'))
        self.users[0] = (actor, second)
        store.social.apply(actor, self.event(0, 'presence', actor, status='offline'))
        self.assertEqual(store.social.state(other[0])['friends'][0]['presenceState'], 'online')
        self.users[0] = (actor, credential)
        store.social.apply(actor, self.event(0, 'presence', actor, status='offline'))
        self.assertEqual(store.social.state(other[0])['friends'][0]['presenceState'], 'offline')
        with patch('tunnel.social.time.time', return_value=time.time() + 21):
            self.assertEqual(store.social.state(other[0])['friends'][0]['presenceState'], 'unknown')
            self.assertFalse(store.social.snapshot(True))
        # Presence is never resurrected from disk on a process restart.
        self.assertFalse(Social(store).presence)

    async def test_outbox_write_failure_never_claims_acceptance(self):
        center, _ = await self.center()
        store = center.federation.accounts
        self.users = [enroll(store, name, delegated=True) for name in ('alice', 'bob')]
        event = self.event(0, 'request', self.users[1][0])
        with patch('tunnel.portal.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                await submit(center.federation, self.users[0][0], event)
        self.assertFalse(store.social.pending)
        self.assertFalse(store.social.edges)

    async def test_admin_tool_uses_real_https_csrf_and_certificate_pin(self):
        from scripts.control_admin import administer
        import aiohttp
        center, client = await self.center(secure=True)
        origin = str(client.make_url('/')).rstrip('/')
        result = await administer(origin, 'admin', 'correct-test-password', fingerprint=self.certificate_pin)
        self.assertEqual(result['version'], 1)
        status = await administer(origin, 'admin', 'correct-test-password', fingerprint=self.certificate_pin, status=True)
        self.assertEqual(status['mode'], 'center')
        with self.assertRaises(aiohttp.ServerFingerprintMismatch):
            await administer(origin, 'admin', 'correct-test-password', fingerprint='00' * 32)
        with self.assertRaises(ValueError):
            await administer(origin.replace('https:', 'http:'), 'admin', 'correct-test-password')
        for endpoint in ('/api/federation/join', '/api/federation/leave'):
            response = await client.post(endpoint, json={}, headers=self.csrf)
            self.assertEqual(response.status, 400)

    async def test_center_scheduler_is_pinned_and_unadmitted_members_are_ignored(self):
        center, client = await self.center()
        edge = await self.edge('edge', await self.ticket(client))
        stranger = await self.portal('stranger')
        stranger.federation.network_key = center.federation.network_key
        stranger.federation.publish()
        center.federation.ingest([stranger.federation.records[stranger.federation.id]])
        self.assertNotIn(stranger.federation.id, center.federation.records)
        edge.federation.seeds[stranger.federation.id] = stranger.federation.profile
        selected = []
        async def exchange(node_id, _profile):
            selected.append(node_id)
            edge.federation.inflight.pop(node_id, None)
        with patch.object(edge.federation, '_exchange_one', exchange):
            await edge.federation.sync_once()
        self.assertEqual(selected, [center.federation.id])

    async def test_center_disk_failure_retains_sender_outbox_until_commit(self):
        center, client = await self.center()
        edge = await self.edge('edge', await self.ticket(client))
        fed = edge.federation
        self.users = [enroll(fed.accounts, name, delegated=True) for name in ('alice', 'bob')]
        event = self.event(0, 'request', self.users[1][0])
        with patch.object(center.federation.accounts.social, 'save', side_effect=OSError('disk full')):
            result = await submit(fed, self.users[0][0], event)
        self.assertEqual(result['delivery']['state'], 'pending')
        self.assertIn(event['action']['id'], fed.accounts.social.pending)
        await retry_pending(fed)
        self.assertFalse(fed.accounts.social.pending)
        self.assertEqual(len(Social(center.federation.accounts).edges), 1)

    async def test_unknown_identity_issuer_and_full_outbox_are_rejected(self):
        from tunnel.social_delivery import accept_context
        center, _ = await self.center()
        outsider = await self.portal('outsider')
        outsider.federation.network_key = center.federation.network_key
        account, _ = enroll(outsider.federation.accounts, 'outsider_user', delegated=True)
        header = outsider.federation.accounts.replication.directory[account]
        with self.assertRaisesRegex(ValueError, 'Unknown identity issuer'):
            accept_context(center.federation, {'identities': [header]})
        self.assertNotIn(account, center.federation.accounts.replication.directory)
        store = center.federation.accounts
        self.users = [enroll(store, name, delegated=True) for name in ('alice', 'bob')]
        for _ in range(256):
            store.social.queue(self.event(0, 'request', self.users[1][0]), persist=False)
        with self.assertRaisesRegex(ValueError, '队列已满'):
            await submit(center.federation, self.users[0][0], self.event(0, 'request', self.users[1][0]))
        self.assertFalse(store.social.edges)

    async def test_enrollment_pending_does_not_accept_new_social_or_game_work(self):
        center, client = await self.center()
        edge = await self.portal('edge')
        path = edge.path.with_name('enrollment.json')
        atomic_json(path, await self.ticket(client))
        edge.control.configure({'mode': 'edge', 'enrollment_file': path.name}, path.parent)
        self.assertTrue(edge.relay.draining)
        self.users = [enroll(edge.federation.accounts, name, delegated=True) for name in ('alice', 'bob')]
        with self.assertRaises(OSError):
            await submit(edge.federation, self.users[0][0], self.event(0, 'request', self.users[1][0]))
        self.assertFalse(edge.federation.accounts.social.pending)
        await edge.control.bootstrap()
        self.assertFalse(edge.relay.draining)

    async def test_pinned_alternative_fallback_does_not_bypass_business_rejection(self):
        center, client = await self.center()
        edge = await self.edge('edge', await self.ticket(client))
        unavailable = await self.portal('unavailable')
        await unavailable.relay.close()
        path = edge.path.with_name('centers.json')
        atomic_json(path, [unavailable.federation.profile, center.federation.profile])
        edge.control.configure({'mode': 'edge', 'centers_file': path.name}, path.parent)
        fed = edge.federation
        self.users = [enroll(fed.accounts, name, delegated=True) for name in ('alice', 'bob')]
        result = await submit(fed, self.users[0][0], self.event(0, 'request', self.users[1][0]))
        self.assertEqual(result['delivery']['state'], 'accepted')
        atomic_json(path, [center.federation.profile, unavailable.federation.profile])
        edge.control.configure({'mode': 'edge', 'centers_file': path.name}, path.parent)
        with patch.object(fed, 'account_rpc', AsyncMock(wraps=fed.account_rpc)) as calls:
            with self.assertRaisesRegex(ValueError, '已发送申请'):
                await submit(fed, self.users[0][0], self.event(0, 'request', self.users[1][0]))
            self.assertEqual(calls.call_count, 1)
