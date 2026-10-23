"""Measure byte-verified forwarding, RTT and server process CPU/RSS separately."""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import secrets
import statistics
import sys
import time

import asyncssh
import psutil

ROOT = Path(__file__).resolve().parents[1]

def sample(pid):
    processes = [psutil.Process(pid)]
    processes.extend(processes[0].children(recursive=True))
    cpu = sum(p.cpu_times().user + p.cpu_times().system for p in processes)
    memory = sum(p.memory_info().rss for p in processes)
    return cpu, memory

async def trial(engine, flows, size, rooms, workers, buffer_kib, room_secrets):
    node = await asyncio.create_subprocess_exec(sys.executable, str(ROOT / 'scripts/benchmark_server.py'), engine,
        '--workers', str(workers), '--buffer-kib', str(buffer_kib), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    connections = []
    server = None
    try:
        profile = json.loads(await asyncio.wait_for(node.stdout.readline(), 20))
        async def connect(role, secret):
            result = await asyncssh.connect('127.0.0.1', profile['port'], username=role + ':' + hashlib.sha256(secret.encode()).hexdigest()[:32],
                password=json.dumps(dict(token=profile['token'], secret=secret)),
                known_hosts=([asyncssh.import_public_key(profile['key'])], [], []), client_keys=[], config=None,
                agent_path=None, encoding=None, window=262144,
                encryption_algs=['aes128-gcm@openssh.com'], compression_algs=['none'])
            connections.append(result)
            return result
        async def echo(reader, writer):
            try:
                while chunk := await reader.read(65536):
                    writer.write(chunk)
                    await writer.drain()
                writer.write_eof()
            except (OSError, asyncio.CancelledError):
                pass
            finally:
                writer.close()
        server = await asyncio.start_server(echo, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        hosts, clients, keys = [], [], []
        for secret in room_secrets:
            host = await connect('host', secret)
            await host.forward_remote_port('127.0.0.1', 0, '127.0.0.1', port)
            hosts.append(host)
            clients.append(await connect('client', secret))
            keys.append(hashlib.sha256(secret.encode()).hexdigest()[:32])
        # Fixed random payloads ensure this measures forwarding rather than
        # compressibility; repeated trials use the same shape on both cores.
        chunk = os.urandom(65536)
        async def transfer(index):
            reader, writer = await clients[index % rooms].open_connection(keys[index % rooms], 25565, encoding=None)
            expected = hashlib.sha256()
            actual = hashlib.sha256()
            async def send():
                remaining = size
                while remaining:
                    data = chunk[:min(remaining, len(chunk))]
                    expected.update(data)
                    writer.write(data)
                    await writer.drain()
                    remaining -= len(data)
                writer.write_eof()
            sender = asyncio.create_task(send())
            received = 0
            while data := await reader.read(65536):
                actual.update(data)
                received += len(data)
            await sender
            writer.close()
            await writer.wait_closed()
            if received != size or actual.digest() != expected.digest():
                raise AssertionError('Forwarded payload mismatch')
        cpu_at, _ = sample(node.pid)
        started = time.perf_counter()
        peak_rss = 0
        stop = asyncio.Event()
        async def monitor():
            nonlocal peak_rss
            while not stop.is_set():
                peak_rss = max(peak_rss, sample(node.pid)[1])
                await asyncio.sleep(.05)
        watcher = asyncio.create_task(monitor())
        try:
            async with asyncio.timeout(120):
                await asyncio.gather(*(transfer(i) for i in range(flows)))
        finally:
            stop.set()
            await watcher
        elapsed = time.perf_counter() - started
        cpu = sample(node.pid)[0] - cpu_at
        reader, writer = await clients[0].open_connection(keys[0], 25565, encoding=None)
        rtts = []
        for _ in range(50):
            ping = time.perf_counter_ns()
            writer.write(b'latency-probe')
            await writer.drain()
            if await reader.readexactly(13) != b'latency-probe':
                raise AssertionError('RTT payload mismatch')
            rtts.append((time.perf_counter_ns() - ping) / 1e6)
        writer.close()
        await writer.wait_closed()
        return dict(engine=engine, rooms=rooms, workers=workers, buffer_kib=buffer_kib, flows=flows, bytes_per_flow=size,
            cipher='aes128-gcm@openssh.com', ssh_compression='none',
            wall_seconds=round(elapsed, 4), server_cpu_seconds=round(cpu, 4),
            server_cpu_percent=round(cpu/elapsed*100, 2), peak_server_rss_mib=round(peak_rss/1048576, 2),
            payload_egress_mib_s=round(2*flows*size/elapsed/1048576, 2),
            rtt_median_ms=round(statistics.median(rtts), 3),
            rtt_p95_ms=round(sorted(rtts)[47], 3), verified=True)
    finally:
        for connection in connections:
            connection.close()
        await asyncio.gather(*(c.wait_closed() for c in connections))
        if server:
            server.close()
            await server.wait_closed()
        if node.returncode is None:
            node.stdin.write(b'stop\n')
            await node.stdin.drain()
            await asyncio.wait_for(node.wait(), 12)

async def main(args):
    report = dict(platform=sys.platform, python=sys.version.split()[0],
                  cpu_count=os.cpu_count(), started=time.time(), trials=[])
    report['dependencies'] = {name: importlib.metadata.version(name) for name in ('asyncssh', 'cryptography', 'psutil')}
    report['core_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / 'core').glob('*')) if p.is_file()}
    room_secrets = [secrets.token_urlsafe(32) for _ in range(args.rooms)]
    for flows in args.flows:
        for engine in args.engines:
            result = await trial(engine, flows, args.mib*1048576, args.rooms, args.workers, args.buffer_kib, room_secrets)
            report['trials'].append(result)
            print(json.dumps(result), flush=True)
            Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--flows', type=int, nargs='+', default=[1, 8, 32])
    parser.add_argument('--engines', nargs='+', default=['python', 'native'])
    parser.add_argument('--mib', type=int, default=8)
    parser.add_argument('--rooms', type=int, default=4)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--buffer-kib', type=int, default=64)
    parser.add_argument('--output', default='benchmark.json')
    asyncio.run(main(parser.parse_args()))
