"""Bounded, room-scoped chat on authenticated SSH connections."""
from collections import deque
import json
import secrets
import time

MAX_FRAME = 4096
MAX_MESSAGES = 200


def text_field(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Invalid chat text')
    if any(not ch.isprintable() or ch == '\u00a7' for ch in value):
        raise ValueError('Chat must be a single line of plain text')
    return value.strip()


class RoomChatSession:
    def __init__(self, peer, channel):
        self.peer, self.channel = peer, channel
        self.buffer = bytearray()
        self.room = None
        self.calls = deque()
        self.name = ''
        self.paused = False

    def start(self):
        room = self.peer.relay.rooms.get(self.peer.room)
        if self.peer.role not in ('host', 'client') or not room:
            self.channel.close()
            return
        if any(s.peer is self.peer for s in room.chat_sessions):
            self.channel.close()
            return
        self.room = room
        room.chat_sessions.add(self)
        self.send({'type': 'ready', 'room': self.peer.room, 'bridge': room.chat_bridge})
        self.send({'type': 'room', 'state': 'ready' if room.available and room.port else 'waiting',
                   'reason': '房主不在游戏内，请等待房主重新开放世界。' if not room.available or not room.port else ''})
        for event in room.chat_history:
            self.send({**event, 'replay': True})

    def send(self, event):
        if self.paused:
            # Slow readers reconnect and recover bounded history instead of
            # retaining unbounded write buffers on the relay.
            self.channel.close()
            return
        self.channel.write(json.dumps(event, ensure_ascii=True).encode() + b'\n')

    def broadcast(self, event):
        for session in tuple(self.room.chat_sessions):
            session.send(event)

    def data_received(self, data, datatype):
        if (datatype is not None or not self.room or
                self.peer.relay.rooms.get(self.peer.room) is not self.room):
            self.channel.close()
            return
        self.buffer.extend(data)
        while b'\n' in self.buffer:
            line, _, self.buffer = self.buffer.partition(b'\n')
            if len(line) > MAX_FRAME:
                self.channel.close()
                return
            request_id = None
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError('Chat message must be an object')
                request_id = message.get('request')
                if request_id is not None and (not isinstance(request_id, str) or
                        len(request_id) != 24 or any(c not in '0123456789abcdef' for c in request_id)):
                    request_id = None
                    raise ValueError('Invalid chat request ID')
                now = time.monotonic()
                while self.calls and self.calls[0] <= now - 10:
                    self.calls.popleft()
                if len(self.calls) >= (100 if self.peer.role == 'host' else 10):
                    raise ValueError('发送过快，请稍后重试。')
                self.calls.append(now)
                operation = message.get('op')
                if operation == 'hello':
                    self.name = text_field(message.get('name'), 32)
                    continue
                if operation == 'bridge' and self.peer.role == 'host':
                    self.room.chat_bridge = text_field(message.get('status'), 160)
                    self.broadcast({'type': 'bridge', 'status': self.room.chat_bridge})
                    continue
                if operation not in ('send', 'game') or (operation == 'game' and self.peer.role != 'host'):
                    raise ValueError('Operation not allowed')
                if not self.name:
                    raise ValueError('Chat identity required')
                name = text_field(message.get('name'), 32) if operation == 'game' else self.name
                text = text_field(message.get('text'), 256)
                if len(text.encode('utf-16-le')) > 512:
                    raise ValueError('Chat text exceeds 256 UTF-16 units')
                event = {'type': 'message', 'id': secrets.token_hex(12), 'time': int(time.time()),
                         'sender': name, 'text': text, 'source': 'game' if operation == 'game' else 'lobby',
                         'peer': self.peer.chat_id}
                # The authenticated SSH account, never a client-supplied nickname.
                event['account'] = getattr(self.peer, 'account', None) if operation == 'send' else None
                if request_id:
                    event['request'] = request_id
                self.room.chat_history.append({key: value for key, value in event.items() if key != 'request'})
                self.broadcast(event)
            except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                self.send({'type': 'error', 'error': str(exc)[:160], 'request': request_id})
        if len(self.buffer) > MAX_FRAME:
            self.channel.close()

    def close(self):
        if self.room:
            self.room.chat_sessions.discard(self)
        self.room = None
