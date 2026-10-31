"""Test portable TCP/UDP fixture allocation with real sockets."""
import asyncio
import errno
import socket
import unittest
from unittest.mock import patch

from tests.network_proxy import TcpOnlyRelay


class EchoUdp(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, address):
        self.transport.sendto(data, address)


class RelayPortTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def echo(reader, writer):
            try:
                while data := await reader.read(4096):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        self.destination = await asyncio.start_server(echo, '127.0.0.1', 0)
        self.addAsyncCleanup(self.destination.wait_closed)
        self.addCleanup(self.destination.close)
        self.port = self.destination.sockets[0].getsockname()[1]

    async def assert_traffic(self, relay):
        reader, writer = await asyncio.open_connection('127.0.0.1', relay.port)
        try:
            writer.write(b'tcp-verified')
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(12), 2), b'tcp-verified')
        finally:
            writer.close()
            await writer.wait_closed()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            loop = asyncio.get_running_loop()
            await loop.sock_sendto(sock, b'udp-verified', ('127.0.0.1', relay.port))
            data, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 100), 2)
            self.assertEqual(data, b'udp-verified')
        finally:
            sock.close()

    async def test_tcp_and_udp_share_a_usable_port_and_close_together(self):
        relay = await TcpOnlyRelay(self.port).start('127.0.0.1', EchoUdp)
        self.addAsyncCleanup(relay.close)
        self.assertEqual(relay.port, relay.udp_transport.get_extra_info('sockname')[1])
        await self.assert_traffic(relay)
        await relay.close()
        await asyncio.sleep(0)
        self.assertTrue(relay.udp_transport.is_closing())
        self.assertFalse(relay.server.is_serving())

    async def test_denied_or_occupied_tcp_port_retries_and_keeps_both_protocols(self):
        original = asyncio.start_server
        for number in (10013, 10048):
            with self.subTest(winerror=number):
                calls = []
                failure = OSError('Windows port unavailable')
                failure.winerror = number

                async def start(*args, **kwargs):
                    calls.append(args[2])
                    if len(calls) == 1:
                        raise failure
                    return await original(*args, **kwargs)

                with patch('tests.network_proxy.asyncio.start_server', side_effect=start):
                    relay = await TcpOnlyRelay(self.port).start('127.0.0.1', EchoUdp)
                self.addAsyncCleanup(relay.close)
                self.assertEqual(len(calls), 2)
                await self.assert_traffic(relay)
                await relay.close()

    async def test_repeated_port_failure_is_bounded_and_closes_udp_sockets(self):
        loop = asyncio.get_running_loop()
        original = loop.create_datagram_endpoint
        transports = []

        async def endpoint(*args, **kwargs):
            transport, protocol = await original(*args, **kwargs)
            transports.append(transport)
            return transport, protocol

        with (patch.object(loop, 'create_datagram_endpoint', side_effect=endpoint),
              patch('tests.network_proxy.asyncio.start_server', side_effect=PermissionError(errno.EACCES, 'denied')) as start):
            with self.assertRaises(PermissionError):
                await TcpOnlyRelay(self.port).start('127.0.0.1', EchoUdp)
        self.assertEqual(start.call_count, 16)
        self.assertTrue(all(transport.is_closing() for transport in transports))


if __name__ == '__main__':
    unittest.main()
