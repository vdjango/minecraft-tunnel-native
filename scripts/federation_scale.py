"""Measure real native federation convergence, fixed fan-out and seed failure."""
import argparse
import asyncio
import json
from pathlib import Path
import secrets
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
import asyncssh
from tunnel.federation import Federation, TTL, MAX_NODES
from tunnel.native_relay import RelayServer
from tunnel.federation_schedule import FANOUT


async def measure(count, duration):
    relays, feds = [], []
    network = secrets.token_urlsafe(48)
    started = time.monotonic()
    samples = []
    try:
        for index in range(count):
            key = asyncssh.generate_private_key('ssh-ed25519')
            relay = await RelayServer('127.0.0.1', 0, host_key=key,
                token=secrets.token_urlsafe(48), workers=1).start()
            relays.append(relay)
            fed = Federation(key, dict(server_host='127.0.0.1', server_port=relay.port,
                token=relay.token, server_public_key=key.export_public_key().decode()),
                network_key=network, name=f'scale-{index}')
            fed.relay, relay.federation = relay, fed
            fed.publish()
            if index:
                fed.join(feds[0].invitation())
            feds.append(fed)
        for fed in feds:
            fed.task = asyncio.create_task(fed.run())
        async with asyncio.timeout(90):
            while any(len(fed.live()) != count for fed in feds):
                await asyncio.sleep(.2)
        convergence = time.monotonic() - started
        print('FEDERATION_CONVERGED ' + json.dumps(dict(nodes=count, seconds=round(convergence,3))), flush=True)
        failed_indices = list(range(min(4, count - 1)))
        for index in failed_indices:
            await feds[index].close()
            relays[index].process.kill()
        active = feds[len(failed_indices):]
        before = {fed.id: dict(fed.stats) for fed in active}
        failures_at = time.monotonic()
        previous_at = failures_at
        previous_starts = sum(fed.stats['exchanges_started'] for fed in active)
        while time.monotonic() - failures_at < duration:
            await asyncio.sleep(1)
            now = time.monotonic()
            starts = sum(fed.stats['exchanges_started'] for fed in active)
            sample = dict(elapsed=round(now-failures_at,3),
                started_per_second=round((starts-previous_starts)/(now-previous_at),3),
                max_inflight=max(len(f.inflight) for f in active),
                max_pooled=max(len(f.connections) for f in active),
                min_live=min(len(f.live()) for f in active),
                max_live=max(len(f.live()) for f in active))
            assert sample['max_inflight'] <= FANOUT, sample
            assert sample['max_pooled'] <= FANOUT, sample
            assert all(f.task and not f.task.done() for f in active), sample
            samples.append(sample)
            previous_at, previous_starts = now, starts
        expected = {f.id for f in active}
        for fed in active:
            assert set(fed.live()) == expected, dict(node=fed.name, live=len(fed.live()), expected=len(expected))
            assert len(fed.seeds) == count-1, 'Loss of seed removed independent membership'
        elapsed = time.monotonic()-failures_at
        total = {name: sum(f.stats[name]-before[f.id][name] for f in active)
                 for name in ('exchanges_started','exchanges_failed','snapshot_received_bytes')}
        return dict(passed=True, nodes=count, failed_nodes=len(failed_indices), survivors=len(active),
            convergence_seconds=round(convergence,3), observation_seconds=round(elapsed,3),
            lease_seconds=TTL, fanout_per_node=FANOUT,
            snapshot_payload_kib_s=round(total['snapshot_received_bytes']/elapsed/1024,3),
            exchanges_per_second=round(total['exchanges_started']/elapsed,3),
            totals=total, samples=samples,
            scope='Real native SSH nodes on one host; directory control-plane test, no game payload')
    finally:
        await asyncio.gather(*(fed.close() for fed in feds), return_exceptions=True)
        await asyncio.gather(*(relay.close() for relay in relays), return_exceptions=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nodes', type=int, default=32)
    parser.add_argument('--seconds', type=int, default=65)
    parser.add_argument('--output', type=Path, default=ROOT/'validation-artifacts/federation-scale.json')
    args = parser.parse_args()
    if not 3 <= args.nodes <= MAX_NODES or args.seconds < TTL+5:
        parser.error(f'nodes must be 3..{MAX_NODES}; seconds must be at least {TTL+5}')
    report = asyncio.run(measure(args.nodes,args.seconds))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('FEDERATION_SCALE ' + json.dumps({k:v for k,v in report.items() if k != 'samples'}),flush=True)


if __name__ == '__main__':
    main()
