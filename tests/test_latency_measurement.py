"""Measure echo RTT separately from login, subsystem setup and directory work."""
import asyncio
import json
import time
import unittest
from unittest.mock import patch

from tests import test_bridge as fixture
from tunnel.directory import RoomControlSession


class LatencyMeasurementTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.BridgeTests.asyncSetUp
    process = fixture.BridgeTests.process
    call = fixture.BridgeTests.call
    add = fixture.BridgeTests.add

    async def test_slow_directory_list_does_not_become_node_ping(self):
        bridge = await self.process()
        node = await self.add(bridge)
        dispatch = RoomControlSession.dispatch
        pings = []

        def delayed(session, request):
            if request.get('op') == 'list':
                time.sleep(.4)
            elif request.get('op') == 'ping':
                pings.append(1)
                time.sleep(.04)
            return dispatch(session, request)

        with patch.object(RoomControlSession, 'dispatch', delayed):
            page = await self.call(bridge, 'room.list', dict(node=node))
        self.assertEqual(len(pings), 3)
        self.assertEqual(page['node_latency_method'], 'application-echo')
        self.assertGreaterEqual(page['node_latency_ms'], 35)
        self.assertLess(page['node_latency_ms'], 250)
        self.assertGreater(page['connect_ms'] - page['node_latency_ms'], 350)
        info = await self.call(bridge, 'node.request', dict(node=node, request={'op': 'ping'}))
        self.assertEqual(info['node_latency_method'], 'application-echo')
        self.assertGreaterEqual(info['node_latency_ms'], 0)

    async def test_active_room_reports_node_echo_independently_of_unavailable_game_ping(self):
        bridge = await self.process()
        node = await self.add(bridge)
        hosted = await self.call(bridge, 'room.host', dict(node=node, game_port=0, transport='relay'))
        async with asyncio.timeout(10):
            while True:
                events = (await self.call(bridge, 'session.events', dict(session=hosted['session'])))['events']
                samples = [event for event in events if event.get('type') == 'node-latency']
                if samples:
                    self.assertEqual(samples[-1]['method'], 'application-echo')
                    self.assertGreaterEqual(samples[-1]['latency_ms'], 0)
                    break
                await asyncio.sleep(.05)

    async def test_host_game_ping_traverses_relay_and_includes_game_response_delay(self):
        async def frame(reader):
            size = shift = 0
            while True:
                byte = (await reader.readexactly(1))[0]
                size |= (byte & 127) << shift
                if not byte & 128:
                    break
                shift += 7
            return await reader.readexactly(size)

        def integer(value):
            result = bytearray()
            while value >= 128:
                result.append((value & 127) | 128)
                value >>= 7
            return bytes(result) + bytes([value])

        async def game(reader, writer):
            try:
                await frame(reader)  # Minecraft status handshake, through the real tunnel.
                self.assertEqual(await frame(reader), b'\x00')
                status = json.dumps(dict(version=dict(name='test', protocol=774),
                                         players=dict(online=1, max=10))).encode()
                payload = b'\x00' + integer(len(status)) + status
                writer.write(integer(len(payload)) + payload)
                await writer.drain()
                ping = await frame(reader)
                self.assertEqual(ping[:1], b'\x01')
                await asyncio.sleep(.09)
                writer.write(integer(len(ping)) + ping)
                await writer.drain()
            except (asyncio.IncompleteReadError, OSError):
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(game, '127.0.0.1', 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', dict(node=hn,
            game_port=server.sockets[0].getsockname()[1], transport='relay'))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite'], transport='relay'))
        async with asyncio.timeout(10):
            while True:
                events = (await self.call(guest, 'session.events', dict(session=joined['session'])))['events']
                samples = [event for event in events if event.get('type') == 'latency'
                           and isinstance(event.get('latency_ms'), int)]
                if samples:
                    self.assertEqual(samples[-1]['method'], 'minecraft-status-ping')
                    self.assertGreaterEqual(samples[-1]['latency_ms'], 80)
                    self.assertLess(samples[-1]['latency_ms'], 500)
                    break
                await asyncio.sleep(.05)
