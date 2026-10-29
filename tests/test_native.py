"""Protocol compatibility through real AsyncSSH clients and the C++ process."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
import asyncssh
from tunnel.native_relay import RelayServer
from tunnel.relay import room_id
from tests.network_proxy import BlackholeProxy


class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.key = asyncssh.generate_private_key('ssh-ed25519')
        self.relay = await RelayServer('127.0.0.1', 0, host_key=self.key,
                                      token=secrets.token_urlsafe(48), workers=2).start()
        self.assertEqual(self.relay.buffer_kib, 512)
        self.connections = []
        self.servers = []

    async def asyncTearDown(self):
        for connection in self.connections:
            connection.close()
        await asyncio.gather(*(c.wait_closed() for c in self.connections))
        for server in self.servers:
            server.close()
            await server.wait_closed()
        await self.relay.close()

    async def connect(self, role='directory', secret='', **extra):
        result = await asyncssh.connect('127.0.0.1', self.relay.port,
            username=role + ':' + (room_id(secret) if secret else 'rooms'),
            password=json.dumps({'token': self.relay.token, 'secret': secret}),
            known_hosts=([asyncssh.import_public_key(self.key.export_public_key())], [], []),
            client_keys=[], agent_path=None, config=None,
            x509_trusted_certs=None, x509_trusted_cert_paths=[], **extra)
        self.connections.append(result)
        return result

    async def call(self, connection, value):
        async with connection.create_process(subsystem='buggcraft-rooms-v1') as process:
            process.stdin.write(json.dumps(value) + '\n')
            return json.loads(await asyncio.wait_for(process.stdout.readline(), 4))

    async def test_directory_and_room_control(self):
        directory = await self.connect()
        self.assertTrue((await self.call(directory, {'op': 'ping'}))['ok'])
        secret = secrets.token_urlsafe(32)
        host = await self.connect('host', secret)
        client = await self.connect('client', secret)
        configured = await self.call(host, {'op':'configure', 'title':'Native world', 'public':True, 'game':{}})
        self.assertTrue(configured['ok'])
        self.assertFalse((await self.call(client, {'op':'close'}))['ok'])
        self.assertEqual((await self.call(client, {'op':'describe'}))['result']['title'], 'Native world')

    async def test_public_preparation_room_is_visible_without_game_port(self):
        directory = await self.connect()
        secret = secrets.token_urlsafe(32)
        key = room_id(secret)
        host = await self.connect('host', secret)
        configured = await self.call(host, {'op': 'configure', 'title': 'Waiting for world',
                                            'public': True, 'require_invite': False, 'game': {}})
        self.assertTrue(configured['ok'])
        page = (await self.call(directory, {'op': 'list', 'query': 'Waiting for world'}))['result']
        listing = next(row for row in page['rooms'] if row['id'] == key)
        self.assertEqual(listing['availability'], 'waiting')
        self.assertFalse(listing['require_invite'])
        resolved = (await self.call(directory, {'op': 'resolve', 'id': key}))['result']
        self.assertEqual(resolved['secret'], secret)
        self.assertEqual(resolved['room']['availability'], 'waiting')

        self.assertTrue((await self.call(host, {'op': 'configure', 'require_invite': True}))['ok'])
        resolved = (await self.call(directory, {'op': 'resolve', 'id': key}))['result']
        self.assertNotIn('secret', resolved)
        self.assertTrue(resolved['room']['require_invite'])

        self.assertTrue((await self.call(host, {'op': 'configure', 'public': False}))['ok'])
        page = (await self.call(directory, {'op': 'list', 'query': 'Waiting for world'}))['result']
        self.assertFalse(any(row['id'] == key for row in page['rooms']))
        self.assertFalse((await self.call(directory, {'op': 'resolve', 'id': key}))['ok'])

    async def test_core_crash_disconnects_peers_and_notifies_service(self):
        directory = await self.connect()
        self.relay.process.kill()
        with self.assertRaises(ConnectionError):
            await asyncio.wait_for(self.relay.wait_failed(), 5)
        await asyncio.wait_for(directory.wait_closed(), 5)
        self.assertFalse(self.relay.native_peers)

    async def test_control_eof_releases_channel_slot(self):
        connection = await self.connect()
        for _ in range(6):
            process = await connection.create_process(subsystem='buggcraft-rooms-v1')
            process.stdin.write_eof()
            await asyncio.wait_for(process.wait_closed(), 3)

    async def test_idle_legacy_client_answers_native_heartbeat(self):
        connection = await self.connect()
        await asyncio.sleep(22)
        self.assertFalse(connection.is_closed())
        self.assertTrue((await self.call(connection, {'op':'ping'}))['ok'])

    async def test_control_handler_timeout_is_distinct_from_heartbeat_timeout(self):
        secret = secrets.token_urlsafe(32)
        connection = await self.connect('host', secret)
        peer = self.relay.rooms[room_id(secret)].owner
        dispatch = self.relay._dispatch
        entered, release = asyncio.Event(), asyncio.Event()
        async def stalled(value):
            if value.get('op') == 'channel_data':
                entered.set()
                await release.wait()
                return {'allowed': False}
            return await dispatch(value)
        self.relay._dispatch = stalled
        try:
            process = await connection.create_process(subsystem='buggcraft-rooms-v1')
            process.stdin.write('{"op":"ping"}\n')
            await asyncio.wait_for(entered.wait(), 3)
            await asyncio.wait_for(connection.wait_closed(), 16)
            self.assertEqual(peer.disconnect_reason, 'control-rpc-timeout')
            # SSH keepalive responses still arrive while the Python handler waits.
            self.assertLess(peer.transport_diagnostic['rx_age_ms'], 10000)
            self.assertGreaterEqual(peer.transport_diagnostic['probe_attempts'], 1)
        finally:
            release.set()
            self.relay._dispatch = dispatch

    async def test_blackholed_host_expires_without_affecting_other_room(self):
        proxy = await BlackholeProxy(self.relay.port).start()
        self.addAsyncCleanup(proxy.close)
        self.relay.host_grace_seconds = .2
        secret = secrets.token_urlsafe(32)
        host = await asyncssh.connect('127.0.0.1', proxy.port,
            username='host:' + room_id(secret),
            password=json.dumps(dict(token=self.relay.token, secret=secret)),
            known_hosts=([self.key.convert_to_public()], [], []),
            client_keys=[], agent_path=None, config=None)
        self.connections.append(host)
        stable_secret = secrets.token_urlsafe(32)
        stable = await self.connect('host', stable_secret)
        original_peer = self.relay.rooms[room_id(secret)].owner
        proxy.mute()
        async with asyncio.timeout(25):
            while room_id(secret) not in self.relay.closed_rooms:
                self.assertTrue((await self.call(stable, {'op':'ping'}))['ok'])
                await asyncio.sleep(.5)
        self.assertEqual(original_peer.disconnect_reason, 'heartbeat-timeout')
        evidence = original_peer.transport_diagnostic
        self.assertGreaterEqual(evidence['rx_age_ms'], 20000)
        self.assertGreaterEqual(evidence['probe_attempts'], 3)
        self.assertLess(evidence['poll_gap_ms'], 5000)
        self.assertGreater(evidence['ssh_rx_packets'], 0)
        self.assertGreater(evidence['ssh_tx_packets'], 0)
        self.assertIn(room_id(stable_secret), self.relay.rooms)
        # Break the proxy only during teardown; until now there was no FIN/RST.
        await proxy.close()

    async def test_short_one_way_pause_does_not_expire_room(self):
        proxy = await BlackholeProxy(self.relay.port).start()
        self.addAsyncCleanup(proxy.close)
        conn = await asyncssh.connect('127.0.0.1', proxy.port,
            username='directory:rooms', password=json.dumps(dict(token=self.relay.token)),
            known_hosts=([self.key.convert_to_public()], [], []),
            client_keys=[], agent_path=None, config=None)
        self.connections.append(conn)
        proxy.mute(1)
        await asyncio.sleep(7)
        proxy.resume()
        self.assertTrue((await self.call(conn, {'op':'ping'}))['ok'])

    async def test_host_reconnect_during_grace_cancels_room_expiry(self):
        self.relay.host_grace_seconds = .5
        secret = secrets.token_urlsafe(32)
        host = await self.connect('host', secret)
        room = self.relay.rooms[room_id(secret)]
        host.abort()
        await asyncio.wait_for(host.wait_closed(), 3)
        async with asyncio.timeout(3):
            while room.expiry is None:
                await asyncio.sleep(.01)
        recovered = await self.connect('host', secret)
        await asyncio.sleep(.6)
        self.assertIs(self.relay.rooms[room_id(secret)], room)
        self.assertIsNone(room.expiry)
        self.assertNotIn(room_id(secret), self.relay.closed_rooms)
        self.assertTrue((await self.call(recovered, {'op':'ping'}))['ok'])

    async def test_unicode_host_key_path(self):
        original = self.relay
        with tempfile.TemporaryDirectory(prefix='native key ') as directory:
            path = Path(directory) / '\u8282\u70b9 \u5bc6\u94a5'
            path.write_bytes(self.key.export_private_key())
            self.relay = await RelayServer('127.0.0.1', 0, host_key=str(path),
                                          token=original.token, workers=1).start()
            try:
                connection = await self.connect()
                self.assertTrue((await self.call(connection, {'op': 'ping'}))['ok'])
                connection.close()
                await connection.wait_closed()
            finally:
                await self.relay.close()
                self.relay = original

    async def test_ipv6_ssh_and_stun(self):
        if not socket.has_ipv6:
            self.skipTest('IPv6 is unavailable')
        relay = await RelayServer('::1', 0, host_key=self.key, token=self.relay.token, workers=1).start()
        connection = None
        udp = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        udp.bind(('::1', 0))
        udp.setblocking(False)
        try:
            connection = await asyncssh.connect('::1', relay.port, username='directory:rooms',
                password=json.dumps(dict(token=relay.token, secret='')),
                known_hosts=([self.key.convert_to_public()], [], []), client_keys=[], config=None, agent_path=None)
            self.assertTrue((await self.call(connection, {'op': 'ping'}))['ok'])
            transaction = os.urandom(12)
            request = b'\x00\x01\x00\x00\x21\x12\xa4\x42' + transaction
            loop = asyncio.get_running_loop()
            await loop.sock_sendto(udp, request, ('::1', relay.port, 0, 0))
            response, _ = await asyncio.wait_for(loop.sock_recvfrom(udp, 512), 3)
            self.assertEqual(response[:20], b'\x01\x01\x00\x18' + request[4:])
            self.assertEqual(response[20:26], b'\x00\x20\x00\x14\x00\x02')
            decoded = bytes(a ^ b for a, b in zip(response[28:], request[4:]))
            self.assertEqual(decoded, socket.inet_pton(socket.AF_INET6, '::1'))
        finally:
            udp.close()
            if connection:
                connection.close()
                await connection.wait_closed()
            await relay.close()

    async def test_payload_and_half_close_without_python_game_bytes(self):
        async def echo(reader, writer):
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
                writer.write(b'after-eof')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(echo, '127.0.0.1', 0)
        self.servers.append(server)
        secret = secrets.token_urlsafe(32)
        host = await self.connect('host', secret)
        await host.forward_remote_port('127.0.0.1', 0, '127.0.0.1', server.sockets[0].getsockname()[1])
        client = await self.connect('client', secret)
        reader, writer = await client.open_connection(room_id(secret), 25565, encoding=None)
        payload = os.urandom(2 * 1024 * 1024)
        async def send():
            writer.write(payload)
            await writer.drain()
            writer.write_eof()
        sending = asyncio.create_task(send())
        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 15), payload)
        self.assertEqual(await asyncio.wait_for(reader.readexactly(9), 5), b'after-eof')
        await sending
        writer.close()
        await writer.wait_closed()
