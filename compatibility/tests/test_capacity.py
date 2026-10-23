"""Real SSH admission, player reporting and measured forwarding budgets."""
import asyncio
import json
from pathlib import Path
import secrets
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import asyncssh
from tunnel.capacity import policy
from tunnel.relay import RelayServer, room_id


async def rpc(connection, operation, **fields):
    async with connection.create_process(subsystem='buggcraft-rooms-v1', encoding='utf-8') as stream:
        stream.stdin.write(json.dumps(dict(op=operation, **fields)) + '\n')
        response = json.loads(await asyncio.wait_for(stream.stdout.readline(), 3))
        if not response['ok']:
            raise ValueError(response['error'])
        return response['result']


class CapacityTests(unittest.IsolatedAsyncioTestCase):
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

    async def connect(self, role, secret=''):
        key = room_id(secret) if secret else 'rooms'
        connection = await asyncssh.connect('127.0.0.1', self.relay.port,
            username=role + ':' + key,
            password=json.dumps(dict(token=self.relay.token, secret=secret)),
            known_hosts=([asyncssh.import_public_key(self.key.export_public_key())], [], []), client_keys=[], agent_path=None,
            config=None, x509_trusted_certs=None, x509_trusted_cert_paths=[])
        self.connections.append(connection)
        return connection

    async def host(self):
        secret = secrets.token_urlsafe(32)
        return await self.connect('host', secret), secret

    async def test_room_capacity_counts_people_and_releases_departed_visitors(self):
        host, secret = await self.host()
        self.relay.capacity.configure(dict(room_capacity=2))
        visitor = await self.connect('client', secret)
        directory = await self.connect('directory')
        state = await rpc(host, 'describe')
        self.assertEqual((state['members'], state['connections'], state['capacity']), (2, 0, 2))
        self.assertEqual(self.relay.capacity.snapshot()['admitted_visitors'], 1)
        with self.assertRaises(asyncssh.PermissionDenied):
            await self.connect('client', secret)
        visitor.close()
        await visitor.wait_closed()
        await asyncio.sleep(.05)
        await self.connect('client', secret)
        self.assertFalse(directory.is_closed())

    async def test_node_bandwidth_budget_is_shared_across_rooms(self):
        self.relay.capacity.configure(dict(bandwidth_mb_s=.24, per_player_mb_s=.128))
        first, a = await self.host()
        second, b = await self.host()
        self.assertEqual(self.relay.capacity.limit(), 1)
        await self.connect('client', a)
        with self.assertRaises(asyncssh.PermissionDenied):
            await self.connect('client', b)
        self.assertEqual(self.relay.capacity.members(room_id(b)), 1)

    async def test_parallel_authentication_cannot_overbook_room(self):
        host, secret = await self.host()
        self.relay.capacity.configure(dict(room_capacity=2))
        await asyncio.gather(*(self.connect('client', secret) for _ in range(5)), return_exceptions=True)
        await asyncio.sleep(.1)
        self.assertEqual(self.relay.capacity.members(room_id(secret)), 2)

    async def test_maximum_rooms_and_lowering_limits_preserve_existing_players(self):
        host, secret = await self.host()
        visitor = await self.connect('client', secret)
        self.relay.capacity.configure(dict(max_rooms=1, room_capacity=1))
        self.assertFalse(visitor.is_closed())
        with self.assertRaises(asyncssh.PermissionDenied):
            await self.host()
        with self.assertRaises(asyncssh.PermissionDenied):
            await self.connect('client', secret)

    async def test_game_player_reports_require_owner_and_expire(self):
        host, secret = await self.host()
        visitor = await self.connect('client', secret)
        with self.assertRaises(ValueError):
            await rpc(visitor, 'presence', players=999, maximum=1000)
        for invalid in (True, -1, 100001, '2'):
            with self.assertRaises(ValueError):
                await rpc(host, 'presence', players=invalid)
        state = await rpc(host, 'presence', players=3, maximum=8)
        self.assertEqual((state['game_players'], state['members']), (3, 2))
        self.relay.rooms[room_id(secret)].game_reported -= 16
        self.assertIsNone((await rpc(visitor, 'describe'))['game_players'])

    async def test_forwarded_bytes_include_both_directions_not_control_sessions(self):
        async def echo(reader, writer):
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
        world = await asyncio.start_server(echo, '127.0.0.1', 0)
        try:
            host, secret = await self.host()
            await host.forward_remote_port('127.0.0.1', 0, '127.0.0.1', world.sockets[0].getsockname()[1])
            visitor = await self.connect('client', secret)
            for _ in range(3):
                await rpc(visitor, 'describe')
            self.assertEqual(self.relay.capacity.bytes, 0)
            reader, writer = await visitor.open_connection(room_id(secret), 25565, encoding=None)
            data = secrets.token_bytes(32768)
            writer.write(data)
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(data)), 3), data)
            # Native payload counters are batched once per second across IPC;
            # game packets must not generate Python callbacks individually.
            async with asyncio.timeout(2):
                while self.relay.capacity.bytes < len(data) * 2:
                    await asyncio.sleep(.01)
            self.assertEqual(self.relay.capacity.bytes, len(data) * 2)
            self.assertGreater(self.relay.capacity.snapshot()['payload_mb_s'], 0)
            self.assertEqual((await rpc(visitor, 'describe'))['members'], 2)
            writer.close()
            await writer.wait_closed()
        finally:
            world.close()
            await world.wait_closed()

    async def test_budget_validation_and_rate_window(self):
        for value in (True, [], '1', dict(bandwidth_mb_s=float('nan')),
                      dict(reserve_percent=0), dict(room_capacity=True), dict(per_player_mb_s=0)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy(value)
        self.relay.capacity.configure(dict(bandwidth_mb_s=1))
        self.assertEqual(self.relay.capacity.limit(), 4)
        budget = self.relay.capacity
        with patch('tunnel.capacity.time.monotonic', return_value=budget.started + 1):
            budget.record(1000000)
            self.assertEqual(budget.snapshot()['payload_mb_s'], 1)
        with patch('tunnel.capacity.time.monotonic', return_value=budget.started + 12):
            self.assertEqual(budget.snapshot()['payload_mb_s'], 0)


if __name__ == '__main__':
    unittest.main()
