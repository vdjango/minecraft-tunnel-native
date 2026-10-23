"""Protocol permission checks through real authenticated SSH connections."""
import asyncio
import json
from pathlib import Path
import secrets
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import asyncssh
from tunnel.relay import RelayServer, room_id


class RelaySecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.key = asyncssh.generate_private_key('ssh-ed25519')
        self.relay = await RelayServer('127.0.0.1', 0, host_key=self.key,
                                      token=secrets.token_urlsafe(48)).start()
        self.connections = []

    async def asyncTearDown(self):
        for connection in self.connections:
            connection.close()
        await asyncio.gather(*(c.wait_closed() for c in self.connections))
        await self.relay.close()

    async def connect(self, role='directory', secret='', token=None, claimed=None, public=None):
        claimed = claimed or (room_id(secret) if secret else 'rooms')
        connection = await asyncssh.connect('127.0.0.1', self.relay.port,
            username=role + ':' + claimed,
            password=json.dumps({'token': token or self.relay.token, 'secret': secret}),
            known_hosts=([asyncssh.import_public_key((public or self.key).export_public_key())], [], []),
            client_keys=[], agent_path=None, config=None,
            x509_trusted_certs=None, x509_trusted_cert_paths=[])
        self.connections.append(connection)
        return connection

    async def call(self, connection, request):
        async with connection.create_process(subsystem='buggcraft-rooms-v1') as process:
            process.stdin.write(json.dumps(request) + '\n')
            return json.loads(await asyncio.wait_for(process.stdout.readline(), 3))

    async def test_bad_token_role_room_secret_and_wrong_host_key_are_refused(self):
        secret = secrets.token_urlsafe(32)
        await self.connect('host', secret)
        for fields in ({'token': 'x' * 48}, {'role': 'admin'},
                       {'role': 'client', 'secret': secret, 'claimed': '0' * 32},
                       {'role': 'client', 'secret': secrets.token_urlsafe(32)},
                       {'role': 'host', 'secret': 'short'}):
            with self.subTest(fields=tuple(fields)), self.assertRaises(asyncssh.PermissionDenied):
                await self.connect(**fields)
        with self.assertRaises(asyncssh.HostKeyNotVerifiable):
            await self.connect(public=asyncssh.generate_private_key('ssh-ed25519'))
        self.assertEqual(len(self.relay.rooms), 1)

    async def test_shell_exec_sftp_and_unknown_subsystem_are_disabled(self):
        directory = await self.connect()
        for kwargs in ({}, {'command': 'echo forbidden'}, {'subsystem': 'sftp'},
                       {'subsystem': 'unknown'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(asyncssh.ChannelOpenError):
                await directory.create_process(**kwargs)
        result = await self.call(directory, {'op': 'ping'})
        self.assertTrue(result['ok'])

    async def test_directory_cannot_mutate_rooms_read_federation_or_forward_traffic(self):
        directory = await self.connect()
        for request in ({'op': 'configure', 'public': True}, {'op': 'close'},
                        {'op': 'snapshot'}, {'op': 'account_lookup', 'identifier': 'alice'},
                        {'op': 'social'}):
            with self.subTest(op=request['op']):
                self.assertFalse((await self.call(directory, request))['ok'])
        with self.assertRaises(asyncssh.ChannelOpenError):
            await directory.open_connection('127.0.0.1', 22)
        with self.assertRaises(asyncssh.ChannelListenError):
            await directory.forward_remote_port('0.0.0.0', 0, '127.0.0.1', 22)

    async def test_room_client_cannot_mutate_owner_metadata_or_forward_arbitrary_target(self):
        secret = secrets.token_urlsafe(32)
        owner = await self.connect('host', secret)
        client = await self.connect('client', secret)
        self.assertTrue((await self.call(owner, {'op': 'configure', 'title': 'Owner title',
                                               'public': False, 'capacity': 4, 'game': {}}))['ok'])
        for request in ({'op': 'configure', 'title': 'Stolen'}, {'op': 'close'}):
            self.assertFalse((await self.call(client, request))['ok'])
        self.assertEqual(self.relay.rooms[room_id(secret)].title, 'Owner title')
        for host, port in [('127.0.0.1', 22), (room_id(secret), 80)]:
            with self.subTest(target=(host, port)), self.assertRaises(asyncssh.ChannelOpenError):
                await client.open_connection(host, port)
        with self.assertRaises(asyncssh.ChannelListenError):
            await owner.forward_remote_port('0.0.0.0', 0, '127.0.0.1', 25565)

    async def test_invalid_control_frames_do_not_stop_other_connections(self):
        directory = await self.connect()
        for request in ([], {'op': 'not-an-operation'}, {'op': 'list', 'offset': -1}):
            self.assertFalse((await self.call(directory, request))['ok'])
        async with directory.create_process(subsystem='buggcraft-rooms-v1') as process:
            process.stdin.write('x' * 16385)
            await process.stdin.drain()
            self.assertEqual(await asyncio.wait_for(process.stdout.read(), 3), '')
        fresh = await self.connect()
        self.assertTrue((await self.call(fresh, {'op': 'ping'}))['ok'])

    async def test_explicit_close_prevents_reusing_old_room_credentials(self):
        secret = secrets.token_urlsafe(32)
        owner = await self.connect('host', secret)
        self.assertTrue((await self.call(owner, {'op': 'close'}))['ok'])
        self.assertNotIn(room_id(secret), self.relay.rooms)
        for role in ('client', 'host'):
            with self.subTest(role=role), self.assertRaises(asyncssh.PermissionDenied):
                await self.connect(role, secret)
