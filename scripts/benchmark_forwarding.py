"""Repeat the existing native three-player forwarding workload, with paired ISA controls."""
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {'auto': ('auto', 'auto'), 'scalar': ('scalar', 'auto'),
            'crypto-fixed': ('crypto-generic', 'aes-gcm'),
            'crypto-auto': ('crypto-generic', 'auto'),
            'library': ('auto', 'library'), 'baseline': ('auto', 'library')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--bulk-streams', type=int, default=2)
    parser.add_argument('--bulk-rate', type=float, default=0)
    parser.add_argument('--compression', choices=('off', 'auto'), default='off')
    parser.add_argument('--profiles', nargs='+', choices=tuple(PROFILES),
                        default=['baseline', 'auto', 'scalar', 'crypto-fixed', 'crypto-auto'])
    parser.add_argument('--baseline-core', type=Path)
    parser.add_argument('--baseline-bridge', type=Path)
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error('At least three paired rounds are required')
    if 'baseline' in args.profiles and (not args.baseline_core or not args.baseline_bridge):
        parser.error('Baseline profile requires both preserved executables')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(platform=platform.platform(), machine=platform.machine(),
                  python=platform.python_version(), cpu_count=os.cpu_count(), trials=[], summary={})
    for round_number in range(args.rounds):
        profiles = args.profiles if round_number % 2 == 0 else list(reversed(args.profiles))
        for profile in profiles:
            mode, policy = PROFILES[profile]
            destination = output / f'{profile}-{round_number + 1}.json'
            env = os.environ.copy()
            if profile == 'baseline':
                env.update(BUGGCRAFT_NATIVE_CORE=str(args.baseline_core.resolve()),
                           BUGGCRAFT_BRIDGE_EXECUTABLE=str(args.baseline_bridge.resolve()))
            command = [sys.executable, str(ROOT / 'scripts/bridge_soak.py'),
                       '--seconds', str(args.seconds), '--bulk-rate', str(args.bulk_rate),
                       '--bulk-streams', str(args.bulk_streams), '--compression', args.compression,
                       '--native-mode', mode, '--cipher-policy', policy, '--output', str(destination)]
            with destination.with_suffix('.log').open('w', encoding='utf-8') as log:
                completed = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           timeout=args.seconds + 120)
            trial = json.loads(destination.read_text(encoding='utf-8'))
            if completed.returncode or not trial.get('passed'):
                raise RuntimeError(f'Forwarding verification failed: {destination}')
            mib = sum(player['mib'] for player in trial['players'])
            trial.update(profile=profile, round=round_number + 1,
                         application_echo_mib_s=mib / trial['elapsed_seconds'],
                         native_cpu_seconds_per_gib=trial['sampled_native_cpu_seconds'] / (mib / 1024),
                         worst_player_p99_ms=max(player['rtt_p99_ms'] for player in trial['players']))
            report['trials'].append(trial)
            for name in args.profiles:
                rows = [row for row in report['trials'] if row['profile'] == name]
                report['summary'][name] = {
                    key: dict(median=statistics.median(values), min=min(values), max=max(values))
                    for key in ('application_echo_mib_s', 'native_cpu_seconds_per_gib',
                                'worst_player_p99_ms', 'peak_native_processes_rss_mib')
                    if (values := [row[key] for row in rows])}
            (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            print(f'{profile} round {round_number + 1}: {trial["application_echo_mib_s"]:.2f} MiB/s; '
                  f'P99 {trial["worst_player_p99_ms"]:.3f} ms', flush=True)


if __name__ == '__main__':
    main()
