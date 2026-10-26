"""Room-scoped WebRTC rendezvous. This channel never forwards game bytes."""
from collections import deque
import json
import time

SUBSYSTEM = 'buggcraft-peer-v1'
MAX_FRAME = 16384


class PeerSignalSession:
    def __init__(self, peer, channel):
        self.peer, self.channel = peer, channel
        self.buffer = bytearray()
        self.calls = deque()
        self.closed = False
        self.paused = False

    def send(self, value):
        if self.closed or self.paused:
            self.close()
            return
        self.channel.write(json.dumps(value, ensure_ascii=True).encode() + b'\n')

    def start(self):
        peer, room = self.peer, self.peer.relay.rooms.get(self.peer.room)
        if not room or getattr(peer, 'peer_signal', None):
            self.close()
            return
        peer.peer_signal = self
        self.send({'type': 'ready', 'peer': peer.chat_id,
                   'available': bool(getattr(room.owner, 'peer_signal', None))})

    def close(self):
        if self.closed:
            return
        self.closed = True
        if getattr(self.peer, 'peer_signal', None) is self:
            self.peer.peer_signal = None
            room = self.peer.relay.rooms.get(self.peer.room)
            owner = getattr(room.owner, 'peer_signal', None) if room else None
            if owner and owner is not self:
                owner.send({'type': 'left', 'peer': self.peer.chat_id})
        self.channel.close()

    def data_received(self, data, datatype):
        if datatype is not None or self.closed:
            self.close()
            return
        self.buffer.extend(data)
        if len(self.buffer) > MAX_FRAME:
            self.close()
            return
        while b'\n' in self.buffer:
            line, _, remaining = self.buffer.partition(b'\n')
            self.buffer = bytearray(remaining)
            try:
                now = time.monotonic()
                while self.calls and self.calls[0] < now - 10:
                    self.calls.popleft()
                # Hosts answer multiple visitors; visitors may send one offer
                # and bounded retries. No candidate broadcast or fan-out.
                limit = 128 if self.peer.role == 'host' else 8
                if len(self.calls) >= limit:
                    raise ValueError('Signaling rate limit')
                self.calls.append(now)
                message = json.loads(line)
                room = self.peer.relay.rooms.get(self.peer.room)
                # ICE may be negotiated before the host opens its LAN world.
                # The host's game listener checks availability per stream.
                if not room or room.owner.conn.is_closed():
                    raise ValueError('Room unavailable')
                if not isinstance(message, dict):
                    raise ValueError('Invalid message')
                kind, sdp = message.get('type'), message.get('sdp')
                attempt = message.get('attempt')
                files = message.get('files', False)
                if type(files) is not bool:
                    raise ValueError('Invalid file channel marker')
                if attempt is not None and (not isinstance(attempt, str) or
                        not 1 <= len(attempt) <= 64 or
                        any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for c in attempt)):
                    raise ValueError('Invalid negotiation attempt')
                if not isinstance(sdp, str) or not 1 <= len(sdp) <= 12288:
                    raise ValueError('Invalid SDP')
                if self.peer.role == 'client' and kind == 'offer':
                    target = getattr(room.owner, 'peer_signal', None)
                elif self.peer is room.owner and kind == 'answer':
                    clients = [p for p in self.peer.relay.peers
                               if p.authenticated and p.role == 'client' and
                               p.room == self.peer.room and getattr(p, 'peer_signal', None)]
                    target = next((getattr(p, 'peer_signal', None) for p in clients
                                   if p.chat_id == message.get('peer')), None)
                    if target is None:
                        # A visitor can time out and leave while the host is
                        # gathering ICE candidates. Discard this late answer
                        # without disabling signaling for the rest of the room.
                        continue
                else:
                    raise ValueError('Signaling role denied')
                if target:
                    if getattr(self.peer.relay, 'diagnostics', None):
                        self.peer.relay.diagnostics.emit(self.peer, 'peer_' + kind)
                    from .diagnostics import player_identity
                    target.send({'type': kind, 'peer': self.peer.chat_id, 'sdp': sdp, 'files': files,
                                 'player': player_identity(self.peer),
                                 **({'attempt': attempt} if attempt is not None else {})})
                else:
                    self.send({'type': 'unavailable'})
            except (ValueError, TypeError, UnicodeError):
                self.close()
                return
