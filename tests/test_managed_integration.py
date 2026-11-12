"""Real private control binary + native relay + native bridge, using an isolated database."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import secrets
import ssl
import time
import unittest

from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives.asymmetric import ec

from tests import test_bridge as fixture
from tests import test_control_plane as portal_fixture
from tests import test_bridge_library as library_fixture
from tests.account_fixture import enroll, sign
from tunnel.accounts import encode
from tunnel.managed_control import IdentityAuthority


@unittest.skipUnless(os.environ.get('CONTROL_BINARY') and os.environ.get('CONTROL_TEST_DATABASE'),
                     'Set CONTROL_BINARY and isolated CONTROL_TEST_DATABASE to run native control integration')
class ManagedIntegrationTests(unittest.IsolatedAsyncioTestCase):
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    exchange = fixture.BridgeTests.exchange
    portal = portal_fixture.ControlTests.portal

    async def asyncSetUp(self):
        await fixture.BridgeTests.asyncSetUp(self)
        root = Path(os.environ.get('CONTROL_SOURCE', fixture.ROOT.parent/'buggcraft-control-native'))
        spec = importlib.util.spec_from_file_location('control_fixture', root/'tests/integration.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.env = await asyncio.to_thread(module.Environment)
        self.addAsyncCleanup(asyncio.to_thread,self.env.close)
        self.public = self.env.public
        health = await asyncio.to_thread(self.public.call,'health')
        common = dict(origin=self.env.base, ca_file=str(self.env.path/'tls.pem'),
                      signing_public_key=health['signing_public_key'])
        (self.logs/'admin-token').write_text(self.env.admin_token)
        from tests.service_fixture import create_identity
        from tunnel.native_relay import RelayServer
        from tunnel.service_portal import RelayPortal
        import asyncssh
        identity_relay = RelayServer('127.0.0.1',0,host_key=asyncssh.generate_private_key('ssh-ed25519'),token='x'*48,workers=1)
        self.addAsyncCleanup(identity_relay.close)
        self.authority, issuer = create_identity(identity_relay,self.logs/'authority',
            dict(common,admin_token_file=str(self.logs/'admin-token')))
        self.addAsyncCleanup(self.authority.identity_authority.close)
        signing_key = self.node.host_key
        # Production stores the relay key as a filename before native startup.
        self.node.host_key = 'production-host-key-path'
        (self.logs/'credential').write_text(self.env.credential)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.env.path/'tls.pem',self.env.path/'tls-key.pem')
        # Reserve a port before configure; the lifecycle starts enrollment in the background.
        port = module.free_port()
        self.node_portal = RelayPortal(self.node,signing_key,self.logs/'node-state.json',dict(common,credential_file=str(self.logs/'credential'),
            endpoint=f'127.0.0.1:{self.node.port}',proof_endpoint=f'127.0.0.1:{port}',
            slots='32',bandwidth='6000000',mode='native'),self.logs)
        server = TestServer(self.node_portal.app, port=port)
        await server.start_server(ssl=context)
        self.addAsyncCleanup(server.close)
        self.assertEqual(server.port,port)
        for _ in range(100):
            if self.node_portal.control.status=='ready':
                break
            await asyncio.sleep(.1)
        self.assertEqual(self.node_portal.control.status,'ready')

    async def user(self,name):
        api = self.authority.account_api
        account,_,key = enroll(api.store,name,return_key=True)
        device = ec.generate_private_key(ec.SECP256R1())
        numbers = device.public_key().public_numbers()
        public = dict(kty='EC',crv='P-256',x=encode(numbers.x.to_bytes(32,'big')),y=encode(numbers.y.to_bytes(32,'big')))
        identifier = secrets.token_hex(16)
        now = int(time.time())
        grant = dict(a=account,i=api.store.federation.id,n='test',p='buggcraft-launcher-device-v1',
                     o=self.env.base,r=identifier,b=now,e=now+3600,d=public)
        entry = dict(grant=grant,signature=sign(key,grant),name=name,last_used=now,revoked=False)
        api.store.launcher_devices.devices[identifier] = entry
        await self.authority.identity_authority.authorize(entry)
        profile = api.store.launcher_devices.profile(identifier)['profile']
        profile['token'] += '~'+encode(device.private_numbers().private_value.to_bytes(32,'big'))
        process = await self.process()
        node = (await self.call(process,'node.add',dict(profile=profile)))['node']
        return account,process,node

    async def test_control_login_friend_invitation_native_payload_and_eof(self):
        a,host,hn = await self.user('managed_host')
        b,guest,gn = await self.user('managed_guest')
        await self.call(host,'social.operate',dict(node=hn,operation='request',target=b))
        state = await self.call(guest,'node.request',dict(node=gn,request={'op':'social'}))
        self.assertEqual(state['requests'][0]['account'],a)
        await self.call(guest,'social.operate',dict(node=gn,operation='accept',target=a))
        hosted = await self.call(host,'room.host',dict(node=hn,game_port=self.port,transport='relay'))
        await self.call(host,'social.operate',dict(node=hn,operation='invite',target=b,fields=dict(room=hosted['room'])))
        state = await self.call(guest,'node.request',dict(node=gn,request={'op':'social'}))
        invite = state['invitations'][0]
        resolved = await self.call(guest,'room.resolve',dict(node=gn,id=hosted['room']))
        self.assertEqual('bugg1-'+resolved['secret'],invite['invite'])
        result = await self.call(guest,'social.operate',dict(node=gn,operation='join',target=a,fields=dict(ref=invite['id'])))
        joined = await self.call(guest,'room.join',dict(node=gn,invite=result['invite'],transport='relay'))
        await self.exchange(joined,secrets.token_bytes(1024*1024))
        await self.node_portal.control.renew_allocations()
        self.assertTrue(self.node_portal.control.batch_refresh)
        self.assertEqual(len(self.node_portal.control.allocations), 2)
        old = next(p for p in self.node.peers if p.role=='client' and p.account==b)
        old.conn.close()
        for _ in range(200):
            if any(p.role=='client' and p.account==b and p is not old for p in self.node.peers):
                break
            await asyncio.sleep(.1)
        else:
            self.fail('Managed client did not recover its assigned room')
        await self.exchange(joined,secrets.token_bytes(1024*1024))
        await asyncio.to_thread(self.env.process.terminate)
        await asyncio.to_thread(self.env.process.wait,10)
        # Already admitted game bytes do not require a live control service.
        await self.exchange(joined,secrets.token_bytes(1024*1024))

    async def test_public_directory_resolve_without_invitation_and_close(self):
        _, host, hn = await self.user('directory_host')
        _, guest, gn = await self.user('directory_guest')
        settings = dict(title='Shared adventure', public=True, require_invite=False,
                        game=dict(version='1.21.1', loader='Fabric'))
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port,
                                                       transport='relay', settings=settings))
        self.assertNotIn('directory_error', hosted)
        rooms = (await self.call(guest, 'room.list', dict(node=gn, query='adventure')))['rooms']
        room = next(r for r in rooms if r['id'] == hosted['room'])
        self.assertEqual(room['title'], settings['title'])
        self.assertEqual(room['game']['version'], '1.21.1')
        self.assertFalse(room['require_invite'])
        self.assertNotIn('secret', room)
        for process, node in ((host, hn), (guest, gn)):
            resolved = await self.call(process, 'room.resolve', dict(node=node, id=hosted['room']))
            self.assertEqual('bugg1-'+resolved['secret'], hosted['invite'])
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite'], transport='relay'))
        await self.exchange(joined, secrets.token_bytes(65536))
        await self.call(guest, 'room.close', dict(session=joined['session']))
        settings['require_invite'] = True
        configured = await self.call(host, 'room.configure', dict(session=hosted['session'], settings=settings))
        self.assertNotIn('directory_error', configured)
        rooms = (await self.call(guest, 'room.list', dict(node=gn)))['rooms']
        self.assertTrue(next(r for r in rooms if r['id'] == hosted['room'])['require_invite'])
        # A copied invitation remains usable without a social invitation event.
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite'], transport='relay'))
        await self.exchange(joined, secrets.token_bytes(65536))
        await self.call(guest, 'room.close', dict(session=joined['session']))
        await self.call(host, 'room.close', dict(session=hosted['session']))
        rooms = (await self.call(guest, 'room.list', dict(node=gn)))['rooms']
        self.assertNotIn(hosted['room'], [r['id'] for r in rooms])


class ManagedLibraryIntegrationTests(ManagedIntegrationTests):
    process = library_fixture.LibraryServicesTests.process
    call = library_fixture.LibraryServicesTests.call
