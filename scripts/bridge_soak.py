"""Measure native relay continuity or concurrent P2P file/game traffic."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--output', type=Path, default=Path('bridge-soak.json'))
    parser.add_argument('--scenario', choices=('relay', 'mixed'), default='relay')
    parser.add_argument('--file-mib', type=int, default=16,
                        help='MiB per file in the four-file mixed scenario, from 1 to 64')
    parser.add_argument('--bulk-rate', type=float, default=96,
                        help='64 KiB blocks/second per stream; 0 runs without a rate limit')
    parser.add_argument('--bulk-streams', type=int, default=1,
                        help='Concurrent bulk streams per player, from 1 to 5')
    parser.add_argument('--native-mode', choices=('auto', 'scalar', 'crypto-generic', 'generic'), default='auto',
                        help='ISA dispatch mode for native children only (relay scenario)')
    parser.add_argument('--cipher-policy', choices=('auto', 'library', 'aes-gcm', 'chacha'), default='auto')
    parser.add_argument('--compression', choices=('auto', 'off'), default='auto')
    parser.add_argument('--asyncio-debug', action='store_true',
                        help='Include asyncio debug instrumentation in the load generator (off for performance measurements)')
    args = parser.parse_args()
    if not 30 <= args.seconds <= 3600:
        parser.error('--seconds must be between 30 and 3600')
    if not 0 <= args.bulk_rate <= 10000 or not 1 <= args.bulk_streams <= 5:
        parser.error('--bulk-rate must be 0..10000 and --bulk-streams must be 1..5')
    if not 1 <= args.file_mib <= 64:
        parser.error('--file-mib must be between 1 and 64')
    if args.scenario == 'mixed' and (args.native_mode != 'auto' or args.compression != 'auto'):
        parser.error('Native masks and compression overrides require --scenario relay')
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ,
        BUGGCRAFT_BRIDGE_SOAK_SECONDS=str(args.seconds),
        BUGGCRAFT_BRIDGE_SOAK_RESULT=str(output),
        BUGGCRAFT_BRIDGE_SOAK_BULK_RATE=str(args.bulk_rate),
        BUGGCRAFT_BRIDGE_SOAK_BULK_STREAMS=str(args.bulk_streams),
        BUGGCRAFT_SOAK_NATIVE_MODE=args.native_mode,
        BUGGCRAFT_SSH_CIPHER_POLICY=args.cipher_policy,
        BUGGCRAFT_SOAK_COMPRESSION=args.compression,
        BUGGCRAFT_BRIDGE_SOAK_ASYNCIO_DEBUG='1' if args.asyncio_debug else '0',
        BUGGCRAFT_BRIDGE_SOAK_LOG_DIR=str(output.parent / (output.stem + '-logs')))
    if args.scenario == 'mixed':
        environment.update(BUGGCRAFT_MIXED_REPORT=str(output),
                           BUGGCRAFT_MIXED_FILE_MIB=str(args.file_mib))
    import psutil
    started = time.time()
    binary_hashes = {}
    native_names = set()
    for variable in ('BUGGCRAFT_NATIVE_CORE', 'BUGGCRAFT_BRIDGE_EXECUTABLE'):
        binary = Path(environment.get(variable, ROOT / 'build' / (
            ('buggcraft-core' if variable.endswith('CORE') else 'buggcraft-bridge') +
            ('.exe' if os.name == 'nt' else ''))))
        binary_hashes[binary.name] = hashlib.sha256(binary.read_bytes()).hexdigest()
        native_names.add(binary.name.casefold())
    state = dict(passed=False, state='running', started_unix=started,
                 duration_seconds=args.seconds if args.scenario == 'relay' else None,
                 scenario=args.scenario, binary_sha256=binary_hashes,
                 asyncio_debug=args.asyncio_debug)
    output.write_text(json.dumps(state, indent=2), encoding='utf-8')
    test = ('tests.test_bridge_services.BridgeServicesTests.test_mixed_file_transfer_latency'
            if args.scenario == 'mixed' else
            'tests.test_bridge.BridgeTests.test_three_players_continuous_load_latency_and_log_isolation')
    result = subprocess.Popen([
        sys.executable, '-m', 'unittest',
        test, '-v'
    ], cwd=ROOT, env=environment)
    parent = psutil.Process(result.pid)
    peak_rss = peak_native_rss = 0
    cpu_seconds = 0
    native_cpu_seconds = 0
    last_cpu = {}
    with output.with_suffix('.resources.jsonl').open('w', encoding='utf-8') as samples:
        while result.poll() is None:
            records = []
            try:
                processes = [parent] + parent.children(recursive=True)
            except psutil.NoSuchProcess:
                break
            for process in processes:
                try:
                    times = process.cpu_times()
                    total = times.user + times.system
                    delta = max(0, total - last_cpu.get(process.pid, 0))
                    cpu_seconds += delta
                    if process.name().casefold() in native_names:
                        native_cpu_seconds += delta
                    last_cpu[process.pid] = total
                    records.append(dict(pid=process.pid, name=process.name(),
                                        rss_bytes=process.memory_info().rss, cpu_seconds=round(total, 3)))
                except psutil.NoSuchProcess:
                    pass
            rss = sum(record['rss_bytes'] for record in records)
            native_rss = sum(record['rss_bytes'] for record in records if record['name'].casefold() in native_names)
            peak_rss = max(peak_rss, rss)
            peak_native_rss = max(peak_native_rss, native_rss)
            samples.write(json.dumps(dict(at=time.time(), elapsed_seconds=round(time.time() - started, 3),
                total_rss_bytes=rss, native_rss_bytes=native_rss, processes=records)) + '\n')
            samples.flush()
            try:
                result.wait(timeout=.1 if args.scenario == 'mixed' else 5)
            except subprocess.TimeoutExpired:
                pass
    result.wait()
    report = json.loads(output.read_text(encoding='utf-8'))
    report.update(passed=result.returncode == 0, state='completed', returncode=result.returncode,
                  scenario=args.scenario,
                  requested_asyncio_debug=args.asyncio_debug,
                  native_mode=args.native_mode, cipher_policy=args.cipher_policy, compression=args.compression,
                  started_unix=started, completed_unix=time.time(), binary_sha256=binary_hashes,
                  peak_test_tree_rss_mib=round(peak_rss / 1024**2, 2),
                  peak_native_processes_rss_mib=round(peak_native_rss / 1024**2, 2),
                  sampled_test_tree_cpu_seconds=round(cpu_seconds, 3),
                  sampled_native_cpu_seconds=round(native_cpu_seconds, 3),
                  resource_samples=str(output.with_suffix('.resources.jsonl')))
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('NATIVE_SOAK_RESULT ' + json.dumps(report), flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
