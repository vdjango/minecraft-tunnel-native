"""Test-only UDP fault injection; signalling and SSH remain untouched."""
import asyncio
import ipaddress
from unittest.mock import patch

from tunnel.peer_signal import PeerSignalSession


class PeerUdpProxy:
    def __init__(self):
        self.allowed = [True, True]
        self.addresses = [None, None]  # Actual guest and host ICE endpoints.
        self.transports = []
        self.forwarded = [0, 0]
        self.dropped = [0, 0]
        self.peer = None

    async def start(self):
        owner = self
        class Leg(asyncio.DatagramProtocol):
            def __init__(self, index):
                self.index = index

            def datagram_received(self, data, address):
                index = self.index
                # Each leg accepts only its advertised bridge endpoint.
                if address != owner.addresses[index]:
                    return
                if not owner.allowed[index]:
                    owner.dropped[index] += 1
                    return
                destination = owner.addresses[1-index]
                if destination:
                    owner.transports[1-index].sendto(data, destination)
                    owner.forwarded[index] += 1

        loop = asyncio.get_running_loop()
        for index in range(2):
            transport, _ = await loop.create_datagram_endpoint(
                lambda index=index: Leg(index), local_addr=('0.0.0.0', 0))
            self.transports.append(transport)
        original = PeerSignalSession.send
        def send(session, value):
            kind = value.get('type')
            if kind == 'offer' and owner.peer is None:
                owner.peer = value['peer']
            affected = (kind == 'offer' and value.get('peer') == owner.peer or
                        kind == 'answer' and session.peer.chat_id == owner.peer)
            if affected:
                index = 0 if kind == 'offer' else 1
                lines = value['sdp'].splitlines()
                candidate = next((line.split() for line in lines
                    if line.startswith('a=candidate:') and len(line.split()) >= 8
                    and ipaddress.ip_address(line.split()[4]).version == 4
                    and not ipaddress.ip_address(line.split()[4]).is_loopback), None)
                if candidate is None:
                    raise AssertionError('The UDP fault test requires an IPv4 host candidate')
                owner.addresses[index] = (candidate[4], int(candidate[5]))
                candidate[5] = str(owner.transports[1-index].get_extra_info('sockname')[1])
                lines = [line for line in lines if not line.startswith('a=candidate:')]
                lines.insert(next((i for i, line in enumerate(lines)
                                   if line == 'a=end-of-candidates'), len(lines)), ' '.join(candidate))
                value = dict(value, sdp='\r\n'.join(lines)+'\r\n')
            return original(session, value)
        self.patch = patch.object(PeerSignalSession, 'send', send)
        self.patch.start()
        return self

    def mute(self, direction=None):
        for index in range(2) if direction is None else [direction]:
            self.allowed[index] = False

    def resume(self):
        self.allowed[:] = [True, True]

    async def close(self):
        self.patch.stop()
        for transport in self.transports:
            transport.close()
        await asyncio.sleep(0)
