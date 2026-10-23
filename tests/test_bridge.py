"""Native launcher processes talking to real SSH node and TCP game sockets."""
import asyncio
import base64
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
from tunnel.native_relay import RelayServer
import asyncssh
from tunnel.diagnostics import Diagnostics
from tests.network_proxy import BlackholeProxy

class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        retained = os.environ.get('BUGGCRAFT_BRIDGE_SOAK_LOG_DIR')
        if retained:
            Path(retained).mkdir(parents=True, exist_ok=True)
            self.logs = Path(tempfile.mkdtemp(prefix='players-', dir=retained))
        else:
            self.temporary = tempfile.TemporaryDirectory(prefix='buggcraft-native-bridge-')
            self.logs = Path(self.temporary.name)
            self.addCleanup(self.temporary.cleanup)
        self.key = asyncssh.generate_private_key('ssh-ed25519')
        self.node = await RelayServer('127.0.0.1', 0, host_key=self.key,
            token=secrets.token_urlsafe(48), workers=2).start()
        self.node.diagnostics = Diagnostics(self.logs / 'node', self.node)
        lifecycle = self.node.diagnostics.lifecycle(None)
        await anext(lifecycle)
        async def close_node_logs():
            try:
                await self.node.close()
            finally:
                await lifecycle.aclose()
        self.addAsyncCleanup(close_node_logs)
        self.process_count = 0
        self.profile = dict(server_host='127.0.0.1', server_port=self.node.port,
            token=self.node.token, server_public_key=self.key.export_public_key().decode())
        async def echo(reader, writer):
            try:
                while data := await reader.read(65536):
                    writer.write(data); await writer.drain()
                writer.write_eof()
            except (OSError, asyncio.CancelledError):
                pass
            finally:
                writer.close()
                try: await writer.wait_closed()
                except OSError: pass
        self.game = await asyncio.start_server(echo, '127.0.0.1', 0)
        self.port = self.game.sockets[0].getsockname()[1]
        self.addAsyncCleanup(self.game.wait_closed)
        self.addCleanup(self.game.close)

    async def process(self):
        self.process_count += 1
        logs = self.logs / ('bridge-' + str(self.process_count))
        binary = os.environ.get('BUGGCRAFT_BRIDGE_EXECUTABLE', str(ROOT/'build'/('buggcraft-bridge.exe' if os.name=='nt' else 'buggcraft-bridge')))
        command = getattr(self, 'bridge_command', None) or [binary]
        process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=dict(os.environ, BUGGCRAFT_BRIDGE_LOG_DIR=str(logs)))
        process.player_logs = logs
        process.expected_returncode = 0
        process.errors = asyncio.create_task(process.stderr.read())
        async def cleanup():
            if process.returncode is None:
                process.stdin.close()
                try: await asyncio.wait_for(process.wait(), 15)
                except TimeoutError: process.kill(); await process.wait()
            errors = await process.errors
            self.assertEqual(process.returncode, process.expected_returncode, errors.decode(errors='replace'))
            self.assertNotIn(self.node.token.encode(), errors)
            for path in logs.glob('*/*.jsonl'):
                text = path.read_text(encoding='utf-8')
                self.assertNotIn(self.node.token, text)
                for line in text.splitlines():
                    row = json.loads(line)
                    self.assertEqual(row['player'], path.parent.name)
                    self.assertNotIn('payload', row)
                    self.assertNotIn('password', row)
            if os.environ.get('BUGGCRAFT_BRIDGE_TEST_LOG'):
                with open(os.environ['BUGGCRAFT_BRIDGE_TEST_LOG'], 'ab') as output: output.write(errors)
        self.addAsyncCleanup(cleanup)
        return process

    async def call(self, process, method, params=None):
        process.stdin.write((json.dumps(dict(jsonrpc='2.0', id=1, method=method, params=params or {}))+'\n').encode())
        await process.stdin.drain()
        raw = await asyncio.wait_for(process.stdout.readline(), 35)
        self.assertTrue(raw, 'Native bridge exited')
        result = json.loads(raw)
        self.assertEqual(result['id'], 1)
        self.assertNotIn('error', result, result)
        return result['result']

    async def add(self, process):
        encoded = base64.urlsafe_b64encode(json.dumps(self.profile).encode()).decode().rstrip('=')
        return (await self.call(process,'node.add',{'code':'buggnode1-'+encoded}))['node']

    async def wait_direct(self, process, joined):
        # A real first negotiation can time out on a multi-interface machine.
        # Require the native retry to restore P2P; relay alone cannot pass.
        async with asyncio.timeout(45):
            while True:
                state = next(row for row in (await self.call(process, 'session.list'))['sessions']
                             if row['session'] == joined['session'])
                self.assertEqual(state['state'], 'online')
                if state['transport'] == 'p2p':
                    return dict(joined, **state)
                await asyncio.sleep(.1)

    async def exchange(self, joined, data=None):
        data = data if data is not None else secrets.token_bytes(1024*1024)
        reader, writer = await asyncio.open_connection('127.0.0.1',int(joined['address'].rsplit(':',1)[1]))
        async def send():
            writer.write(data); await writer.drain(); writer.write_eof()
        task = asyncio.create_task(send())
        try:
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(data)),20),data)
            await task
            self.assertEqual(await asyncio.wait_for(reader.read(),8),b'')
        finally:
            task.cancel(); await asyncio.gather(task,return_exceptions=True)
            writer.close(); await writer.wait_closed()

    async def test_relay_auto_fallback_compression_and_half_close(self):
        process = await self.process(); node = await self.add(process)
        hosted = await self.call(process,'room.host',{'node':node,'game_port':self.port,'transport':'relay'})
        joined = await self.call(process,'room.join',{'node':node,'invite':hosted['invite']})
        self.assertEqual(joined['transport'],'relay')
        await self.exchange(joined)
        await self.exchange(joined,b'minecraft terrain data '*60000)
        await self.call(process,'room.close',{'session':joined['session']})
        with self.assertRaises(OSError):
            await asyncio.open_connection('127.0.0.1',int(joined['address'].rsplit(':',1)[1]))
        await self.call(process,'room.close',{'session':hosted['session']})
        self.assertNotIn(hosted['room'],self.node.rooms)

    async def test_two_native_processes_mixed_p2p_relay(self):
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host,'room.host',{'node':hn,'game_port':self.port})
        direct = await self.call(guest,'room.join',{'node':gn,'invite':hosted['invite']})
        self.assertEqual(direct['transport'],'p2p')
        relayed = await self.call(guest,'room.join',{'node':gn,'invite':hosted['invite'],'transport':'relay'})
        await asyncio.gather(self.exchange(direct),self.exchange(relayed))
        await self.call(host,'room.close',{'session':hosted['session']})

    async def test_compressed_tail_drains_after_remote_channel_close(self):
        host, guest = await self.process(), await self.process()
        hn, gn = await self.add(host), await self.add(guest)
        hosted = await self.call(host, 'room.host', dict(node=hn, game_port=self.port, transport='relay'))
        joined = await self.call(guest, 'room.join', dict(node=gn, invite=hosted['invite'], transport='relay'))
        # Highly compressible responses fit in one SSH read but expand across
        # several socket writes. CLOSE must not discard the remaining tail.
        for size in ([245760, 262144 + 173, 2 * 1024 * 1024 + 49152] * 6):
            pattern = b'compressed EOF boundary '
            await self.exchange(joined, (pattern * ((size + len(pattern) - 1) // len(pattern)))[:size])
        reader, writer = await asyncio.open_connection('127.0.0.1', int(joined['address'].rsplit(':', 1)[1]))
        payload = b'last compressed frame' * 200000
        async def send():
            writer.write(payload)
            await writer.drain()
            writer.write_eof()
        sending = asyncio.create_task(send())
        try:
            await asyncio.sleep(.15)
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 20), payload)
            await sending
            self.assertEqual(await asyncio.wait_for(reader.read(), 5), b'')
        finally:
            sending.cancel()
            await asyncio.gather(sending, return_exceptions=True)
            writer.close()
            await writer.wait_closed()

    async def test_fragmented_maximum_raw_frame_keeps_receiving(self):
        # The handshake and first 65536 bytes of a 65539-byte frame can arrive
        # separately. The receiver must read the last three bytes, even when
        # its decoder already holds a whole payload block.
        from tunnel.relay import room_id
        secret = secrets.token_urlsafe(32)
        host = await asyncssh.connect('127.0.0.1', self.node.port,
            username='host:' + room_id(secret),
            password=json.dumps(dict(token=self.node.token, secret=secret)),
            known_hosts=([self.key.convert_to_public()], [], []),
            client_keys=[], agent_path=None, config=None)
        self.addAsyncCleanup(host.wait_closed)
        self.addCleanup(host.close)
        payload = secrets.token_bytes(65536)
        # Uncompressed BCZ1 frame length = payload length << 1 (varint).
        wire = b'\x80\x80\x08' + payload
        async def serve(reader, writer):
            try:
                writer.write(b'BCZ1')
                await writer.drain()
                await asyncio.sleep(.1)
                writer.write(wire[:65536])
                await writer.drain()
                await asyncio.sleep(.1)
                writer.write(wire[65536:])
                await writer.drain()
                await reader.read()
                writer.write_eof()
            finally:
                writer.close()
        listener = await host.start_server(lambda *_: serve, 'buggcraft-zstd', 0, encoding=None)
        self.addAsyncCleanup(listener.wait_closed)
        self.addCleanup(listener.close)
        guest = await self.process()
        node = await self.add(guest)
        joined = await self.call(guest, 'room.join', dict(node=node, invite='bugg1-' + secret, transport='relay'))
        reader, writer = await asyncio.open_connection('127.0.0.1', int(joined['address'].rsplit(':', 1)[1]))
        try:
            self.assertEqual(await asyncio.wait_for(reader.readexactly(len(payload)), 2), payload)
            writer.write_eof()
            self.assertEqual(await asyncio.wait_for(reader.read(), 2), b'')
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_errors_identity_pin_and_frame_budget(self):
        process = await self.process()
        self.profile['server_public_key'] = asyncssh.generate_private_key('ssh-ed25519').export_public_key().decode()
        code='buggnode1-'+base64.urlsafe_b64encode(json.dumps(self.profile).encode()).decode().rstrip('=')
        process.stdin.write((json.dumps(dict(jsonrpc='2.0',id=7,method='node.add',params={'code':code}))+'\n').encode())
        result=json.loads(await asyncio.wait_for(process.stdout.readline(),10))
        self.assertEqual(result['error']['code'],-32030)
        process.stdin.write(b'{bad\n')
        self.assertEqual(json.loads(await process.stdout.readline())['error']['code'],-32700)
        process.stdin.write(b'x'*8193);await process.stdin.drain()
        self.assertEqual(json.loads(await process.stdout.readline())['error']['code'],-32600)
        await asyncio.wait_for(process.wait(),10)
        self.assertEqual(process.returncode,0)

    async def test_blackholed_guest_times_out_without_stopping_other_players(self):
        host, guest, survivor = await self.process(), await self.process(), await self.process()
        hn = await self.add(host)
        hosted = await self.call(host, 'room.host', {'node':hn, 'game_port':self.port, 'transport':'relay'})
        proxy = await BlackholeProxy(self.node.port).start()
        self.addAsyncCleanup(proxy.close)
        original = self.profile
        self.profile = dict(original, server_port=proxy.port)
        try:
            gn = await self.add(guest)
        finally:
            self.profile = original
        joined = await self.call(guest, 'room.join', {'node':gn, 'invite':hosted['invite'], 'transport':'relay'})
        guest_peer = next(peer for peer in self.node.peers if peer.role == 'client')
        sn = await self.add(survivor)
        stable = await self.call(survivor, 'room.join', {'node':sn, 'invite':hosted['invite'], 'transport':'relay'})
        await self.exchange(joined, b'before-blackhole')
        proxy.mute()
        started = time.monotonic()
        async with asyncio.timeout(25):
            while True:
                await self.exchange(stable, b'surviving-player' * 32)
                state = (await self.call(guest, 'session.list'))['sessions'][0]
                if state['state'] in ('reconnecting', 'stopped'):
                    break
                await asyncio.sleep(.2)
            while guest_peer in self.node.peers:
                await asyncio.sleep(.05)
        self.assertEqual(state['reason'], 'heartbeat-timeout')
        self.assertEqual(guest_peer.disconnect_reason, 'heartbeat-timeout')
        self.assertLess(time.monotonic() - started, 25)
        self.assertEqual(sum(p.role == 'client' for p in self.node.peers), 1)
        self.assertTrue(any('heartbeat_timeout' in p.read_text(encoding='utf-8')
                            for p in guest.player_logs.glob('*/*.jsonl')))
        await self.exchange(stable)
        await self.call(guest, 'room.close', {'session':joined['session']})

    async def test_forced_guest_exit_releases_capacity_without_closing_room(self):
        host, guest, survivor = await self.process(), await self.process(), await self.process()
        hn = await self.add(host)
        hosted = await self.call(host, 'room.host', {'node':hn, 'game_port':self.port, 'transport':'relay'})
        gn, sn = await self.add(guest), await self.add(survivor)
        await self.call(guest, 'room.join', {'node':gn, 'invite':hosted['invite'], 'transport':'relay'})
        stable = await self.call(survivor, 'room.join', {'node':sn, 'invite':hosted['invite'], 'transport':'relay'})
        guest.kill()
        await asyncio.wait_for(guest.wait(), 5)
        guest.expected_returncode = guest.returncode
        self.assertNotEqual(guest.returncode, 0)
        async with asyncio.timeout(5):
            while sum(p.role == 'client' for p in self.node.peers) != 1:
                await asyncio.sleep(.02)
        self.assertIn(hosted['room'], self.node.rooms)
        await self.exchange(stable)

    async def test_three_players_continuous_load_latency_and_log_isolation(self):
        duration = float(os.environ.get('BUGGCRAFT_BRIDGE_SOAK_SECONDS', '8'))
        bulk_rate = float(os.environ.get('BUGGCRAFT_BRIDGE_SOAK_BULK_RATE', '96'))
        bulk_streams = int(os.environ.get('BUGGCRAFT_BRIDGE_SOAK_BULK_STREAMS', '1'))
        self.assertTrue(0 <= bulk_rate <= 10000 and 1 <= bulk_streams <= 5)
        host = await self.process()
        node = await self.add(host)
        hosted = await self.call(host, 'room.host', {
            'node': node, 'game_port': self.port, 'transport': 'relay'})
        guests, joined = [], []
        for _ in range(3):
            guest = await self.process()
            guest_node = await self.add(guest)
            joined.append(await self.call(guest, 'room.join', {
                'node': guest_node, 'invite': hosted['invite'], 'transport': 'relay'}))
            guests.append(guest)
        identities = [
            (await self.call(guest, 'room.request', {
                'session': session['session'], 'request': {'op': 'ping'}}))['diagnostic_player']
            for guest, session in zip(guests, joined)
        ]
        self.assertEqual(len(set(identities)), 3, 'Anonymous players sharing an invite were merged')
        started_at = time.perf_counter()
        deadline = started_at + duration
        measurements = [{'bytes': 0, 'rtt_ms': [], 'bulk_max_gap_ms': 0,
                         'bulk_blocks': 0, 'bulk_gaps_over_100_ms': 0,
                         'bulk_gaps_over_250_ms': 0, 'bulk_gaps_over_1000_ms': 0}
                        for _ in joined]
        churn_count = 0

        async def transfer(index, probe):
            port = int(joined[index]['address'].rsplit(':', 1)[1])
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            payload = (b'position-update' * 8 if probe else
                       (b'chunk-data' * 6554)[:65536] if index == 0 else secrets.token_bytes(65536))
            previous = time.perf_counter()
            try:
                while time.perf_counter() < deadline:
                    started = time.perf_counter()
                    writer.write(payload)
                    await writer.drain()
                    data = await asyncio.wait_for(reader.readexactly(len(payload)), 5)
                    self.assertEqual(data, payload, 'Sustained transfer corrupted bytes')
                    ended = time.perf_counter()
                    if probe:
                        measurements[index]['rtt_ms'].append((ended - started) * 1000)
                        await asyncio.sleep(.02)
                    else:
                        measurements[index]['bytes'] += len(data)
                        gap = (ended - previous) * 1000
                        measurements[index]['bulk_max_gap_ms'] = max(measurements[index]['bulk_max_gap_ms'], gap)
                        measurements[index]['bulk_blocks'] += 1
                        for threshold in (100, 250, 1000):
                            measurements[index][f'bulk_gaps_over_{threshold}_ms'] += gap > threshold
                        if bulk_rate:
                            await asyncio.sleep(max(0, 1 / bulk_rate - (ended - started)))
                        previous = ended
                writer.write_eof()
                self.assertEqual(await asyncio.wait_for(reader.read(), 5), b'')
            finally:
                writer.close()
                await writer.wait_closed()

        async def churn_and_control():
            nonlocal churn_count
            while time.perf_counter() < deadline:
                await asyncio.sleep(.5)
                await self.exchange(joined[0], b'new-player-stream' * 1024)
                await self.call(host, 'room.request', {
                    'session': hosted['session'], 'request': {'op': 'describe'}})
                churn_count += 1

        stopped = asyncio.Event()
        async def progress():
            previous_bytes = [0] * len(measurements)
            previous_samples = [0] * len(measurements)
            previous_at = started_at
            while True:
                try:
                    await asyncio.wait_for(stopped.wait(), 30)
                    return
                except TimeoutError:
                    pass
                now = time.perf_counter()
                players = []
                for index, result in enumerate(measurements):
                    recent = sorted(result['rtt_ms'][previous_samples[index]:])
                    players.append(dict(mib=round(result['bytes'] / 1024**2, 2),
                        mib_s=round((result['bytes'] - previous_bytes[index]) / (now - previous_at) / 1024**2, 2),
                        recent_rtt_p99_ms=round(recent[min(len(recent) - 1, int(len(recent) * .99))], 3) if recent else None,
                        recent_rtt_max_ms=round(max(recent), 3) if recent else None,
                        bulk_max_gap_ms=round(result['bulk_max_gap_ms'], 3)))
                    previous_bytes[index] = result['bytes']
                    previous_samples[index] = len(result['rtt_ms'])
                status = dict(elapsed_seconds=round(now - started_at, 1), target_seconds=duration,
                              churn_connections=churn_count, players=players)
                print('NATIVE_SOAK_PROGRESS ' + json.dumps(status), flush=True)
                output = os.environ.get('BUGGCRAFT_BRIDGE_SOAK_RESULT')
                if output:
                    Path(output).with_suffix('.progress.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
                previous_at = now

        observer = asyncio.create_task(progress())
        try:
            await asyncio.wait_for(asyncio.gather(
                *(transfer(i, False) for i in range(3) for _ in range(bulk_streams)),
                *(transfer(i, True) for i in range(3)), churn_and_control()), duration + 15)
        finally:
            stopped.set()
            await observer
        report = {'duration_seconds': duration, 'clients': 3, 'game_protocol': False,
                  'elapsed_seconds': round(time.perf_counter() - started_at, 3),
                  'path': 'native-relay', 'bulk_streams_per_client': bulk_streams,
                  'bulk_rate_per_stream': bulk_rate, 'churn_connections': churn_count,
                  'player_logs': str(self.logs), 'players': []}
        max_rtt = float(os.environ.get('BUGGCRAFT_BRIDGE_MAX_LOCAL_RTT_MS', '1000'))
        for result in measurements:
            samples = sorted(result['rtt_ms'])
            self.assertGreater(len(samples), 20)
            self.assertGreater(result['bytes'], 1024 * 1024)
            report['players'].append({
                'mib': round(result['bytes'] / 1024**2, 2),
                'rtt_p50_ms': round(samples[len(samples) // 2], 3),
                'rtt_p99_ms': round(samples[min(len(samples) - 1, int(len(samples) * .99))], 3),
                'rtt_max_ms': round(max(samples), 3),
                'rtt_samples': len(samples),
                'bulk_max_gap_ms': round(result['bulk_max_gap_ms'], 3),
                **{key: value for key, value in result.items() if key.startswith(('bulk_blocks', 'bulk_gaps_'))}})
            self.assertLess(max(samples), max_rtt, report)
        for guest, session in zip(guests, joined):
            await self.call(guest, 'room.close', {'session': session['session']})
        await self.call(host, 'room.close', {'session': hosted['session']})
        host.stdin.close()
        await asyncio.wait_for(host.wait(), 15)
        for player in identities:
            files = list((host.player_logs / player).glob('*.jsonl'))
            self.assertTrue(files, f'Host journal lost player {player}')
            events = [json.loads(line) for file in files for line in file.read_text(encoding='utf-8').splitlines()]
            self.assertTrue(any(row['event'] == 'stream_sample' for row in events), player)
            self.assertTrue(any(row['event'] == 'stream_closed' for row in events), player)
            self.assertNotIn(hosted['invite'], ''.join(file.read_text(encoding='utf-8') for file in files))
        await self.node.diagnostics.flush()
        for player in identities:
            node_files = list((self.node.diagnostics.root / player).glob('*.jsonl'))
            self.assertTrue(node_files, f'Node journal lost player {player}')
            events = [json.loads(line) for file in node_files for line in file.read_text(encoding='utf-8').splitlines()]
            self.assertTrue(any(row['event'] == 'stream_open' for row in events), player)
            self.assertTrue(any(row['event'] == 'stream_closed' for row in events), player)
        output = os.environ.get('BUGGCRAFT_BRIDGE_SOAK_RESULT')
        if output:
            Path(output).write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('NATIVE_CONTINUITY ' + json.dumps(report), flush=True)

if __name__=='__main__':unittest.main(verbosity=2)
