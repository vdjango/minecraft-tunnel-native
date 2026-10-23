"""Python management plane for the C++ SSH/STUN data plane.

The inherited protocol handlers remain the authority for room/account/federation
permissions. Only bounded control data and periodic counters cross the private
child-process pipes. Game and modpack payloads never enter this interpreter.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import secrets
import time

import asyncssh
from .relay import RelayServer as PythonRelay, RoomPeer
from .diagnostics import player_identity

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
MAX_IPC = 2 * 1024 * 1024


class NativeConnection:
    def __init__(self, relay, identity, username):
        self.relay, self.identity, self.username = relay, identity, username
        self.closed = False
        self.listeners = {}

    def is_closed(self):
        return self.closed

    def close(self):
        if not self.closed:
            self.closed = True
            self.relay.send(dict(op='disconnect', session=self.identity))

    async def wait_closed(self):
        while self.identity in self.relay.native_peers:
            await asyncio.sleep(.01)

    def get_extra_info(self, key, default=None):
        return self.username if key == 'username' else default

    async def forward_local_port(self, host, port, destination, destination_port):
        # This is a logical SSH listener ID, not an exposed TCP socket.
        self.relay.listener_sequence = self.relay.listener_sequence % 50000 + 1
        result = NativeListener(self.relay.listener_sequence, destination)
        self.listeners[result.port] = result
        return result


class NativeListener:
    def __init__(self, port, host):
        self.port, self.host, self.closed = port, host, False

    def get_port(self):
        return self.port

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class NativeChannel:
    def __init__(self, relay, peer, identity):
        self.relay, self.peer, self.identity = relay, peer, identity
        self.closed = self.paused = False
        self.pending = 0
        self.handler = None

    def write(self, data):
        if self.closed:
            return
        if not isinstance(data, bytes) or len(data) > 1024 * 1024 or self.pending + len(data) > 1024 * 1024:
            self.close()
            return
        self.pending += len(data)
        self.relay.send(dict(op='channel_write', session=self.peer.conn.identity,
                             channel=self.identity, data=base64.b64encode(data).decode()))
        if self.pending >= 131072 and not self.paused:
            self.paused = True
            self.handler.pause_writing()

    def written(self, length):
        self.pending = max(0, self.pending - length)
        if self.pending < 65536 and self.paused:
            self.paused = False
            self.handler.resume_writing()

    def close(self):
        if not self.closed:
            self.closed = True
            self.relay.send(dict(op='channel_close', session=self.peer.conn.identity, channel=self.identity))

    def write_eof(self):
        self.close()


class NativeStream:
    def __init__(self, relay, room, client, identity, kind):
        self.relay, self.room, self.client, self.identity = relay, room, client, identity
        self.trace, self.closed = secrets.token_hex(6), False
        self.started = time.monotonic()
        self.upload_bytes = self.download_bytes = 0
        room.streams.add(self)
        client.streams.add(self)
        relay.streams.add(self)
        self.event('stream_open', kind=kind, engine='native')

    def event(self, event, **fields):
        if self.relay.diagnostics:
            self.relay.diagnostics.emit(self.client, event, stream=self.trace, **fields)

    def count(self, upload, download):
        self.upload_bytes += upload
        self.download_bytes += download
        self.relay.capacity.record(upload + download)
        if self.relay.diagnostics:
            for peer, up, down in ((self.client, upload, download), (self.room.owner, download, upload)):
                self.relay.diagnostics.traffic(peer, 'upload', up)
                self.relay.diagnostics.traffic(peer, 'download', down)

    def close(self, notify=True):
        if self.closed:
            return
        self.closed = True
        self.event('stream_closed', upload_bytes=self.upload_bytes, download_bytes=self.download_bytes,
                   duration_ms=round((time.monotonic() - self.started) * 1000, 2))
        self.room.streams.discard(self)
        self.client.streams.discard(self)
        self.relay.streams.discard(self)
        if notify:
            self.relay.send(dict(op='stream_close', session=self.client.conn.identity, stream=self.identity))


class RelayServer(PythonRelay):
    def __init__(self, *args, native_binary=None, workers=None, buffer_kib=64, compression='auto', **kwargs):
        super().__init__(*args, **kwargs)
        self.native_binary = native_binary or os.environ.get('BUGGCRAFT_NATIVE_CORE')
        if not self.native_binary:
            executable = 'buggcraft-core.exe' if os.name == 'nt' else 'buggcraft-core'
            candidates = [ROOT / 'bin' / executable, ROOT / 'build' / executable,
                          ROOT / 'build' / 'Release' / executable]
            self.native_binary = str(next((p for p in candidates if p.is_file()), candidates[0]))
        self.workers = min(8, os.cpu_count() or 1) if workers is None else workers
        if (type(self.workers) is not int or type(buffer_kib) is not int
                or not 1 <= self.workers <= 64 or not 32 <= buffer_kib <= 4096):
            raise ValueError('Invalid native worker count or buffer size')
        self.buffer_kib = buffer_kib
        if compression not in ('auto', 'off'):
            raise ValueError('Invalid relay compression policy')
        self.compression = compression
        self.process = self.reader_task = self.writer_task = None
        self.native_peers, self.native_channels, self.native_streams = {}, {}, {}
        self.listener_sequence = self.stream_sequence = 0
        self.outbox = asyncio.Queue(maxsize=512)
        self.tasks = set()
        self.ready = None
        self.native_stats = {}
        self.failed = asyncio.Event()

    async def wait_failed(self):
        await self.failed.wait()
        raise ConnectionError('Native core exited; service restart required')

    def compression_capability(self, room=None):
        if self.compression == 'off':
            return None
        if room is None:
            return 'zstd-1'
        connection = room.owner.conn
        return 'zstd-1' if not connection.closed and any(
            listener.host == 'buggcraft-zstd' and not listener.closed
            for listener in connection.listeners.values()) else None

    def send(self, value):
        if not self.process or self.process.returncode is not None:
            return
        try:
            self.outbox.put_nowait(value)
        except asyncio.QueueFull:
            # Fail closed rather than retaining unbounded control buffers or
            # accepting an authorization whose reply can no longer be sent.
            LOG.error('native_control_overflow action=stop-core')
            self.process.kill()

    async def _write(self):
        try:
            while True:
                value = await self.outbox.get()
                self.process.stdin.write(json.dumps(value, separators=(',', ':')).encode() + b'\n')
                await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionError):
            pass

    async def start(self):
        # Read Unicode paths in Python and import the key through the private
        # pipe. Windows narrow-character C library paths depend on the ACP.
        key = self.host_key if isinstance(self.host_key, asyncssh.SSHKey) else asyncssh.read_private_key(self.host_key)
        key_data = key.export_private_key().decode('ascii')
        self.ready = asyncio.get_running_loop().create_future()
        try:
            self.process = await asyncio.create_subprocess_exec(
                str(self.native_binary), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, limit=MAX_IPC,
                creationflags=0x08000000 if os.name == 'nt' else 0)
            self.writer_task = asyncio.create_task(self._write(), name='native-control-writer')
            self.reader_task = asyncio.create_task(self._read(), name='native-control-reader')
            self.send(dict(op='init', host=self.host, port=self.port, host_key_data=key_data,
                           workers=self.workers, max_connections=self.max_connections,
                           buffer_bytes=self.buffer_kib * 1024, max_streams=self.max_total_streams))
            info = await asyncio.wait_for(asyncio.shield(self.ready), 15)
            self.port = info['port']
            self.native_stats = info
            self.listener = NativeListener(self.port, self.host)
            LOG.info('native_ready port=%d workers=%d buffer_kib=%d', self.port, self.workers, self.buffer_kib)
            return self
        except BaseException:
            await self.close()
            raise

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                value = json.loads(line)
                if value.get('op') == 'ready':
                    if not self.ready.done():
                        self.ready.set_result(value)
                    continue
                if value.get('op') == 'fatal':
                    raise RuntimeError('Native core failed: ' + str(value.get('reason', 'unknown')))
                task = asyncio.create_task(self._receive(value))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        except (ValueError, OSError, RuntimeError) as exc:
            if not self.closing:
                LOG.error('native_control_lost reason=%s', type(exc).__name__)
            if not self.ready.done():
                self.ready.set_exception(exc)
        finally:
            if not self.ready.done():
                self.ready.set_exception(ConnectionError('Native core exited during startup'))
            for identity in tuple(self.native_peers):
                self._disconnected(identity)
            if not self.closing:
                LOG.error('native_core_exited action=restart-service')
                self.failed.set()
            if not self.closing and self.process.returncode is None:
                self.process.kill()

    def _disconnected(self, identity, reason='transport-closed'):
        peer = self.native_peers.pop(identity, None)
        if not peer:
            return
        peer.disconnect_reason = reason
        peer.conn.closed = True
        for key, channel in tuple(self.native_channels.items()):
            if channel.peer is peer:
                self.native_channels.pop(key, None)
                channel.closed = True
                channel.handler.connection_lost(None)
        peer.connection_lost(None)

    async def _receive(self, value):
        result = None
        try:
            result = await self._dispatch(value)
        except (OSError, ValueError, TypeError, KeyError, asyncssh.Error) as exc:
            LOG.info('native_control_rejected op=%s reason=%s', value.get('op'), type(exc).__name__)
            result = dict(allowed=False)
        except Exception:
            LOG.exception('native_control_handler_failed op=%s', value.get('op'))
            result = dict(allowed=False)
        finally:
            if 'rid' in value:
                self.send(dict(op='reply', worker=value['worker'], rid=value['rid'], result=result))

    async def _dispatch(self, value):
        op, identity = value['op'], value.get('session')
        peer = self.native_peers.get(identity)
        if op == 'auth':
            if not peer:
                peer = RoomPeer(self)
                peer.connection_made(NativeConnection(self, identity, value['username']))
                self.native_peers[identity] = peer
            if peer.conn.closed or peer.authenticated:
                return dict(allowed=False)
            peer.conn.username = value['username']
            if value['method'] == 'password':
                allowed = await peer.validate_password(value['username'], value['password'])
            else:
                allowed = peer.validate_public_key(value['username'], asyncssh.import_public_key(value['key']))
            if allowed and value.get('signed', True):
                peer.auth_completed()
                allowed = peer.authenticated and not peer.conn.closed
            return dict(allowed=bool(allowed))
        if op == 'disconnected':
            self._disconnected(identity, value.get('reason', 'transport-closed'))
            return
        if op == 'stats':
            self.native_stats.update(value)
            return
        if op == 'stream_stats':
            stream = self.native_streams.get(value['stream'])
            if stream:
                stream.count(value['upload'], value['download'])
            return
        if op == 'stream_closed':
            stream = self.native_streams.pop(value['stream'], None)
            if stream:
                stream.close(False)
            return
        if op == 'stream_event':
            stream = self.native_streams.get(value['stream'])
            if stream:
                stream.event(value['event'], direction=value.get('direction'),
                             duration_ms=value.get('duration_ms'))
            return
        if not peer or not peer.authenticated or peer.conn.closed:
            return dict(allowed=False)
        if op == 'forward':
            if value['host'] == 'buggcraft-zstd':
                room = self.rooms.get(peer.room)
                allowed = (self.compression == 'auto' and peer.role == 'host' and room and
                           room.owner is peer and value['port'] == 0 and
                           not any(l.host == 'buggcraft-zstd' and not l.closed for l in peer.conn.listeners.values()))
                listener = await peer.conn.forward_local_port('127.0.0.1', 0, value['host'], 0) if allowed else None
                return dict(allowed=bool(allowed), port=listener.get_port() if listener else 0)
            listener = await peer.server_requested(value['host'], value['port'])
            return dict(allowed=bool(listener), port=listener.get_port() if listener else 0)
        if op == 'cancel_forward':
            room = self.rooms.get(peer.room)
            listener = peer.conn.listeners.get(value['port'])
            if listener and listener.host == value['host']:
                listener.close()
                if room and room.owner is peer:
                    if room.port == listener.port:
                        room.port = 0
                    if room.pack_port == listener.port:
                        room.pack_port = 0
                return dict(allowed=True)
            return dict(allowed=False)
        if op == 'stream_open':
            room = self.rooms.get(peer.room)
            pack = value['port'] == 25564
            compressed = value['port'] == 25566
            port = (room.pack_port if pack else room.port) if room else 0
            if compressed and room and self.compression_capability(room):
                port = next(l.port for l in room.owner.conn.listeners.values()
                            if l.host == 'buggcraft-zstd' and not l.closed)
            elif compressed:
                port = 0
            if (peer.role != 'client' or value['host'] != peer.room or value['port'] not in (25564, 25565, 25566)
                    or not room or not port or (not pack and not room.available) or room.owner.conn.is_closed()):
                return dict(allowed=False)
            if (len(room.streams) >= self.max_streams or len(peer.streams) >= self.max_client_streams
                    or len(self.streams) >= self.max_total_streams):
                return dict(allowed=False)
            self.stream_sequence += 1
            stream = NativeStream(self, room, peer, self.stream_sequence, 'modpack' if pack else 'game')
            self.native_streams[stream.identity] = stream
            listener = room.owner.conn.listeners[port]
            return dict(allowed=True, stream=stream.identity, owner=room.owner.conn.identity,
                        host=listener.host, port=port, player=player_identity(peer))
        key = (identity, value.get('channel'))
        if op == 'subsystem':
            handler = peer.session_requested()
            if not handler:
                return dict(allowed=False)
            channel = NativeChannel(self, peer, value['channel'])
            channel.handler = handler
            handler.connection_made(channel)
            if not handler.subsystem_requested(value['name']):
                handler.connection_lost(None)
                return dict(allowed=False)
            self.native_channels[key] = channel
            handler.session_started()
            return dict(allowed=True)
        channel = self.native_channels.get(key)
        if not channel:
            return dict(allowed=False)
        if op == 'channel_data':
            channel.handler.data_received(base64.b64decode(value['data'], validate=True), None)
        elif op == 'channel_written':
            channel.written(value['length'])
        elif op == 'channel_eof':
            if not channel.handler.eof_received():
                channel.close()
        elif op == 'channel_closed':
            self.native_channels.pop(key, None)
            channel.closed = True
            channel.handler.connection_lost(None)
        return dict(allowed=True)

    async def close(self):
        self.closing = True
        for room in self.rooms.values():
            if room.expiry:
                room.expiry.cancel()
        if self.process:
            self.send(dict(op='shutdown'))
            try:
                await asyncio.wait_for(self.process.wait(), 8)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
            if self.reader_task:
                await self.reader_task
            if self.writer_task:
                self.writer_task.cancel()
                await asyncio.gather(self.writer_task, return_exceptions=True)
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for identity in tuple(self.native_peers):
            self._disconnected(identity)
        self.rooms.clear()
        self.native_streams.clear()
