"""Opt-in acceptance against two deployed nodes; creates temporary test rooms."""
import asyncio
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import sys
import unittest
from urllib.parse import urljoin

import aiohttp
import asyncssh

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from tunnel.relay import room_id
from tunnel.stun import COOKIE
from tunnel.federation import identity


@unittest.skipUnless(os.environ.get('BUGGCRAFT_TEST_DEPLOYED') == '1',
                     'Requires explicit opt-in and private deployed-node configuration')
class DeployedNodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        path = Path(os.environ['BUGGCRAFT_DEPLOYED_CONFIG'])
        self.nodes = json.loads(path.read_text(encoding='utf-8'))['nodes']
        self.assertEqual(len(self.nodes), 2, 'Acceptance requires exactly two configured nodes')
        self.connections = []

    async def asyncTearDown(self):
        for connection in self.connections:
            connection.close()
        await asyncio.gather(*(c.wait_closed() for c in self.connections))

    async def connect(self, profile, role='directory', secret='', member_token=None):
        connection = await asyncssh.connect(
            profile['server_host'], profile['server_port'],
            username=role + ':' + (room_id(secret) if secret else 'rooms'),
            password=json.dumps({'token': profile['token'], 'secret': secret,
                                 'member_token': member_token or secrets.token_urlsafe(32)}),
            known_hosts=([asyncssh.import_public_key(profile['server_public_key'])], [], []),
            config=None, client_keys=[], agent_path=None, connect_timeout=10,
            x509_trusted_certs=None, x509_trusted_cert_paths=[])
        self.connections.append(connection)
        return connection

    async def rpc(self, connection, operation, **fields):
        async with connection.create_process(subsystem='buggcraft-rooms-v1') as process:
            process.stdin.write(json.dumps({'op': operation, **fields}) + '\n')
            response = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
            self.assertTrue(response.get('ok'), 'Node refused acceptance operation: ' + operation)
            return response['result']

    async def test_public_https_status_and_protected_admin(self):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as client:
            for node in self.nodes:
                with self.subTest(node=node['name']):
                    pin = aiohttp.Fingerprint(bytes.fromhex(node['tls_sha256']))
                    for path in ('/', '/developers', '/admin/nodes'):
                        async with client.get(node['web_url'] + path, ssl=pin) as response:
                            self.assertEqual(response.status, 200, path)
                            body = (await response.read()).decode('utf-8')
                            self.assertNotIn(node['profile']['token'], body)
                            if path == '/developers':
                                asset = re.search(r'<script[^>]+src="([^"]*vue-page-developers\.js[^"]*)"', body)
                                self.assertIsNotNone(asset, 'Developer page must load the built Vue content')
                                async with client.get(urljoin(node['web_url'], asset.group(1)), ssl=pin) as script:
                                    self.assertEqual(script.status, 200)
                                    docs = (await script.read()).decode('utf-8')
                                self.assertIn('文档核对：2026-10-28', docs)
                                self.assertIn('.buggpack', docs)
                                self.assertIn('resourcepacks', docs)
                                self.assertIn('room-lobby-v1', docs)
                                self.assertIn('公开准备房间尚未开放游戏世界时', docs)
                                self.assertIn('logs_request', docs)
                    async with client.get(node['web_url'] + '/api/status', ssl=pin) as response:
                        self.assertEqual(response.status, 200)
                        status = await response.json()
                    self.assertEqual(status['name'], node['name'])
                    self.assertEqual(status['status'], 'online')
                    self.assertTrue(status['players_include_p2p'])
                    self.assertEqual(status['game_players_source'], 'host_minecraft_status')
                    self.assertGreaterEqual(status['game_counts_reported'], 0)
                    self.assertGreaterEqual(status['game_counts_unknown'], 0)
                    # Health includes configured/expired peers. They do not
                    # count as live nodes and must not hide a deployed target.
                    expected = {identity(n['profile']['server_public_key']) for n in self.nodes}
                    live = {n['id'] for n in status['nodes']
                            if n['status'] in ('online', 'indirect')}
                    self.assertEqual(live, expected)
                    self.assertEqual(status['network_nodes'], len(expected))
                    self.assertEqual(status['id'], identity(node['profile']['server_public_key']))
                    self.assertNotIn(node['profile']['token'], json.dumps(status))
                    async with client.get(node['web_url'] + '/api/admin', ssl=pin) as response:
                        self.assertEqual(response.status, 401)
                    async with client.post(node['web_url'] + '/api/settings', ssl=pin,
                                           headers={'Origin': 'https://untrusted.invalid'},
                                           json={}) as response:
                        self.assertEqual(response.status, 403)

    async def test_public_udp_binding_preserves_transaction_and_observed_port(self):
        loop = asyncio.get_running_loop()
        for node in self.nodes:
            with self.subTest(node=node['name']):
                profile = node['profile']
                addresses = await loop.getaddrinfo(profile['server_host'], profile['server_port'],
                                                   family=socket.AF_INET, type=socket.SOCK_DGRAM)
                target = addresses[0][4]
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    probe.bind(('0.0.0.0', 0))
                    probe.setblocking(False)
                    transaction = secrets.token_bytes(12)
                    await loop.sock_sendto(probe, struct.pack('!HHI', 1, 0, COOKIE) + transaction, target)
                    data, sender = await asyncio.wait_for(loop.sock_recvfrom(probe, 2048), 5)
                self.assertEqual(sender, target)
                self.assertEqual(struct.unpack('!HHI', data[:8]), (0x101, 12, COOKIE))
                self.assertEqual(data[8:20], transaction)
                self.assertEqual(struct.unpack('!HH', data[20:24]), (0x20, 8))
                self.assertEqual(data[25], 1)
                self.assertGreater(struct.unpack('!H', data[26:28])[0] ^ (COOKIE >> 16), 0)
                observed = bytes(a ^ b for a, b in zip(data[28:32], struct.pack('!I', COOKIE)))
                self.assertTrue(ipaddress.ip_address(observed).is_global,
                                'A public node must report the public source address')

    async def test_cross_node_discovery_routing_bidirectional_payload_and_cleanup(self):
        async def echo(reader, writer):
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        game = await asyncio.start_server(echo, '127.0.0.1', 0)
        try:
            for owner_node, directory_node in (self.nodes, self.nodes[::-1]):
                with self.subTest(owner=owner_node['name']):
                    secret = secrets.token_urlsafe(32)
                    key = room_id(secret)
                    host = await self.connect(owner_node['profile'], 'host', secret)
                    directory = None
                    visitor = None
                    try:
                        await host.forward_remote_port('127.0.0.1', 0, '127.0.0.1',
                                                       game.sockets[0].getsockname()[1])
                        await self.rpc(host, 'configure', title='Deployment acceptance',
                                       public=True, require_invite=True, capacity=2)
                        directory = await self.connect(directory_node['profile'])
                        async with asyncio.timeout(20):
                            while True:
                                page = await self.rpc(directory, 'list', query='Deployment acceptance')
                                if any(r['id'] == key for r in page['rooms']):
                                    break
                                await asyncio.sleep(.5)
                        located = await self.rpc(directory, 'locate', id=key)
                        self.assertIsNotNone(located['profile'])
                        self.assertEqual(located['profile']['server_public_key'],
                                         owner_node['profile']['server_public_key'])
                        visitor = await self.connect(located['profile'], 'client', secret)
                        reader, writer = await visitor.open_connection(key, 25565, encoding=None)
                        payload = secrets.token_bytes(1024 * 1024)
                        writer.write(payload)
                        await writer.drain()
                        writer.write_eof()
                        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 30), payload)
                        self.assertEqual(await asyncio.wait_for(reader.read(), 10), b'')
                        writer.close()
                        await writer.wait_closed()
                        state = await self.rpc(visitor, 'describe')
                        self.assertEqual(state['members'], 2)
                        self.assertEqual(state['connections'], 0)
                    finally:
                        try:
                            if not host.is_closed():
                                await self.rpc(host, 'close')
                        finally:
                            host.close()
                            await host.wait_closed()
                    async with asyncio.timeout(20):
                        while True:
                            state = await self.rpc(directory, 'state', id=key)
                            if state['availability'] != 'ready':
                                break
                            await asyncio.sleep(.5)
                    page = await self.rpc(directory, 'list', query='Deployment acceptance')
                    self.assertFalse(any(r['id'] == key for r in page['rooms']))
                    visitor.close()
                    await visitor.wait_closed()
                    directory.close()
                    await directory.wait_closed()
        finally:
            game.close()
            await game.wait_closed()

    async def test_preparation_members_consent_transfer_and_kick_on_both_public_nodes(self):
        async def rejected(connection, operation, **fields):
            async with connection.create_process(subsystem='buggcraft-rooms-v1') as process:
                process.stdin.write(json.dumps({'op': operation, **fields}) + '\n')
                response = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
                self.assertFalse(response.get('ok'), operation + ' must be rejected')

        for owner_node, entry_node in (self.nodes, self.nodes[::-1]):
            with self.subTest(owner=owner_node['name']):
                secret = secrets.token_urlsafe(32)
                key = room_id(secret)
                host = await self.connect(owner_node['profile'], 'host', secret)
                directory = await self.connect(entry_node['profile'])
                guest = third = None
                try:
                    self.assertIn('room-lobby-v1', (await self.rpc(directory, 'ping'))['capabilities'])
                    await self.rpc(host, 'configure', title='Preparation deployment acceptance',
                                   public=True, require_invite=True, capacity=4,
                                   game=dict(version='1.21.8', loader='vanilla'))
                    async with asyncio.timeout(25):
                        while True:
                            page = await self.rpc(directory, 'list', query='Preparation deployment acceptance')
                            if any(row['id'] == key for row in page['rooms']):
                                break
                            await asyncio.sleep(.7)
                    located = await self.rpc(directory, 'locate', id=key)
                    self.assertEqual(located['profile']['server_public_key'], owner_node['profile']['server_public_key'])
                    resolved = await self.rpc(directory, 'resolve', id=key)
                    self.assertEqual(resolved['room']['availability'], 'waiting')
                    self.assertTrue(resolved['room']['require_invite'])
                    self.assertNotIn('secret', resolved)
                    guest = await self.connect(located['profile'], 'client', secret)
                    snapshot = await self.rpc(guest, 'members')
                    target, provider = snapshot['self_id'], snapshot['provider_id']
                    self.assertEqual(snapshot['online'], 2)
                    self.assertEqual((await self.rpc(guest, 'describe'))['availability'], 'waiting')
                    update = dict(config_id=snapshot['config_id'], seq=1, task='public-install',
                                  state='downloading', stage='mods', done=256, total=1024, speed=128)
                    self.assertTrue((await self.rpc(guest, 'member_update', **update))['accepted'])
                    self.assertFalse((await self.rpc(guest, 'member_update', **update))['accepted'])
                    row = next(row for row in (await self.rpc(host, 'members'))['members'] if row['id'] == target)
                    self.assertEqual((row['done'], row['total'], row['speed']), (256, 1024, 128))
                    await rejected(host, 'logs_read', member=target)
                    await self.rpc(host, 'logs_request', member=target)
                    request = (await self.rpc(guest, 'members'))['logs']['request']
                    await self.rpc(guest, 'logs_answer', request=request['id'], allow=True)
                    await self.rpc(guest, 'logs_append', entries=[dict(seq=1, category='mods', level='error',
                        text=r'sodium.jar C:\Users\Private\mods password=secret https://example.test/private')])
                    rows = (await self.rpc(host, 'logs_read', member=target))['entries']
                    self.assertEqual(len(rows), 1)
                    self.assertIn('sodium.jar', rows[0]['text'])
                    for private in ('Private', 'secret', 'example.test'):
                        self.assertNotIn(private, rows[0]['text'])
                    await self.rpc(guest, 'logs_revoke')
                    await rejected(host, 'logs_read', member=target)
                    offer = (await self.rpc(host, 'owner_offer', member=target))['transfer']
                    await self.rpc(guest, 'owner_answer', offer=offer['id'], accept=True)
                    changed = await self.rpc(guest, 'members')
                    self.assertEqual((changed['manager_id'], changed['provider_id']), (target, provider))
                    await rejected(host, 'configure', title='Must not change')
                    await self.rpc(guest, 'configure', title='Transferred preparation acceptance')
                    proof = secrets.token_urlsafe(32)
                    third = await self.connect(located['profile'], 'client', secret, proof)
                    third_id = (await self.rpc(third, 'members'))['self_id']
                    await rejected(third, 'member_kick', member=target)
                    await self.rpc(guest, 'member_kick', member=third_id)
                    await asyncio.wait_for(third.wait_closed(), 10)
                    with self.assertRaises(asyncssh.PermissionDenied):
                        await self.connect(located['profile'], 'client', secret, proof)
                    await self.rpc(guest, 'member_unblock', member=third_id)
                    resumed = await self.connect(located['profile'], 'client', secret, proof)
                    self.assertEqual((await self.rpc(resumed, 'members'))['self_id'], third_id)
                    resumed.close(); await resumed.wait_closed()
                    host.close(); await host.wait_closed()
                    self.assertEqual((await self.rpc(guest, 'describe'))['availability'], 'waiting')
                    self.assertEqual((await self.rpc(guest, 'members'))['manager_id'], target)
                finally:
                    if guest and not guest.is_closed():
                        await self.rpc(guest, 'close')
                    for connection in (third, guest, host, directory):
                        if connection:
                            connection.close()
                            await connection.wait_closed()

    async def test_public_ssh_rejects_unknown_host_key_and_bad_credentials(self):
        for node in self.nodes:
            with self.subTest(node=node['name']):
                profile = node['profile']
                wrong = dict(profile, token=secrets.token_urlsafe(48))
                with self.assertRaises(asyncssh.PermissionDenied):
                    await self.connect(wrong)
                wrong = dict(profile, server_public_key=asyncssh.generate_private_key(
                    'ssh-ed25519').export_public_key().decode())
                with self.assertRaises(asyncssh.HostKeyNotVerifiable):
                    await self.connect(wrong)
