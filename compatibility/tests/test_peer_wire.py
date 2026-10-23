"""Rendezvous travels over authenticated SSH; STUN uses a real UDP socket."""
import asyncio
import json
from pathlib import Path
import secrets
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import asyncssh
from tunnel.relay import RelayServer, room_id
from tunnel.stun import COOKIE


class PeerWireTests(unittest.IsolatedAsyncioTestCase):
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
        connection = await asyncssh.connect('127.0.0.1', self.relay.port,
            username=role + ':' + (room_id(secret) if secret else 'rooms'),
            password=json.dumps(dict(token=self.relay.token, secret=secret)),
            known_hosts=([asyncssh.import_public_key(self.key.export_public_key())], [], []),
            client_keys=[], config=None, agent_path=None,
            x509_trusted_certs=None, x509_trusted_cert_paths=[])
        self.connections.append(connection)
        return connection

    async def receive(self, process):
        return json.loads(await asyncio.wait_for(process.stdout.readline(), 3))

    async def test_real_ssh_offer_and_answer_preserve_authenticated_peer_identity(self):
        secret = secrets.token_urlsafe(32)
        owner = await self.connect('host', secret)
        visitor = await self.connect('client', secret)
        async with owner.create_process(subsystem='buggcraft-peer-v1') as host, \
                visitor.create_process(subsystem='buggcraft-peer-v1') as client:
            await self.receive(host)
            ready = await self.receive(client)
            self.assertTrue(ready['available'])
            client.stdin.write(json.dumps(dict(type='offer', sdp='offer bytes', peer='forged')) + '\n')
            offer = await self.receive(host)
            self.assertEqual(offer['peer'], ready['peer'])
            self.assertEqual(offer['sdp'], 'offer bytes')
            host.stdin.write(json.dumps(dict(type='answer', peer=offer['peer'], sdp='answer bytes')) + '\n')
            self.assertEqual((await self.receive(client))['sdp'], 'answer bytes')
        self.assertEqual(self.relay.capacity.bytes, 0)

    async def test_directory_identity_cannot_open_peer_signaling(self):
        directory = await self.connect('directory')
        with self.assertRaises(asyncssh.ChannelOpenError):
            await directory.create_process(subsystem='buggcraft-peer-v1')

    async def test_real_udp_binding_returns_observed_source_port_and_transaction(self):
        loop = asyncio.get_running_loop()
        received, closed = loop.create_future(), loop.create_future()

        class Probe(asyncio.DatagramProtocol):
            def datagram_received(self, data, address):
                if not received.done():
                    received.set_result((data, address))

            def connection_lost(self, error):
                if not closed.done():
                    closed.set_result(None)

        transport, _ = await loop.create_datagram_endpoint(Probe,
            local_addr=('127.0.0.1', 0), remote_addr=('127.0.0.1', self.relay.port))
        source_port = transport.get_extra_info('sockname')[1]
        transaction = secrets.token_bytes(12)
        try:
            transport.sendto(struct.pack('!HHI', 1, 0, COOKIE) + transaction)
            data, address = await asyncio.wait_for(received, 3)
            self.assertEqual(address[1], self.relay.port)
            self.assertEqual(data[8:20], transaction)
            self.assertEqual(struct.unpack('!HHI', data[:8]), (0x101, 12, COOKIE))
            self.assertEqual(struct.unpack('!H', data[26:28])[0] ^ (COOKIE >> 16), source_port)
            expected_ip = bytes(a ^ b for a, b in zip(b'\x7f\x00\x00\x01', struct.pack('!I', COOKIE)))
            self.assertEqual(data[28:32], expected_ip)
        finally:
            transport.close()
            await asyncio.wait_for(closed, 3)
