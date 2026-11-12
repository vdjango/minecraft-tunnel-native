"""Create one headless managed node; custom control services use the same inputs."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from tunnel.provisioning import initialize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--host', required=True, help='Public relay/proof hostname or IP')
    parser.add_argument('--port', type=int, default=3333)
    parser.add_argument('--proof-port', type=int, default=18443)
    parser.add_argument('--control-origin', required=True)
    parser.add_argument('--control-ca', help='Private control-service CA; public CA certificates use the system store')
    parser.add_argument('--control-key', required=True, help='Control Ed25519 public key in hex')
    parser.add_argument('--credential-file', required=True)
    parser.add_argument('--region', default='unknown')
    parser.add_argument('--operator', default='unknown')
    parser.add_argument('--slots', default='32')
    parser.add_argument('--bandwidth', default='6000000', help='bit/s')
    args = parser.parse_args()
    host = '[' + args.host + ']' if ':' in args.host else args.host
    initialize(args.directory, args.host, args.port, web_port=args.proof_port, control={
        'origin': args.control_origin, 'ca_file': str(Path(args.control_ca).resolve()) if args.control_ca else '',
        'signing_public_key': args.control_key,
        'credential_file': str(Path(args.credential_file).resolve()),
        'endpoint': f'{host}:{args.port}', 'proof_endpoint': f'{host}:{args.proof_port}',
        'region': args.region, 'operator': args.operator, 'slots': args.slots, 'bandwidth': args.bandwidth})
    print('Managed node configured. Start web/main.py with --directory; no web setup or administrator login is created.')


if __name__ == '__main__':
    main()
