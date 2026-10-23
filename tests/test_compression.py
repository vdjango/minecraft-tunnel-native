import asyncio
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
# Optional interoperability baseline, isolated from the launcher's C++ adapter.
# The actual native codec and both native endpoints are exercised in CTest and
# tests.test_bridge even when this archived Python reference is unavailable.
LEGACY = ROOT.parent / 'buggcraft' / 'compatibility'
LEGACY_AVAILABLE = (LEGACY / 'legacy_hookup' / 'compression.py').is_file()
sys.path[:0] = [str(LEGACY), str(ROOT / 'web')]
try:
    from legacy_hookup.compression import Codec, CompressionListener, BLOCK, read_length
    from legacy_hookup.relay import RelayProfile, TunnelHost, TunnelClient
except ModuleNotFoundError as exc:
    LEGACY_AVAILABLE = False
    _legacy_reason = 'Optional archived compression dependency missing: ' + str(exc.name)
    Codec = CompressionListener = RelayProfile = TunnelHost = TunnelClient = object
    BLOCK = 65536

    async def read_length(_reader):
        raise unittest.SkipTest(_legacy_reason)
from tests import test_native


class CodecTests(unittest.IsolatedAsyncioTestCase):
    async def unpack(self, data):
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        frame = await read_length(reader)
        kind, size = frame & 1, frame >> 1
        length = await read_length(reader) if kind else size
        return kind, length, await reader.readexactly(size)

    async def test_adaptive_compression_and_incompressible_sampling(self):
        codec, decoder = Codec(), Codec()
        repeated = b'chunk-palette-position=' * 2000
        kind, length, payload = await self.unpack(codec.encode(repeated))
        self.assertEqual(kind, 1)
        self.assertEqual(decoder.decode(kind, length, payload), repeated)
        self.assertLess(len(payload), len(repeated)//10)
        codec = Codec()
        for _ in range(40):
            raw = os.urandom(BLOCK)
            was_passthrough = codec.passthrough
            encoded = codec.encode(raw)
            if codec.passthrough:
                self.assertEqual(encoded, (b'' if was_passthrough else b'\0') + raw)
            else:
                kind, length, payload = await self.unpack(encoded)
                self.assertEqual(kind, 0)
                self.assertEqual(decoder.decode(kind, length, payload), raw)
        self.assertTrue(codec.passthrough)
        self.assertEqual(codec.encode(b'a'), b'a')

    async def test_tiny_packets_switch_to_unframed_direction(self):
        codec = Codec()
        for _ in range(65):
            codec.encode(b'keepalive')
        self.assertTrue(codec.passthrough)
        before = codec.wire_bytes - codec.raw_bytes
        for _ in range(1000):
            self.assertEqual(codec.encode(b'position'), b'position')
        self.assertEqual(codec.wire_bytes-codec.raw_bytes, before)

    async def test_corrupt_truncated_and_bomb_frames_are_bounded(self):
        import zstandard
        codec = Codec()
        for args in [(0, 2, b'a'), (2, 1, b'a'), (1, BLOCK+1, b'x'), (1, 1, b'bad')]:
            with self.subTest(args=args[:2]), self.assertRaises(ValueError):
                codec.decode(*args)
        bomb = zstandard.ZstdCompressor().compress(b'x' * (16 * BLOCK))
        with self.assertRaises(ValueError):
            codec.decode(1, BLOCK, bomb)
        for data in (b'\x80', b'\x80\x80\x80', b'\x81'):
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            with self.assertRaises(ValueError):
                await read_length(reader)


class CompressionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await test_native.NativeTests.asyncSetUp(self)
        self.endpoints = []

    async def asyncTearDown(self):
        for endpoint, _task in reversed(self.endpoints):
            await endpoint.stop()
        await asyncio.gather(*(task for _endpoint, task in self.endpoints), return_exceptions=True)
        await test_native.NativeTests.asyncTearDown(self)

    async def endpoint(self, cls, *args, **kwargs):
        profile = RelayProfile('127.0.0.1', self.relay.port, self.relay.token,
                               self.key.export_public_key().decode())
        endpoint = cls(profile, *args, announce=False, transport_policy='relay', **kwargs)
        ready = asyncio.Event()
        endpoint.on_status = lambda state, _detail: ready.set() if state == 'online' else None
        task = asyncio.create_task(endpoint.run())
        self.endpoints.append((endpoint, task))
        await asyncio.wait_for(ready.wait(), 10)
        return endpoint

    async def world(self):
        async def echo(reader, writer):
            try:
                while data := await reader.read(BLOCK):
                    writer.write(data)
                    await writer.drain()
                writer.write(b'after-eof')
                await writer.drain()
            except OSError:
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
        server = await asyncio.start_server(echo, '127.0.0.1', 0)
        self.servers.append(server)
        return server.sockets[0].getsockname()[1]

    async def exchange(self, client, payload):
        reader, writer = await asyncio.open_connection('127.0.0.1', client.listen_port)
        async def send():
            writer.write(payload)
            await writer.drain()
            writer.write_eof()
        task = asyncio.create_task(send())
        try:
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 15), payload)
            self.assertEqual(await asyncio.wait_for(reader.readexactly(9), 5), b'after-eof')
            await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()

    async def test_sdk_compresses_relay_preserves_eof_and_reduces_node_bytes(self):
        host = await self.endpoint(TunnelHost, game_port=await self.world())
        client = await self.endpoint(TunnelClient, host.invite, listen_port=0)
        self.assertIsInstance(client.listener, CompressionListener)
        before = self.relay.capacity.bytes
        payload = b'vanilla-chunk-palette-' * 100000
        await self.exchange(client, payload)
        await asyncio.sleep(1.1)
        self.assertLess(self.relay.capacity.bytes-before, len(payload)//5)
    async def test_stale_compression_offer_falls_back_before_game_bytes(self):
        host = await self.endpoint(TunnelHost, game_port=await self.world())
        client = await self.endpoint(TunnelClient, host.invite, listen_port=0)
        self.relay.compression = 'off'
        with self.assertLogs('legacy_hookup.compression', level='INFO') as logs:
            await self.exchange(client, os.urandom(131072))
        self.assertTrue(any('relay_compression_fallback' in line for line in logs.output))

    async def test_policy_off_uses_legacy_relay(self):
        self.relay.compression = 'off'
        host = await self.endpoint(TunnelHost, game_port=await self.world())
        client = await self.endpoint(TunnelClient, host.invite, listen_port=0)
        self.assertIsNone(host.compressed_listener)
        self.assertNotIsInstance(client.listener, CompressionListener)
        await self.exchange(client, os.urandom(131072))

    async def test_peer_loss_uses_compressed_relay_for_new_game_stream(self):
        from legacy_hookup.peer import PeerTransport, GameListener
        host = await self.endpoint(TunnelHost, game_port=await self.world())
        client = await self.endpoint(TunnelClient, host.invite, listen_port=0)
        peer = PeerTransport(client)
        listener = await GameListener.start(peer, 0)
        self.servers.append(listener)
        client.listen_port = listener.get_port()
        before = self.relay.capacity.bytes
        payload = b'game-after-peer-loss-' * 80000
        await self.exchange(client, payload)
        await asyncio.sleep(1.1)
        self.assertLess(self.relay.capacity.bytes-before, len(payload)//5)


if not LEGACY_AVAILABLE:
    _reason = globals().get('_legacy_reason', 'Optional archived Python compression reference is not installed')
    CodecTests.__unittest_skip__ = True
    CodecTests.__unittest_skip_why__ = _reason
    CompressionIntegrationTests.__unittest_skip__ = True
    CompressionIntegrationTests.__unittest_skip_why__ = _reason
