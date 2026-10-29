"""A relay without UDP must fall back to a reachable topology STUN server."""
import asyncio
import base64
import ipaddress
import json
import socket
import struct
import unittest

from tests import test_bridge as fixture
from tests.network_proxy import BlackholeProxy


class TcpOnlyRelay(BlackholeProxy):
    async def start(self, host):
        self.server = await asyncio.start_server(self.accept, host, 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self


class DropUdp(asyncio.DatagramProtocol):
    packets = 0

    def datagram_received(self, data, address):
        self.packets += 1


class SlowGate:
    async def wait(self):
        await asyncio.sleep(0.08)


class StunResponder(asyncio.DatagramProtocol):
    def datagram_received(self, data, address):
        if len(data) < 20 or data[:2] != b'\x00\x01' or data[4:8] != b'\x21\x12\xa4\x42':
            return
        mapped_ip = int(ipaddress.IPv4Address('198.51.100.23')) ^ 0x2112A442
        mapped_port = address[1] ^ 0x2112
        attribute = struct.pack('!HHBBHI', 0x0020, 8, 0, 1, mapped_port, mapped_ip)
        response = struct.pack('!HHI', 0x0101, len(attribute), 0x2112A442) + data[8:20] + attribute
        self.transport.sendto(response, address)

    def connection_made(self, transport):
        self.transport = transport


class PeerStunFallbackTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    wait_direct = fixture.BridgeTests.wait_direct
    exchange = fixture.BridgeTests.exchange

    async def add_with_stun_alternative(self, process, stun_port):
        code = 'buggnode1-' + base64.urlsafe_b64encode(
            json.dumps(self.profile).encode()).decode().rstrip('=')
        alternative = dict(self.profile, server_port=stun_port)
        return (await self.call(process, 'node.add', {
            'code': code, 'alternatives': [alternative]}))['node']

    def session_rows(self, process, session):
        return [json.loads(line)
                for path in process.player_logs.glob('*/' + session + '.jsonl')
                for line in path.read_text(encoding='utf-8').splitlines()]

    async def test_relay_without_udp_uses_responsive_topology_stun(self):
        loop = asyncio.get_running_loop()
        addresses = socket.gethostbyname_ex(socket.gethostname())[2]
        local_ip = next((ip for ip in addresses
                         if not ipaddress.ip_address(ip).is_loopback), None)
        if local_ip is None:
            self.skipTest('A non-loopback IPv4 interface is required for ICE')

        proxy = await TcpOnlyRelay(self.node.port).start(local_ip)
        self.addAsyncCleanup(proxy.close)
        dropped_transport, dropped = await loop.create_datagram_endpoint(
            DropUdp, local_addr=(local_ip, proxy.port))
        self.addCleanup(dropped_transport.close)
        self.profile = dict(self.profile, server_host=local_ip, server_port=proxy.port)

        # Topology alternatives must also pass authenticated TCP routing
        # probes. Keep this route slower so the TCP-only relay stays selected.
        alternative = await TcpOnlyRelay(self.node.port).start(local_ip)
        alternative.allowed = [SlowGate(), SlowGate()]
        self.addAsyncCleanup(alternative.close)
        transport, _ = await loop.create_datagram_endpoint(
            StunResponder, local_addr=(local_ip, alternative.port))
        self.addCleanup(transport.close)
        stun_port = transport.get_extra_info('sockname')[1]

        host, guest = await self.process(), await self.process()
        host_node = await self.add_with_stun_alternative(host, stun_port)
        guest_node = await self.add_with_stun_alternative(guest, stun_port)
        for process, node in ((host, host_node), (guest, guest_node)):
            selected = await self.call(process, 'node.selected', {'node': node})
            self.assertEqual(selected['profile']['server_port'], proxy.port)
            self.assertEqual(len(selected['backups']), 1)
        hosted = await self.call(host, 'room.host', {
            'node': host_node, 'game_port': self.port})
        joined = await self.call(guest, 'room.join', {
            'node': guest_node, 'invite': hosted['invite']})
        direct = await self.wait_direct(guest, joined)
        self.assertEqual(direct['transport'], 'p2p')
        await self.exchange(direct)
        self.assertGreaterEqual(dropped.packets, 4)

        for process, session in ((host, hosted['session']), (guest, joined['session'])):
            rows = self.session_rows(process, session)
            selected = [row for row in rows if row['event'] == 'peer_stun_servers_ready']
            self.assertEqual(len(selected), 1, rows)
            self.assertEqual(selected[0]['count'], 1)
            self.assertEqual(selected[0]['port'], stun_port)
            candidates = [row for row in rows if row['event'] == 'peer_candidates_ready']
            self.assertTrue(any(row['candidates']['srflx'] for row in candidates), rows)
            paths = [row['path'] for row in rows if row['event'] == 'game_path']
            self.assertIn('p2p', paths)


if __name__ == '__main__':
    unittest.main(verbosity=2)
