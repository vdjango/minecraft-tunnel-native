"""One node command for installed and standalone packages; transport stays native."""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys


def main():
    root = Path(os.environ.get('BUGGCRAFT_NODE_ROOT', getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1])))
    installed = root == Path('/usr/lib/buggcraft-node')
    default_data = (Path('/var/lib/buggcraft-node') if installed else
                    Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'BuggcraftNode' if os.name == 'nt' else
                    Path.home() / '.local/share/buggcraft-node')
    sys.path[:0] = [str(root / 'python'), str(root / 'web')]
    os.environ['BUGGCRAFT_NATIVE_CORE'] = str(root / 'native/bin' / ('buggcraft-core.exe' if os.name == 'nt' else 'buggcraft-core'))
    parser = argparse.ArgumentParser(description='Buggcraft node: join, serve, status, logs', add_help=False)
    parser.add_argument('command', nargs='?', choices=('join', 'configure', 'serve', 'status', 'logs'), default='join')
    parser.add_argument('--directory', type=Path, default=Path(os.environ.get('BUGGCRAFT_NODE_DATA', default_data)))
    args, extra = parser.parse_known_args()
    if args.command in ('serve', 'configure'):
        script = root / ('web/main.py' if args.command == 'serve' else 'scripts/configure_node.py')
        sys.argv = [str(script), '--directory', str(args.directory), *extra]
        runpy.run_path(str(script), run_name='__main__')
        if args.command == 'configure':
            service_ownership(args.directory, installed)
        return
    if args.command in ('status', 'logs'):
        if extra:
            parser.error('Unexpected arguments')
        if installed:
            command = (['systemctl', 'status', 'buggcraft-node', '--no-pager'] if args.command == 'status'
                       else ['journalctl', '-u', 'buggcraft-node', '-n', '80', '--no-pager'])
            raise SystemExit(subprocess.call(command))
        print('Data: ' + str(args.directory))
        print('Log: ' + str(args.directory / 'logs/node.log'))
        return
    defaults = json.loads((root / 'packaging/node-defaults.json').read_text(encoding='utf-8'))
    join_parser = argparse.ArgumentParser(prog='buggcraft-node join', description='Join a control service without copying keys or certificates')
    join_parser.add_argument('--control', default=defaults['control_origin'], help='Custom HTTPS control address')
    join_parser.add_argument('--host', help='Public numeric IP of this node (IPv4 or IPv6)')
    join_parser.add_argument('--region', help='Region, e.g. cn-east, cn-north, cn-south')
    join_parser.add_argument('--bandwidth-mbps', type=float, help='Available upload bandwidth, in Mbps')
    join_parser.add_argument('--operator', default='community')
    join_parser.add_argument('--slots', type=int, default=32)
    join_parser.add_argument('--port', type=int, default=3333)
    join_parser.add_argument('--proof-port', type=int, default=18443)
    join_parser.add_argument('--ca-file', type=Path, help='Only needed for a private CA')
    join_parser.add_argument('--no-start', action='store_true', help='Configure without starting the service')
    settings = join_parser.parse_args(extra)
    if installed and args.directory != Path('/var/lib/buggcraft-node') and not settings.no_start:
        join_parser.error('Custom data directories require --no-start; run serve with the same --directory')
    if installed and os.geteuid() != 0:
        parser.exit(1, 'Run: sudo buggcraft-node join\n')
    if (args.directory / 'server.conf').exists():
        if len(sys.argv) == 1 and not installed:
            sys.argv = [str(root / 'web/main.py'), '--directory', str(args.directory)]
            runpy.run_path(sys.argv[0], run_name='__main__')
            return
        parser.exit(1, 'Already configured. Use buggcraft-node serve (standalone) or status (installed). Existing keys are preserved.\n')
    if not settings.host and not sys.stdin.isatty():
        join_parser.error('--host PUBLIC_IP is required without an interactive terminal')
    print('Control: ' + settings.control)
    try:
        host = settings.host or input('Public IP of this server: ').strip()
        interactive = sys.stdin.isatty() and not settings.host
        region = settings.region or (input('Region [cn-east]: ').strip() if interactive else '') or 'cn-east'
        bandwidth = settings.bandwidth_mbps
        if bandwidth is None:
            bandwidth = float(input('Available upload bandwidth in Mbps [6]: ').strip() or '6') if interactive else 6
        if not math.isfinite(bandwidth):
            raise ValueError('Invalid bandwidth')
        from tunnel.enrollment import join
        asyncio.run(join(args.directory, settings.control, host, port=settings.port, proof_port=settings.proof_port,
                         ca_file=settings.ca_file, region=region, operator=settings.operator,
                         slots=settings.slots, bandwidth=int(bandwidth * 1000000)))
        service_ownership(args.directory, installed)
    except (KeyboardInterrupt, EOFError):
        parser.exit(1, 'Setup cancelled.\n')
    except Exception:
        parser.exit(1, 'Join failed. Check the public IP, control address/HTTPS trust and enrollment availability. Existing configuration is never replaced.\n')
    print(f'Configured. Allow inbound TCP {settings.port} and {settings.proof_port} in the server firewall/cloud security group.')
    print(f'UDP {settings.port} is recommended for peer discovery. No domain, Nginx or manual proof certificate is required.')
    if settings.no_start:
        print('Start with: buggcraft-node serve' if not installed else 'Start with: sudo systemctl start buggcraft-node')
    elif installed:
        subprocess.run(['systemctl', 'enable', '--now', 'buggcraft-node'], check=True)
        print('Service started; check enrollment with buggcraft-node status or logs. Configuration alone does not prove public reachability.')
    else:
        sys.argv = [str(root / 'web/main.py'), '--directory', str(args.directory)]
        runpy.run_path(sys.argv[0], run_name='__main__')


def service_ownership(directory, installed):
    """Hand only the installed node data to its dedicated service account."""
    if installed and os.geteuid() == 0:
        shutil.chown(directory, 'buggcraft-node', 'buggcraft-node')
        for path in directory.iterdir():
            if path.is_file() and not path.is_symlink():
                shutil.chown(path, 'buggcraft-node', 'buggcraft-node')


if __name__ == '__main__':
    main()
