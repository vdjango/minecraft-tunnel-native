"""Local signed directory reads and bounded, non-recursive cache repair."""
import asyncio
import logging
import time

import asyncssh

from .account_replication import validate_header
from .friend_codes import matches_account

LOG = logging.getLogger(__name__)
PROBE_FANOUT = 2
PROBE_TIMEOUT = 4
NEGATIVE_TTL = 2
MAX_NEGATIVE = 256


class AccountLookup:
    def __init__(self, federation):
        self.fed = federation
        self.pending = {}
        self.negative = {}
        self.cursor = 0
        self.next_probe = 0
        self.stats = dict(local_hits=0, probes=0, coalesced=0, throttled=0)

    def local(self, identifier):
        fed = self.fed
        entries = dict(fed.accounts.replication.directory)
        entries.update({a: r for a, r in fed.accounts.records.items()
                        if r['certificate']['home'] == fed.id})
        return [{'account': account, 'username': entry['certificate']['username']}
                for account, entry in sorted(entries.items())
                if entry['certificate']['home'] not in fed.blocked
                and not fed.relay.account_blocked(account)
                and matches_account(identifier, account, entry['certificate']['username'])][:8]

    async def lookup(self, identifier):
        # Ingest already verified both signatures. A cache hit never needs a
        # network request, even when a seed or another member is unreachable.
        matches = self.local(identifier)
        if matches:
            self.stats['local_hits'] += 1
            return matches
        key = identifier.casefold()
        if key in self.pending:
            self.stats['coalesced'] += 1
            return await asyncio.shield(self.pending[key])
        now = time.monotonic()
        if self.negative.get(key, 0) > now:
            return []
        # One cache-repair batch per node, at most two peers and at most one
        # batch per second. This leaves transport capacity for heartbeats.
        if self.pending or now < self.next_probe:
            self.stats['throttled'] += 1
            return []
        self.next_probe = now + 1
        task = asyncio.create_task(self._probe(identifier))
        self.pending[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self.pending.get(key) is task:
                self.pending.pop(key, None)

    async def _probe(self, identifier):
        key = identifier.casefold()
        fed = self.fed
        try:
            peers = {n: r['body']['profile'] for n, r in fed.live().items()
                     if n != fed.id and n not in fed.blocked}
            peers.update({n: p for n, p in fed.seeds.items()
                          if n != fed.id and n not in fed.blocked})
            nodes = sorted(peers)
            if nodes:
                offset = self.cursor % len(nodes)
                nodes = nodes[offset:] + nodes[:offset]
                self.cursor += PROBE_FANOUT
            # A full account ID contains its home node. Contact it directly;
            # usernames use a couple of mirrors of the replicated directory.
            home = identifier.partition('.')[2]
            if home in nodes:
                nodes.remove(home)
                nodes.insert(0, home)
            selected = nodes[:PROBE_FANOUT]

            async def query(node):
                self.stats['probes'] += 1
                async with asyncio.timeout(PROBE_TIMEOUT):
                    return await fed.account_rpc(peers[node], 'account_lookup', identifier=identifier)

            results = await asyncio.gather(*(query(n) for n in selected), return_exceptions=True)
            learned = False
            for result in results:
                if not isinstance(result, dict) or not isinstance(result.get('entries'), list) or len(result['entries']) > 8:
                    continue
                for entry in result['entries']:
                    try:
                        validate_header(entry)
                        account = entry['routing']['account']
                        cert = entry['certificate']
                        if (cert['home'] in fed.blocked or cert['home'] not in fed.records or
                                fed.relay.account_blocked(account) or
                                not matches_account(identifier, account, cert['username'])):
                            continue
                        learned = fed.accounts.replication.accept(entry) or learned
                    except (ValueError, TypeError, KeyError, asyncssh.Error):
                        continue
            if learned:
                try:
                    fed.accounts.replication.save()
                except OSError:
                    LOG.exception('Failed to persist repaired account directory')
            matches = self.local(identifier)
            if not matches:
                self.negative = {k: until for k, until in self.negative.items() if until > time.monotonic()}
                if len(self.negative) >= MAX_NEGATIVE:
                    self.negative.pop(next(iter(self.negative)))
                self.negative[key] = time.monotonic() + NEGATIVE_TTL
            LOG.debug('account_lookup_repair peers=%d matches=%d', len(selected), len(matches))
            return matches
        finally:
            if self.pending.get(key) is asyncio.current_task():
                self.pending.pop(key, None)

    def reset(self):
        for task in self.pending.values():
            task.cancel()
        self.pending.clear()
        self.negative.clear()
        self.next_probe = 0
        self.cursor = 0

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.negative.clear()
