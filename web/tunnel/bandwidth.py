"""Cached host interface rates; independent of relay payload accounting."""
import asyncio
from contextlib import suppress
import ipaddress
import socket
import time

import psutil


def interface_counters():
    """Read OS counters without exposing interface names or addresses publicly."""
    addresses = psutil.net_if_addrs()
    result = {}
    for name, counter in psutil.net_io_counters(pernic=True, nowrap=False).items():
        ips = [ipaddress.ip_address(a.address.split('%')[0])
               for a in addresses.get(name, ()) if a.family in (socket.AF_INET, socket.AF_INET6)]
        if name.lower() in ('lo', 'lo0') or (ips and all(ip.is_loopback for ip in ips)):
            continue
        result[name] = (counter.bytes_recv, counter.bytes_sent)
    return result


class Bandwidth:
    interval = 1
    stale_after = 5

    def __init__(self, reader=interface_counters, clock=time.monotonic):
        self.reader, self.clock = reader, clock
        self.previous = None
        self.updated = None
        self.rates = None
        self.period = None
        self.state = 'warming_up'
        self.task = None

    async def sample(self):
        try:
            counters = await asyncio.to_thread(self.reader)
        except (OSError, RuntimeError, psutil.Error):
            self.previous = self.rates = self.updated = self.period = None
            self.state = 'unavailable'
            return
        now = self.clock()
        self.rates = self.period = None
        self.state = 'warming_up' if counters else 'unavailable'
        if counters and self.previous:
            then, before = self.previous
            elapsed = now - then
            # A new/reset interface starts a fresh baseline, rather than reporting
            # its lifetime byte count as a one-second burst.
            shared = [name for name in counters.keys() & before.keys()
                      if all(a >= b for a, b in zip(counters[name], before[name]))]
            if elapsed > 0 and shared:
                self.rates = tuple(sum(counters[n][i] - before[n][i] for n in shared) / elapsed
                                   for i in (0, 1))
                self.period = elapsed
                self.state = 'ready'
        self.previous = (now, counters) if counters else None
        self.updated = now

    def snapshot(self):
        age = None if self.updated is None else max(0, self.clock() - self.updated)
        state = 'stale' if age is not None and age > self.stale_after else self.state
        ready = state == 'ready'
        return dict(status=state, scope='host_non_loopback_interfaces',
                    rx_bytes_per_second=self.rates[0] if ready else None,
                    tx_bytes_per_second=self.rates[1] if ready else None,
                    sample_seconds=self.period if ready else None,
                    age_seconds=round(age, 2) if age is not None else None)

    async def _run(self):
        while True:
            await asyncio.sleep(self.interval)
            await self.sample()

    async def lifecycle(self, app):
        await self.sample()
        self.task = asyncio.create_task(self._run(), name='host-bandwidth')
        try:
            yield
        finally:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
