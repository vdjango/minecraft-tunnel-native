"""Room-scoped Minecraft forwarding over the maintained AsyncSSH transport.

Only authenticated, room-scoped forwarding is available. SSH shell, process,
SFTP, arbitrary destinations and public reverse listeners are disabled.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from collections import deque

import asyncssh
from asyncssh.forward import SSHForwarder
from .directory import RoomControlSession

LOG = logging.getLogger(__name__)


def minecraft_handshake_state(data):
    """Return True for a valid handshake, False for hostile input, None if partial."""
    view = memoryview(data)
    offset = 0
    packet_length = 0
    for index in range(5):
        if offset >= len(view):
            return None
        byte = view[offset]
        offset += 1
        packet_length |= (byte & 0x7f) << (7 * index)
        if not byte & 0x80:
            break
    else:
        return False
    if packet_length < 6 or packet_length > 8192:
        return False
    if len(view) < offset + packet_length:
        return None
    packet = view[offset:offset + packet_length]
    values = []
    position = 0
    for _ in range(3):
        value = 0
        for index in range(5):
            if position >= len(packet):
                return False
            byte = packet[position]
            position += 1
            value |= (byte & 0x7f) << (7 * index)
            if not byte & 0x80:
                break
        else:
            return False
        values.append(value)
    if values[0] != 0:
        return False
    name_length = values[2]
    if name_length > 255 or position + name_length + 3 > len(packet):
        return False
    position += name_length + 2
    if position >= len(packet) or packet[position] not in (1, 2):
        return False
    return position + 1 == len(packet)


def room_id(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()[:32]


@dataclass
class Room:
    owner: "RoomPeer"
    secret: str = field(default='', repr=False)
    port: int = 0
    pack_port: int = 0
    streams: set = field(default_factory=set)
    title: str = '好友的世界'
    public: bool = False
    require_invite: bool = False
    capacity: int = 100
    game: dict = field(default_factory=dict)
    chat_sessions: set = field(default_factory=set)
    chat_history: deque = field(default_factory=lambda: deque(maxlen=200))
    chat_bridge: str = '等待主机连接游戏聊天'
    available: bool = True
    expiry: object = None
    game_players: object = None
    game_max_players: object = None
    game_reported: float = 0
    lobby: object = None


class CountedForwarder(SSHForwarder):
    """Use AsyncSSH flow control and EOF handling; add a resource counter."""

    def __init__(self, peer, room, client, kind='game'):
        super().__init__(peer)
        self.room = room
        self.client = client
        self.trace = secrets.token_hex(6)
        self.closed = False
        self.upload_bytes = self.download_bytes = 0
        self.blocked = {}
        self.started = time.monotonic()
        self.handshake = bytearray()
        self.handshake_started = time.monotonic()
        self.handshake_validated = kind != 'game'
        room.streams.add(self)
        client.streams.add(self)
        client.relay.streams.add(self)
        self.event('stream_open', kind=kind)

    def event(self, event, **fields):
        if self.client.relay.diagnostics:
            self.client.relay.diagnostics.emit(self.client, event, stream=self.trace, **fields)

    def close(self):
        if not self.closed:
            self.closed = True
            for direction in tuple(self.blocked):
                self.unblock(direction)
            self.event('stream_closed', duration_ms=round((time.monotonic()-self.started)*1000, 2),
                       upload_bytes=self.upload_bytes, download_bytes=self.download_bytes)
        self.room.streams.discard(self)
        self.client.streams.discard(self)
        self.client.relay.streams.discard(self)
        super().close()

    def data_received(self, data, datatype=None):
        if not self.handshake_validated:
            self.handshake.extend(data)
            state = minecraft_handshake_state(self.handshake)
            if state is False or len(self.handshake) > 8192 or time.monotonic() - self.handshake_started > 10:
                self.event('stream_rejected', reason='minecraft-handshake')
                self.close()
                return
            if state is None:
                return
            self.handshake_validated = True
            data = bytes(self.handshake)
            self.handshake.clear()
        self.client.relay.capacity.record(len(data))
        self.upload_bytes += len(data)
        if self.client.relay.diagnostics:
            self.client.relay.diagnostics.traffic(self.client, 'upload', len(data))
            self.client.relay.diagnostics.traffic(self.room.owner, 'download', len(data))
        super().data_received(data, datatype)

    def write(self, data):
        # The peer writes host-to-visitor traffic here. Both directions consume
        # node egress once; do not count SSH directory/heartbeat sessions.
        self.client.relay.capacity.record(len(data))
        self.download_bytes += len(data)
        if self.client.relay.diagnostics:
            self.client.relay.diagnostics.traffic(self.client, 'download', len(data))
            self.client.relay.diagnostics.traffic(self.room.owner, 'upload', len(data))
        super().write(data)

    def eof_received(self):
        self.event('stream_eof', direction='upload')
        return super().eof_received()

    def write_eof(self):
        self.event('stream_eof', direction='download')
        return super().write_eof()

    def connection_lost(self, exc):
        self.event('stream_transport_lost', error_type=type(exc).__name__ if exc else None)
        super().connection_lost(exc)

    def block(self, direction):
        if direction not in self.blocked:
            self.blocked[direction] = time.monotonic()
            self.event('backpressure_start', direction=direction)

    def unblock(self, direction):
        started = self.blocked.pop(direction, None)
        if started is not None:
            self.event('backpressure_end', direction=direction, duration_ms=round((time.monotonic()-started)*1000, 2))

    def pause_reading(self):
        self.block('upload')
        super().pause_reading()

    def resume_reading(self):
        self.unblock('upload')
        super().resume_reading()

    def pause_writing(self):
        self.block('download')
        super().pause_writing()

    def resume_writing(self):
        self.unblock('download')
        super().resume_writing()


class RelayServer:
    def __init__(self, host="0.0.0.0", port=3333, *, host_key, token,
                 max_connections=512, max_rooms=128, max_streams=128,
                 max_total_streams=512, max_client_streams=8, host_grace_seconds=120,
                 federation=None):
        if len(token) < 32:
            raise ValueError("The service token must contain at least 32 characters")
        self.host, self.port = host, port
        self.host_key, self.token = host_key, token
        self.max_connections, self.max_rooms, self.max_streams = max_connections, max_rooms, max_streams
        self.max_total_streams, self.max_client_streams = max_total_streams, max_client_streams
        self.streams, self.opening = set(), 0
        self.rooms: dict[str, Room] = {}
        self.peers: set[RoomPeer] = set()
        self.listener = None
        self.stun_transport = None
        self.host_grace_seconds = host_grace_seconds
        self.closed_rooms = {}
        self.closing = False
        self.federation = federation
        self.draining = False
        self.diagnostics = None
        self.account_authenticate = lambda token: None
        self.account_blocked = lambda account: False
        from .capacity import Capacity
        self.capacity = Capacity(self)

    def room_event(self, key, room, state=None, reason=''):
        state = state or ('ready' if room.available and room.port else 'waiting')
        event = {'type': 'room', 'state': state, 'reason': reason or
                 ('游戏世界已开放，可以进入游戏。' if state == 'ready' else '房主不在游戏内，请等待房主重新开放世界。'),
                 'game': {key: value for key, value in room.game.items() if key != 'mods'}, 'title': room.title,
                 'members': self.capacity.members(key),
                 'capacity': self.capacity.room_limit(room),
                 'game_players': room.game_players if room.available and time.monotonic() - room.game_reported < 15 else None}
        for session in tuple(room.chat_sessions):
            session.send(event)
        return event

    def end_room(self, key, reason='房主已结束联机，房间已关闭。'):
        room = self.rooms.pop(key, None)
        if not room:
            return
        if room.expiry:
            room.expiry.cancel()
        self.closed_rooms[key] = time.monotonic()
        while len(self.closed_rooms) > self.max_rooms * 4:
            self.closed_rooms.pop(next(iter(self.closed_rooms)))
        self.room_event(key, room, 'closed', reason)
        room.chat_history.clear()
        closing_peers = [peer for peer in self.peers if peer.room == key]
        for stream in tuple(room.streams):
            stream.close()
        # Leave authenticated control connections alive long enough to receive
        # the terminal event or discover it by polling after a chat reconnect.
        LOG.info('room closed %s', key)
        def disconnect_remaining():
            for peer in closing_peers:
                peer.conn.close()
        asyncio.get_running_loop().call_later(5, disconnect_remaining)

    async def start(self):
        self.listener = await asyncssh.create_server(
            lambda: RoomPeer(self), self.host, self.port,
            server_host_keys=[self.host_key], login_timeout=15,
            keepalive_interval=15, keepalive_count_max=3,
            encoding=None, window=256 * 1024,
        )
        self.port = self.listener.get_port()
        from .stun import start
        try:
            self.stun_transport = await start(self.host, self.port)
        except OSError:
            # TCP relay remains usable if UDP is occupied or prohibited.
            LOG.warning('UDP address discovery unavailable on this node')
        LOG.info("relay listening on %s:%s", self.host, self.port)
        return self

    async def close(self):
        if self.stun_transport:
            transport = self.stun_transport
            transport.close()
            closed = transport.get_protocol().closed
            if closed is not None:
                await closed
            self.stun_transport = None
        self.closing = True
        for room in self.rooms.values():
            if room.expiry:
                room.expiry.cancel()
        if self.listener:
            self.listener.close()
            await self.listener.wait_closed()
        connections = [peer.conn for peer in tuple(self.peers)]
        for conn in connections:
            conn.close()
        await asyncio.gather(*(conn.wait_closed() for conn in connections), return_exceptions=True)
        self.rooms.clear()


class RoomPeer(asyncssh.SSHServer):
    def __init__(self, relay):
        self.relay = relay
        self.conn = None
        self.role = self.room = None
        self.authenticated = False
        self.opening = 0
        self.secret = None
        self.control_sessions = 0
        self.control_calls = deque()
        self.streams = set()
        self.client_opening = 0
        self.chat_id = secrets.token_hex(12)
        self.member_id = self.chat_id
        self.membership_protected = False

    def connection_made(self, conn):
        self.conn = conn
        if len(self.relay.peers) >= self.relay.max_connections:
            conn.close()
        else:
            self.relay.peers.add(self)

    def connection_lost(self, exc):
        self.relay.peers.discard(self)
        for stream in tuple(self.streams):
            stream.close()
        if self.relay.diagnostics:
            self.relay.diagnostics.disconnected(self, exc)
        room = self.relay.rooms.get(self.room)
        if room and room.lobby:
            room.lobby.leave(self)
        if room and room.owner is not self:
            self.relay.room_event(self.room, room)
        if room and room.owner is self:
            room.port = 0
            room.pack_port = 0
            room.available = False
            for stream in tuple(room.streams):
                stream.close()
            if not self.relay.closing:
                self.relay.room_event(self.room, room, 'waiting', '房主连接暂时中断，请等待恢复；房间尚未关闭。')
                if not room.lobby or not room.lobby.active(room.lobby.manager):
                    room.expiry = asyncio.get_running_loop().call_later(
                        self.relay.host_grace_seconds, self.relay.end_room, self.room,
                        '世界提供者长时间未恢复连接，房间已关闭。')
            LOG.info("room waiting %s", self.room)

    def begin_auth(self, username):
        return True

    def public_key_auth_supported(self):
        return bool(self.relay.federation)

    def validate_public_key(self, username, key):
        if not self.relay.federation or not username.startswith('federation:'):
            return False
        node_id = username.partition(':')[2]
        if self.relay.federation.accepts(node_id, key):
            self.role, self.room = 'federation', None
            self.node_id = node_id
            return True
        return False

    def password_auth_supported(self):
        return True

    def reject_auth(self, reason):
        count = getattr(self, '_auth_rejections', 0)
        self._auth_rejections = count + 1
        if count < 3:
            LOG.warning('node_auth_rejected peer=%s reason=%s', self.chat_id, reason)
            if self.relay.diagnostics:
                self.relay.diagnostics.auth_rejected(self, reason)
        return False

    async def validate_password(self, username, password):
        try:
            role, claimed_room = username.split(":", 1)
            credentials = json.loads(password)
            token, secret = credentials["token"], credentials.get("secret", '')
            if role not in ("host", "client", "directory"):
                return self.reject_auth('invalid-role')
            self.diagnostic_role = role
            self.account = self.relay.account_authenticate(token)
            import inspect
            if inspect.isawaitable(self.account):
                self.account = await self.account
            if not isinstance(token, str) or (not hmac.compare_digest(token, self.relay.token) and not self.account):
                return self.reject_auth('credential-rejected')
            self.credential = token
            if role == 'directory':
                if claimed_room != 'rooms':
                    return self.reject_auth('invalid-directory')
                self.role, self.room = role, None
                return True
            if not isinstance(secret, str) or not 32 <= len(secret) <= 128:
                return self.reject_auth('invalid-room-proof')
            if not hmac.compare_digest(room_id(secret), claimed_room):
                return self.reject_auth('room-proof-mismatch')
            existing = self.relay.rooms.get(claimed_room)
            from .room_lobby import identity
            member_token = credentials.get('member_token', '')
            if member_token and (not isinstance(member_token, str) or not 32 <= len(member_token) <= 128 or
                                 any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in member_token)):
                return self.reject_auth('invalid-member-proof')
            self.member_id = identity(claimed_room, member_token) if member_token else self.chat_id
            self.membership_protected = bool(member_token)
            if existing and existing.lobby:
                if self.member_id in existing.lobby.blocked or any(
                        self.account and v.get('account') == self.account for v in existing.lobby.blocked.values()):
                    return self.reject_auth('room-removed')
                if existing.lobby.active(self.member_id):
                    return self.reject_auth('member-already-connected')
                if role == 'host' and existing.owner.membership_protected and self.member_id != existing.lobby.provider:
                    return self.reject_auth('world-provider-proof-required')
            if role == 'client' and not existing:
                return self.reject_auth('room-not-found')
            if role == 'client' and not self.relay.capacity.can_join(claimed_room):
                return self.reject_auth('room-capacity')
            if claimed_room in self.relay.closed_rooms:
                return self.reject_auth('room-closed')
            if role == "host" and ((existing and not existing.owner.conn.is_closed()) or
                    (not existing and (self.relay.draining or len(self.relay.rooms) >= self.relay.max_rooms))):
                return self.reject_auth('host-exists' if existing else 'node-capacity')
            self.role, self.room = role, claimed_room
            self.secret = secret
            return True
        except (ValueError, TypeError, KeyError, UnicodeError):
            return self.reject_auth('malformed-credentials')

    def auth_completed(self):
        if self.role == 'client' and not self.relay.capacity.can_join(self.room):
            self.reject_auth('room-capacity')
            self.conn.close()
            return
        if self.role == "host":
            # Reserve synchronously. A duplicate cannot replace a live room.
            existing = self.relay.rooms.get(self.room)
            if (existing and not existing.owner.conn.is_closed()) or (
                    not existing and (self.relay.draining or len(self.relay.rooms) >= self.relay.max_rooms)):
                self.reject_auth('host-exists' if existing else 'node-capacity')
                self.conn.close()
                return
            if existing:
                if existing.expiry:
                    existing.expiry.cancel()
                existing.expiry = None
                existing.owner, existing.port, existing.pack_port = self, 0, 0
                existing.available = False
            else:
                self.relay.rooms[self.room] = Room(self, secret=self.secret)
        self.authenticated = True
        if self.role in ('host', 'client') and self.room in self.relay.rooms:
            room = self.relay.rooms[self.room]
            if room.lobby is None:
                from .room_lobby import RoomLobby
                room.lobby = RoomLobby(self.relay, room)
            if self.role == 'host' and not self.membership_protected and room.lobby.provider != self.member_id:
                previous = room.lobby.provider
                room.lobby.provider = self.member_id
                if room.lobby.manager == previous:
                    room.lobby.manager = self.member_id
            room.lobby.join(self)
        if self.relay.diagnostics:
            self.relay.diagnostics.connected(self)
        if self.role in ('host', 'client') and self.room in self.relay.rooms:
            self.relay.room_event(self.room, self.relay.rooms[self.room])

    def session_requested(self):
        # One chat stream, one availability watcher, plus one foreground
        # metadata call may coexist. exec requests are still rejected by the
        # session object because it only accepts the two known subsystems.
        if not self.authenticated or self.control_sessions >= 4:
            return False
        self.control_sessions += 1
        return RoomControlSession(self)

    async def server_requested(self, listen_host, listen_port):
        room = self.relay.rooms.get(self.room)
        if not self.authenticated or self.role != "host" or not room or room.owner is not self:
            return False
        if listen_host not in ("127.0.0.1", "buggcraft-mods") or listen_port != 0:
            return False
        pack = listen_host == 'buggcraft-mods'
        if (room.pack_port if pack else room.port):
            return False
        # Bind only an ephemeral loopback port. The public has no raw game port.
        listener = await self.conn.forward_local_port("127.0.0.1", 0, listen_host, listen_port)
        if pack:
            room.pack_port = listener.get_port()
        else:
            room.port = listener.get_port()
        LOG.info("room online %s", self.room)
        return listener

    async def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
        room = self.relay.rooms.get(self.room)
        pack = dest_port == 25564
        target_port = (room.pack_port if pack else room.port) if room else 0
        if (not self.authenticated or self.role != "client" or dest_host != self.room or dest_port not in (25564, 25565)
                or not room or not target_port or (not pack and not room.available) or room.owner.conn.is_closed()):
            raise asyncssh.ChannelOpenError(asyncssh.OPEN_ADMINISTRATIVELY_PROHIBITED, "Room unavailable")
        if (len(room.streams) + room.owner.opening >= self.relay.max_streams
                or len(self.streams) + self.client_opening >= self.relay.max_client_streams
                or len(self.relay.streams) + self.relay.opening >= self.relay.max_total_streams):
            raise asyncssh.ChannelOpenError(asyncssh.OPEN_RESOURCE_SHORTAGE, "Room connection limit")
        room.owner.opening += 1
        self.client_opening += 1
        self.relay.opening += 1
        try:
            _, peer = await asyncio.wait_for(
                asyncio.get_running_loop().create_connection(SSHForwarder, "127.0.0.1", target_port), 10)
            if self.relay.rooms.get(self.room) is not room:
                peer.close()
                raise asyncssh.ChannelOpenError(asyncssh.OPEN_CONNECT_FAILED, "Room disconnected")
            return CountedForwarder(peer, room, self, 'modpack' if pack else 'game')
        except (OSError, asyncio.TimeoutError) as exc:
            if self.relay.diagnostics:
                self.relay.diagnostics.emit(self, 'host_connect_failed', error_type=type(exc).__name__)
            raise asyncssh.ChannelOpenError(asyncssh.OPEN_CONNECT_FAILED, "Host unavailable") from exc
        finally:
            room.owner.opening -= 1
            self.client_opening -= 1
            self.relay.opening -= 1
