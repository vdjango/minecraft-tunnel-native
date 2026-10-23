"""Minimal RFC 8489 Binding service: address discovery, never a UDP relay."""
import asyncio
from collections import OrderedDict
import ipaddress
import struct
import time

COOKIE = 0x2112A442


class BindingServer(asyncio.DatagramProtocol):
    def __init__(self):
        self.transport = None
        self.buckets = OrderedDict()
        self.global_bucket = (0, 0)
        self.closed = None

    def connection_made(self, transport):
        self.transport = transport
        self.closed = asyncio.get_running_loop().create_future()

    def connection_lost(self, error):
        if self.closed is not None and not self.closed.done():
            self.closed.set_result(None)

    def datagram_received(self, data, address):
        # A padded request (aiortc adds SOFTWARE/FINGERPRINT) receives a
        # smaller response. Tiny 20-byte probes are accepted for RFC clients.
        if not 20 <= len(data) <= 512:
            return
        kind, size, cookie = struct.unpack('!HHI', data[:8])
        if kind != 1 or cookie != COOKIE or size != len(data) - 20 or size % 4:
            return
        ip = ipaddress.ip_address(address[0])
        if ip.is_unspecified or ip.is_multicast:
            return
        now = int(time.monotonic())
        second, total = self.global_bucket
        total = total + 1 if second == now else 1
        self.global_bucket = (now, total)
        key = str(ipaddress.ip_network(str(ip) + ('/24' if ip.version == 4 else '/64'), strict=False))
        second, count = self.buckets.pop(key, (now, 0))
        count = count + 1 if second == now else 1
        self.buckets[key] = (now, count)
        while len(self.buckets) > 4096:
            self.buckets.popitem(last=False)
        if total > 1000 or count > 20:
            return
        transaction = data[8:20]
        mask = struct.pack('!I', COOKIE) + transaction
        encoded = bytes(a ^ b for a, b in zip(ip.packed, mask))
        value = struct.pack('!BBH', 0, 1 if ip.version == 4 else 2, address[1] ^ (COOKIE >> 16)) + encoded
        attribute = struct.pack('!HH', 0x20, len(value)) + value
        self.transport.sendto(struct.pack('!HHI', 0x101, len(attribute), COOKIE) + transaction + attribute, address)


async def start(host, port):
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(BindingServer,
                                                       local_addr=(host, port))
    return transport
