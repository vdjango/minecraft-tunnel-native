"""Private child process for comparing identical Python and native nodes."""
import argparse
import asyncio
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
import asyncssh

async def main(engine, workers, buffer_kib, diagnostics_dir=None, bind='127.0.0.1'):
    if engine == 'native':
        from tunnel.native_relay import RelayServer
        options = dict(workers=workers, buffer_kib=buffer_kib)
    else:
        from tunnel.relay import RelayServer
        options = {}
    key = asyncssh.generate_private_key('ssh-ed25519')
    relay = await RelayServer(bind, 0, host_key=key, token=secrets.token_urlsafe(48),
        max_connections=2048, max_total_streams=2048, max_streams=512, max_client_streams=128, **options).start()
    journal_context = None
    if diagnostics_dir:
        from tunnel.diagnostics import Diagnostics
        relay.diagnostics = Diagnostics(diagnostics_dir, relay)
        journal_context = relay.diagnostics.lifecycle(None)
        await anext(journal_context)
    print(json.dumps(dict(port=relay.port, token=relay.token, key=key.export_public_key().decode())), flush=True)
    try:
        while (await asyncio.to_thread(sys.stdin.buffer.readline)).strip() == b'stats':
            print(json.dumps(dict(bytes=relay.capacity.bytes)), flush=True)
    finally:
        await relay.close()
        if journal_context:
            await journal_context.aclose()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('engine', choices=['python', 'native'])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--buffer-kib', type=int, default=512)
    parser.add_argument('--diagnostics-dir')
    parser.add_argument('--bind', choices=['127.0.0.1', '0.0.0.0'], default='127.0.0.1')
    args = parser.parse_args()
    asyncio.run(main(args.engine, args.workers, args.buffer_kib, args.diagnostics_dir, args.bind))
