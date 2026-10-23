"""Run and retrieve an isolated Ubuntu bridge soak using workspace SSH trust."""
import argparse
import json
from pathlib import Path
import shlex
import sys
import time

from remote_validate import connect

ROOT = Path(__file__).resolve().parents[1]
REMOTE = '/tmp/buggcraft-native-validation'


def fetch(sftp, remote, local):
    import stat
    local.mkdir(parents=True, exist_ok=True)
    for entry in sftp.listdir_attr(remote):
        if stat.S_ISDIR(entry.st_mode):
            fetch(sftp, remote + '/' + entry.filename, local / entry.filename)
        elif stat.S_ISREG(entry.st_mode):
            sftp.get(remote + '/' + entry.filename, str(local / entry.filename))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=1800)
    parser.add_argument('--bulk-streams', type=int, default=3)
    parser.add_argument('--bulk-rate', type=float, default=0)
    args = parser.parse_args()
    if not 30 <= args.seconds <= 3600 or not 1 <= args.bulk_streams <= 5 or not 0 <= args.bulk_rate <= 10000:
        parser.error('seconds=30..3600, bulk-streams=1..5, bulk-rate=0..10000')
    name = time.strftime('bridge-soak-%Y%m%dT%H%M%S')
    artifact = ROOT / 'validation-artifacts' / name
    artifact.mkdir(parents=True)
    remote_artifact = REMOTE + '/soak/' + name
    print('SOAK_ARTIFACTS ' + str(artifact), flush=True)
    with connect() as client:
        client.get_transport().set_keepalive(20)
        with client.open_sftp() as sftp:
            for source in ('scripts/bridge_soak.py', 'tests/test_bridge.py'):
                sftp.put(str(ROOT / source), REMOTE + '/' + source)
        command = (
            'cd ' + shlex.quote(REMOTE) + ' && '
            'BUGGCRAFT_NATIVE_CORE=' + REMOTE + '/build/buggcraft-core '
            'BUGGCRAFT_BRIDGE_EXECUTABLE=' + REMOTE + '/build/buggcraft-bridge '
            '.venv/bin/python -u scripts/bridge_soak.py '
            f'--seconds {args.seconds} --bulk-streams {args.bulk_streams} --bulk-rate {args.bulk_rate} '
            '--output ' + shlex.quote(remote_artifact + '/report.json') + ' 2>&1')
        channel = client.get_transport().open_session(timeout=20)
        channel.exec_command(command)
        deadline = time.monotonic() + args.seconds + 180
        with (artifact / 'console.log').open('wb') as log:
            while True:
                if channel.recv_ready():
                    data = channel.recv(65536)
                    log.write(data)
                    log.flush()
                    sys.stdout.write(data.decode('utf-8', errors='replace'))
                    sys.stdout.flush()
                elif channel.exit_status_ready():
                    break
                elif time.monotonic() > deadline:
                    channel.close()
                    raise TimeoutError('Remote soak exceeded its duration and cleanup budget')
                else:
                    time.sleep(.2)
        status = channel.recv_exit_status()
        with client.open_sftp() as sftp:
            fetch(sftp, remote_artifact, artifact)
    report = json.loads((artifact / 'report.json').read_text(encoding='utf-8'))
    print('SOAK_RETRIEVED ' + json.dumps(dict(path=str(artifact), exit_code=status,
                                             passed=report.get('passed'))), flush=True)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
