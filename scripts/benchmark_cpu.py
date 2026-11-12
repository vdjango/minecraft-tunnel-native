"""Repeat native CPU workloads with separately controlled byte/crypto dispatch.

This never changes the machine environment or an existing service. Capability
masks apply only to new benchmark children. Generic crypto is not a proof that
the compiler/libc uses no vector instructions anywhere.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import time


def child_environment(mode: str) -> dict[str, str]:
    env = os.environ.copy()
    for key in ('BUGGCRAFT_DISABLE_SIMD', 'OPENSSL_ia32cap', 'OPENSSL_armcap'):
        env.pop(key, None)
    if mode in ('scalar', 'generic'):
        env['BUGGCRAFT_DISABLE_SIMD'] = '1'
    if mode in ('crypto-generic', 'generic'):
        machine = platform.machine().lower()
        if machine in ('amd64', 'x86_64', 'x86', 'i386', 'i686'):
            env['OPENSSL_ia32cap'] = '0:0'
        elif machine in ('arm64', 'aarch64'):
            env['OPENSSL_armcap'] = '0'
        else:
            raise ValueError(f'No verified OpenSSL capability mask for {machine}')
    return env


def run(binary: Path, mode: str, round_number: int) -> dict:
    env = child_environment(mode)
    completed = subprocess.run([str(binary.resolve())], env=env, capture_output=True, text=True, check=True, timeout=120)
    rows = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    # Earlier baseline binaries mark each row; current binaries commit verification
    # only after all byte/digest/AEAD checks. Nonzero exits discard the entire run.
    if not rows or not any(row.get('verified') for row in rows):
        raise RuntimeError('Benchmark did not verify its output')
    return dict(mode=mode, round=round_number, rows=rows, diagnostics=completed.stderr,
                environment={key: env[key] for key in ('BUGGCRAFT_DISABLE_SIMD', 'OPENSSL_ia32cap', 'OPENSSL_armcap') if key in env})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--modes', nargs='+', choices=('auto', 'scalar', 'crypto-generic', 'generic'), default=['auto', 'scalar', 'crypto-generic', 'generic'])
    args = parser.parse_args()
    if args.rounds < 3:
        parser.error('Use at least three rounds')
    report = dict(platform=platform.platform(), machine=platform.machine(), processor=platform.processor(),
                  python=platform.python_version(), cpu_count=os.cpu_count(), started=time.time(),
                  binary=str(args.binary.resolve()), binary_sha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(),
                  trials=[], summary=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for round_number in range(args.rounds):
        # Reverse order on alternate rounds to reduce consistent warmup bias.
        modes = args.modes if round_number % 2 == 0 else list(reversed(args.modes))
        for mode in modes:
            report['trials'].append(run(args.binary, mode, round_number))
            args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for mode in args.modes:
        cases: dict[str, list[float]] = {}
        for trial in report['trials']:
            if trial['mode'] == mode:
                for row in trial['rows']:
                    if 'case' in row:
                        cases.setdefault(row['case'], []).append(row['mib_s'])
        for case, values in cases.items():
            report['summary'].append(dict(mode=mode, case=case, median_mib_s=statistics.median(values),
                                          min_mib_s=min(values), max_mib_s=max(values), samples=len(values)))
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__':
    main()
