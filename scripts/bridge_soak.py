"""Run the three-player native bridge continuity test for a longer interval."""
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
    parser.add_argument('--bulk-rate', type=float, default=96,
                        help='64 KiB blocks/second per stream; 0 runs without a rate limit')
    parser.add_argument('--bulk-streams', type=int, default=1,
                        help='Concurrent bulk streams per player, from 1 to 5')
    args = parser.parse_args()
    if not 30 <= args.seconds <= 3600:
        parser.error('--seconds must be between 30 and 3600')
    if not 0 <= args.bulk_rate <= 10000 or not 1 <= args.bulk_streams <= 5:
        parser.error('--bulk-rate must be 0..10000 and --bulk-streams must be 1..5')
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ,
        BUGGCRAFT_BRIDGE_SOAK_SECONDS=str(args.seconds),
        BUGGCRAFT_BRIDGE_SOAK_RESULT=str(output),
        BUGGCRAFT_BRIDGE_SOAK_BULK_RATE=str(args.bulk_rate),
        BUGGCRAFT_BRIDGE_SOAK_BULK_STREAMS=str(args.bulk_streams),
        BUGGCRAFT_BRIDGE_SOAK_LOG_DIR=str(output.parent / (output.stem + '-logs')))
    import psutil
    started = time.time()
    binary_hashes = {}
    for variable in ('BUGGCRAFT_NATIVE_CORE', 'BUGGCRAFT_BRIDGE_EXECUTABLE'):
        binary = Path(environment.get(variable, ROOT / 'build' / (
            ('buggcraft-core' if variable.endswith('CORE') else 'buggcraft-bridge') +
            ('.exe' if os.name == 'nt' else ''))))
        binary_hashes[binary.name] = hashlib.sha256(binary.read_bytes()).hexdigest()
    state = dict(passed=False, state='running', started_unix=started,
                 duration_seconds=args.seconds, binary_sha256=binary_hashes)
    output.write_text(json.dumps(state, indent=2), encoding='utf-8')
    result = subprocess.Popen([
        sys.executable, '-m', 'unittest',
        'tests.test_bridge.BridgeTests.test_three_players_continuous_load_latency_and_log_isolation', '-v'
    ], cwd=ROOT, env=environment)
    parent = psutil.Process(result.pid)
    peak_rss = peak_native_rss = 0
    cpu_seconds = 0
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
                    cpu_seconds += max(0, total - last_cpu.get(process.pid, 0))
                    last_cpu[process.pid] = total
                    records.append(dict(pid=process.pid, name=process.name(),
                                        rss_bytes=process.memory_info().rss, cpu_seconds=round(total, 3)))
                except psutil.NoSuchProcess:
                    pass
            rss = sum(record['rss_bytes'] for record in records)
            native_rss = sum(record['rss_bytes'] for record in records if record['name'].startswith('buggcraft-'))
            peak_rss = max(peak_rss, rss)
            peak_native_rss = max(peak_native_rss, native_rss)
            samples.write(json.dumps(dict(at=time.time(), elapsed_seconds=round(time.time() - started, 3),
                total_rss_bytes=rss, native_rss_bytes=native_rss, processes=records)) + '\n')
            samples.flush()
            try:
                result.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    result.wait()
    report = json.loads(output.read_text(encoding='utf-8'))
    report.update(passed=result.returncode == 0, state='completed', returncode=result.returncode,
                  started_unix=started, completed_unix=time.time(), binary_sha256=binary_hashes,
                  peak_test_tree_rss_mib=round(peak_rss / 1024**2, 2),
                  peak_native_processes_rss_mib=round(peak_native_rss / 1024**2, 2),
                  sampled_test_tree_cpu_seconds=round(cpu_seconds, 3),
                  resource_samples=str(output.with_suffix('.resources.jsonl')))
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('NATIVE_SOAK_RESULT ' + json.dumps(report), flush=True)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
