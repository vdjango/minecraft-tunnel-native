"""Peer rendezvous authorization and RFC 8489 address discovery boundaries."""
import asyncio
import ipaddress
import json
import struct
import unittest
from collections import deque
from types import SimpleNamespace

from tunnel.peer_signal import PeerSignalSession
from tunnel.stun import BindingServer, COOKIE


class SignalChannel:
    def __init__(self):
        self.frames, self.closed = [], False
    def write(self, value):
        self.frames.append(json.loads(value))
    def close(self):
        self.closed = True


class PeerSignalTests(unittest.TestCase):
    def make_peer(self, role, room_id, relay):
        peer = SimpleNamespace(role=role, room=room_id, relay=relay,
            authenticated=True, chat_id=role + room_id, conn=SimpleNamespace(is_closed=lambda: False),
            peer_signal=None)
        return peer

    def session(self, peer):
        channel = SignalChannel()
        session = PeerSignalSession(peer, channel)
        session.start()
        return session, channel

    def test_offer_only_routes_to_owner_and_answer_to_authenticated_same_room_member(self):
        room = SimpleNamespace(available=True)
        other_room = SimpleNamespace(available=True)
        relay = SimpleNamespace(rooms={'a': room, 'b': other_room}, peers=[])
        owner = self.make_peer('host', 'a', relay)
        client = self.make_peer('client', 'a', relay)
        stranger = self.make_peer('client', 'b', relay)
        other_owner = self.make_peer('host', 'b', relay)
        other_room.owner = other_owner
        relay.peers = [owner, client, stranger, other_owner]
        room.owner = owner
        client_session, client_wire = self.session(client)
        host_session, host_wire = self.session(owner)
        stranger_session, stranger_wire = self.session(stranger)
        self.assertFalse(client_wire.frames[0]['available'])
        owner.peer_signal = host_session
        client_session.data_received((json.dumps({'type': 'offer', 'sdp': 'offer-sdp'}) + '\n').encode(), None)
        self.assertEqual(host_wire.frames[-1], {'type': 'offer', 'peer': client.chat_id,
            'sdp': 'offer-sdp', 'files': False, 'player': 'anonymous-' + client.chat_id})
        host_session.data_received((json.dumps({'type': 'answer', 'peer': client.chat_id,
                                                'sdp': 'answer-sdp'}) + '\n').encode(), None)
        self.assertEqual(client_wire.frames[-1]['sdp'], 'answer-sdp')
        host_session.data_received((json.dumps({'type': 'answer', 'peer': stranger.chat_id,
                                                'sdp': 'cross-room'}) + '\n').encode(), None)
        self.assertEqual(stranger_wire.frames, [{'type': 'ready', 'peer': stranger.chat_id,
                                                 'available': False}])
        self.assertFalse(host_session.closed)
        # A later legitimate visitor still receives its answer.
        host_session.data_received((json.dumps({'type':'answer', 'peer':client.chat_id,
                                                'sdp':'still-working'}) + '\n').encode(), None)
        self.assertEqual(client_wire.frames[-1]['sdp'], 'still-working')

    def test_malformed_and_oversized_frames_close_without_forwarding(self):
        room = SimpleNamespace(available=True)
        relay = SimpleNamespace(rooms={'a': room}, peers=[])
        owner = self.make_peer('host', 'a', relay)
        room.owner = owner
        session, channel = self.session(owner)
        session.data_received(b'{bad}\n', None)
        self.assertTrue(channel.closed)
        self.assertEqual(len(channel.frames), 1)
        session, channel = self.session(owner)
        session.data_received(b'x' * 16385, None)
        self.assertTrue(channel.closed)
        self.assertEqual(len(channel.frames), 1)

    def test_stun_binding_xor_mapped_ipv4_and_ipv6(self):
        class Datagram:
            def __init__(self): self.responses = []
            def sendto(self, data, address): self.responses.append((data, address))
        for source in [('198.51.100.8', 45678), ('2001:db8::8', 45678)]:
            server, transport = BindingServer(), Datagram()
            server.transport = transport
            transaction = bytes(range(12))
            request = struct.pack('!HHI', 1, 0, COOKIE) + transaction
            server.datagram_received(request, source)
            data, target = transport.responses[0]
            self.assertEqual(target, source)
            self.assertEqual(data[:2], b'\x01\x01')
            family = 1 if ':' not in source[0] else 2
            length = 8 if family == 1 else 20
            attribute = data[20:]
            self.assertEqual(struct.unpack('!HH', attribute[:4]), (0x20, length))
            self.assertEqual(attribute[5], family)
            cookie = struct.pack('!I', COOKIE)
            mask = cookie + transaction
            encoded = attribute[8:]
            decoded = bytes(a ^ b for a, b in zip(encoded, mask))
            self.assertEqual(ipaddress.ip_address(decoded).compressed, ipaddress.ip_address(source[0]).compressed)
            self.assertEqual(struct.unpack('!H', attribute[6:8])[0] ^ (COOKIE >> 16), source[1])

    def test_stun_ignores_bad_packets_and_bounds_source_rate(self):
        class Datagram:
            def __init__(self): self.responses = []
            def sendto(self, data, address): self.responses.append(data)
        server, transport = BindingServer(), Datagram()
        server.transport = transport
        source = ('198.51.100.1', 5000)
        request = struct.pack('!HHI', 1, 0, COOKIE) + b'x' * 12
        for packet in (request[:19], b'bad' + request[3:], struct.pack('!HHI', 1, 0, COOKIE + 1) + b'x' * 12):
            server.datagram_received(packet, source)
        self.assertEqual(transport.responses, [])
        for _ in range(21):
            server.datagram_received(request, source)
        self.assertEqual(len(transport.responses), 20)


if __name__ == '__main__':
    unittest.main(verbosity=2)
