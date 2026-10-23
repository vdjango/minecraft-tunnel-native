"""Production SSH connections using existing private workspace credentials.

No passwords or access tokens are accepted on the command line or written to
deployment artifacts. Host keys must already be pinned in the workspace file.
"""
from pathlib import Path
import sys

from remote_validate import connect as connect_first

ROOT = Path(__file__).resolve().parents[1]


def connect(node):
    if node == 'node1':
        return connect_first()
    if node != 'node2':
        raise ValueError('Expected node1 or node2')
    from domain_ops import connect as trusted_connect
    client = trusted_connect('43.241.50.19')
    client.get_transport().set_keepalive(20)
    return client


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('node', choices=['node1', 'node2'])
    parser.add_argument('--script', type=Path, required=True)
    args = parser.parse_args()
    import shlex
    script = args.script.read_text(encoding='utf-8-sig')
    with connect(args.node) as client:
        command = 'sudo -n python3 -c ' + shlex.quote(script)
        _, stdout, stderr = client.exec_command(command, timeout=55)
        print(stdout.read().decode(errors='replace'), end='')
        error = stderr.read().decode(errors='replace')
        if error:
            print(error, end='', file=sys.stderr)
        raise SystemExit(stdout.channel.recv_exit_status())
