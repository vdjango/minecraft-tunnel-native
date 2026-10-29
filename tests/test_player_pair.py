"""Repeatable A-host/B-join timings and idle/saturated native transport RTT.

Run this explicit test:
python -m unittest tests.test_player_pair.PlayerPairTests.test_pair_performance -v
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import struct
import time
import unittest

from tests import test_bridge as bridges


def distribution(values):
    values = sorted(values)
    def percentile(p):
        return round(values[min(len(values) - 1, int((len(values) - 1) * p))], 3)
    return dict(samples=len(values), p50=percentile(.5), p95=percentile(.95),
                p99=percentile(.99), maximum=round(values[-1], 3))


class PlayerPairTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = bridges.BridgeTests.asyncSetUp
    process = bridges.BridgeTests.process
    call = bridges.BridgeTests.call
    add = bridges.BridgeTests.add
    exchange = bridges.BridgeTests.exchange

    async def test_pair_performance(self):
        # Debug stack collection measures the Python driver, not the native path.
        asyncio.get_running_loop().set_debug(False)
        seconds = float(os.environ.get('BUGGCRAFT_PAIR_SECONDS', '30'))
        self.assertGreaterEqual(seconds, 5)
        output = Path(os.environ.get('BUGGCRAFT_PAIR_RESULT', self.logs / 'pair.json'))
        report = dict(passed=False, platform=platform.platform(), measured_seconds_per_mode=seconds,
                      scope='Loopback native node and two independent native bridges; echo application',
                      payload_bytes=65536, bulk_streams=4, window_blocks_per_stream=16,
                      player_logs=str(self.logs), modes={})
        for name in ('buggcraft-core', 'buggcraft-bridge'):
            variable = 'BUGGCRAFT_NATIVE_CORE' if name.endswith('core') else 'BUGGCRAFT_BRIDGE_EXECUTABLE'
            binary = Path(os.environ.get(variable, bridges.ROOT / 'build' / (name + ('.exe' if os.name == 'nt' else ''))))
            report.setdefault('sha256', {})[name] = hashlib.sha256(binary.read_bytes()).hexdigest()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        host, guest = await self.process(), await self.process()
        async def timed(process, method, params):
            started = time.perf_counter()
            result = await self.call(process, method, params)
            return result, (time.perf_counter() - started) * 1000
        start = time.perf_counter()
        hn = await self.add(host)
        report['a_node_add_ms'] = round((time.perf_counter() - start) * 1000, 3)
        start = time.perf_counter()
        gn = await self.add(guest)
        report['b_node_add_ms'] = round((time.perf_counter() - start) * 1000, 3)

        async def measure(port):
            async def probes(duration=None, count=None):
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.write(bridges.GAME_HANDSHAKE); await writer.drain()
                self.assertEqual(await reader.readexactly(len(bridges.GAME_HANDSHAKE)), bridges.GAME_HANDSHAKE)
                values = []
                deadline = time.perf_counter() + duration if duration else None
                try:
                    while (len(values) < count) if count else (time.perf_counter() < deadline):
                        payload = struct.pack('!Q', len(values)) + b'game-position' * 10
                        started = time.perf_counter()
                        writer.write(payload)
                        await writer.drain()
                        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 10), payload)
                        values.append((time.perf_counter() - started) * 1000)
                        await asyncio.sleep(.01)
                    writer.write_eof()
                    self.assertEqual(await asyncio.wait_for(reader.read(), 10), b'')
                finally:
                    writer.close()
                    await writer.wait_closed()
                return distribution(values)
            idle = await probes(count=150)
            end = time.perf_counter() + seconds
            started = time.perf_counter()
            async def bulk(index):
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                writer.write(bridges.GAME_HANDSHAKE); await writer.drain()
                self.assertEqual(await reader.readexactly(len(bridges.GAME_HANDSHAKE)), bridges.GAME_HANDSHAKE)
                body = secrets.token_bytes(65536 - 8)
                window = asyncio.Semaphore(16)
                sent = received = 0
                gaps = []
                async def sender():
                    nonlocal sent
                    while time.perf_counter() < end:
                        await window.acquire()
                        writer.write(struct.pack('!Q', sent) + body)
                        await writer.drain()
                        sent += 1
                    writer.write_eof()
                sending = asyncio.create_task(sender())
                last = time.perf_counter()
                try:
                    while True:
                        try:
                            payload = await asyncio.wait_for(reader.readexactly(65536), 15)
                        except asyncio.IncompleteReadError as error:
                            self.assertEqual(error.partial, b'')
                            break
                        now = time.perf_counter()
                        gaps.append((now - last) * 1000)
                        last = now
                        self.assertEqual(payload, struct.pack('!Q', received) + body)
                        received += 1
                        window.release()
                    await sending
                    self.assertEqual(sent, received)
                finally:
                    sending.cancel()
                    await asyncio.gather(sending, return_exceptions=True)
                    writer.close()
                    await writer.wait_closed()
                return dict(stream=index, verified_bytes=received * 65536,
                            receive_gap_ms=distribution(gaps),
                            gaps_over_100ms=sum(value > 100 for value in gaps),
                            gaps_over_250ms=sum(value > 250 for value in gaps),
                            gaps_over_1000ms=sum(value > 1000 for value in gaps))
            # Four unthrottled streams plus one small-packet probe, all from B to A.
            tasks = [asyncio.create_task(bulk(index)) for index in range(4)]
            try:
                loaded = await probes(duration=seconds)
                streams = await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            elapsed = time.perf_counter() - started
            return dict(idle_rtt_ms=idle, saturated_rtt_ms=loaded,
                        elapsed_seconds=round(elapsed, 3),
                        one_direction_mib_per_second=round(sum(row['verified_bytes'] for row in streams) / elapsed / 1024**2, 3),
                        echo_roundtrip_verified_mib=round(sum(row['verified_bytes'] for row in streams) / 1024**2, 3),
                        streams=streams)
        try:
            if not os.environ.get('BUGGCRAFT_PAIR_SKIP_BASELINE'):
                report['direct_tcp'] = await measure(self.port)
            for policy in os.environ.get('BUGGCRAFT_PAIR_MODES', 'auto,relay').split(','):
                host_times, join_times, first_times = [], [], []
                for repeat in range(5):
                    hosted, host_ms = await timed(host, 'room.host', dict(node=hn, game_port=self.port, transport=policy))
                    joined, join_ms = await timed(guest, 'room.join', dict(node=gn, invite=hosted['invite'], transport=policy))
                    expected = 'p2p' if policy == 'auto' else 'relay'
                    self.assertEqual(joined['transport'], expected)
                    before = time.perf_counter()
                    reader, writer = await asyncio.open_connection('127.0.0.1', int(joined['address'].rsplit(':', 1)[1]))
                    try:
                        first = bridges.GAME_HANDSHAKE + b'first-game-packet' * 8
                        writer.write(first)
                        await writer.drain()
                        self.assertEqual(await asyncio.wait_for(reader.readexactly(len(first)), 10), first)
                        # Stop at the first reply; EOF/close is a separate path.
                        first_times.append((time.perf_counter() - before) * 1000)
                        writer.write_eof()
                        self.assertEqual(await asyncio.wait_for(reader.read(), 10), b'')
                    finally:
                        writer.close()
                        await writer.wait_closed()
                    host_times.append(host_ms)
                    join_times.append(join_ms)
                    if repeat == 4:
                        measurements = await measure(int(joined['address'].rsplit(':', 1)[1]))
                    await self.call(guest, 'room.close', dict(session=joined['session']))
                    await self.call(host, 'room.close', dict(session=hosted['session']))
                report['modes'][expected] = dict(a_create_room_ms=distribution(host_times),
                    b_join_room_ms=distribution(join_times), first_connection_echo_ms=distribution(first_times),
                    **measurements)
                print('PAIR_MODE ' + json.dumps(dict(mode=expected, **report['modes'][expected])), flush=True)
            report['passed'] = True
        finally:
            output.write_text(json.dumps(report, indent=2), encoding='utf-8')
            print('PAIR_ARTIFACT ' + str(output), flush=True)
