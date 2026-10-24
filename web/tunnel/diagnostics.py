"""Bounded, asynchronous metadata journals. Never persist payloads or credentials."""
import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import time

LOG = logging.getLogger(__name__)
SAFE = re.compile(r'[a-zA-Z0-9_-]{1,90}')


def player_identity(peer):
    account = getattr(peer, 'account', None)
    return ('account-' + hashlib.sha256(account.encode()).hexdigest()[:32]
            if account else 'anonymous-' + peer.chat_id)


class Diagnostics:
    def __init__(self, root, relay, *, max_bytes=1024**3, days=7, queue_limit=8192):
        self.root, self.relay = Path(root), relay
        self.max_bytes, self.days = max_bytes, days
        self.queue = deque(maxlen=queue_limit)
        self.active = {}
        self.dropped = self.write_errors = 0
        self.task = None
        self.loop_lag_ms = 0
        self.last_prune = 0

    def emit(self, peer, event, **fields):
        session = self.active.get(peer)
        if not session:
            return
        if len(self.queue) == self.queue.maxlen:
            self.dropped += 1
        self.queue.append((session['player'], session['session'],
                           dict(at=time.time(), event=event, **fields)))

    def connected(self, peer):
        if peer.role not in ('host', 'client'):
            return
        account = getattr(peer, 'account', None)
        player = player_identity(peer)
        username = '匿名玩家'
        fed = self.relay.federation
        if account and fed and fed.accounts:
            username = fed.accounts.social.name(account)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        self.active[peer] = dict(player=player, session=stamp + '-' + secrets.token_hex(6),
                                  username=username, role=peer.role, room=peer.room,
                                  authenticated=bool(account), started=time.time(),
                                  path='unknown', control_rtt_ms=None, game_rtt_ms=None,
                                  peer_rtt_ms=None, last_report_at=None,
                                  upload_bytes=0, download_bytes=0, last_upload=0, last_download=0,
                                  last_sample=time.monotonic(), closed=False)
        self.emit(peer, 'connected', **self.public_session(self.active[peer]))

    @staticmethod
    def public_session(session):
        return {k: v for k, v in session.items() if not k.startswith('last_')}

    def traffic(self, peer, direction, size):
        session = self.active.get(peer)
        if session:
            session[direction + '_bytes'] += size

    def disconnected(self, peer, exc):
        session = self.active.get(peer)
        if session:
            session['closed'] = True
            # Exception text can contain addresses, credentials, or game text.
            # Preserve only its type and transport's numeric disconnect code.
            code = getattr(exc, 'code', None)
            self.emit(peer, 'disconnected', reason='service_shutdown' if self.relay.closing else
                      ('heartbeat_timeout' if getattr(peer, 'disconnect_reason', None) == 'heartbeat-timeout' else
                       'transport_error' if exc else 'transport_closed'),
                      error_type=type(exc).__name__ if exc else None,
                      error_code=code if type(code) is int else None,
                      **self.public_session(session))
            del self.active[peer]

    def report(self, peer, report):
        if peer not in self.active or not isinstance(report, dict):
            raise ValueError('Diagnostics unavailable for this session')
        now = time.monotonic()
        session = self.active[peer]
        if now - session.get('last_report', 0) < 2:
            raise ValueError('Diagnostics report too frequent')
        result = {}
        for key in ('control_rtt_ms', 'game_rtt_ms', 'peer_rtt_ms'):
            value = report.get(key)
            if value is not None:
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 120000:
                    raise ValueError('Invalid diagnostic latency')
                result[key] = round(value, 2)
        if report.get('path') not in ('p2p', 'relay', 'auto', 'mixed', 'unavailable'):
            raise ValueError('Invalid diagnostic path')
        result['path'] = report['path']
        session['last_report'] = now
        session.update({key: result.get(key) for key in
                        ('control_rtt_ms', 'game_rtt_ms', 'peer_rtt_ms')})
        session['path'] = result['path']
        session['last_report_at'] = time.time()
        self.emit(peer, 'client_report', source='launcher_report', **result)
        return {'ok': True}

    def transport_counts(self):
        counts = {'p2p': 0, 'relay': 0, 'unknown': 0}
        now = time.monotonic()
        for peer, session in self.active.items():
            if peer.role == 'client':
                path = session['path'] if now - session.get('last_report', 0) <= 20 else 'unknown'
                counts[path if path in ('p2p', 'relay') else 'unknown'] += 1
        return counts

    def online_players(self):
        values = []
        rooms = getattr(self.relay, 'rooms', {})
        now = time.monotonic()
        for peer, session in tuple(self.active.items()):
            if peer.role not in ('host', 'client'):
                continue
            room = rooms.get(peer.room)
            fresh = now - session.get('last_report', 0) <= 20
            values.append({
                'username': session['username'],
                'role': session['role'],
                'room': room.title if room else '房间暂不可用',
                'started': session['started'],
                'path': session['path'] if fresh else 'unknown',
                'control_rtt_ms': session['control_rtt_ms'] if fresh else None,
                'game_rtt_ms': session['game_rtt_ms'] if fresh else None,
                'peer_rtt_ms': session['peer_rtt_ms'] if fresh else None,
                'last_report_at': session['last_report_at'],
            })
        return sorted(values, key=lambda item: item['started'], reverse=True)[:1000]

    async def lifecycle(self, app):
        try:
            if self.root.is_symlink():
                raise OSError('Symlink journal directory')
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != 'nt':
                self.root.chmod(0o700)
        except OSError:
            self.write_errors += 1
            LOG.warning('Player diagnostic journal unavailable; forwarding continues')
        self.task = asyncio.create_task(self.run())
        try:
            yield
        finally:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            # Relay closes after the HTTP runner, so explicitly record shutdown.
            for peer in tuple(self.active):
                self.emit(peer, 'service_shutdown')
            await self.flush()

    async def run(self):
        expected = time.monotonic() + 1
        cpu_at, wall_at = time.process_time(), time.monotonic()
        while True:
            await asyncio.sleep(max(0, expected - time.monotonic()))
            now = time.monotonic()
            self.loop_lag_ms = round(max(0, now - expected) * 1000, 2)
            cpu = time.process_time()
            cpu_percent = round(100 * (cpu - cpu_at) / max(.001, now - wall_at), 2)
            cpu_at, wall_at = cpu, now
            expected = now + 1
            for peer, session in tuple(self.active.items()):
                interval = max(.001, now - session['last_sample'])
                self.emit(peer, 'sample', upload_bps=round((session['upload_bytes'] - session['last_upload']) / interval),
                          download_bps=round((session['download_bytes'] - session['last_download']) / interval),
                          upload_bytes=session['upload_bytes'], download_bytes=session['download_bytes'],
                          streams=len(peer.streams), loop_lag_ms=self.loop_lag_ms, process_cpu_percent=cpu_percent)
                session.update(last_sample=now, last_upload=session['upload_bytes'], last_download=session['download_bytes'])
            await self.flush()

    async def flush(self):
        batch = []
        while self.queue:
            batch.append(self.queue.popleft())
        # Await the worker even on cancellation: a second flush must never
        # append to the same files concurrently.
        task = asyncio.create_task(asyncio.to_thread(self._write, batch))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def _write(self, batch):
        grouped = defaultdict(list)
        for player, session, row in batch:
            grouped[(player, session)].append(row)
        try:
            for (player, session), rows in grouped.items():
                directory = self.root / player
                if self.root.is_symlink() or directory.is_symlink():
                    raise OSError('Symlink journal directory')
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                if os.name != 'nt':
                    directory.chmod(0o700)
                path = directory / (session + '.jsonl')
                if path.is_symlink():
                    raise OSError('Symlink journal file')
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
                descriptor = os.open(path, flags, 0o600)
                with os.fdopen(descriptor, 'a', encoding='utf-8') as output:
                    for row in rows:
                        output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                if os.name != 'nt':
                    path.chmod(0o600)
            if self.root.is_dir() and time.monotonic() - self.last_prune > 300:
                self._prune()
                self.last_prune = time.monotonic()
        except (OSError, ValueError):
            self.write_errors += 1
            LOG.warning('Player diagnostic journal unavailable (failure %d); forwarding continues', self.write_errors)

    def _paths(self):
        if self.root.is_symlink() or not self.root.is_dir():
            return []
        return [path for directory in self.root.iterdir()
                if not directory.is_symlink() and directory.is_dir() and SAFE.fullmatch(directory.name)
                for path in directory.glob('*.jsonl') if path.is_file() and not path.is_symlink()]

    def _prune(self):
        paths = sorted(self._paths(), key=lambda p: p.stat().st_mtime)
        total = sum(path.stat().st_size for path in paths)
        cutoff = time.time() - self.days * 86400
        active_sessions = tuple(self.active.values())
        active = {s['session'] for s in active_sessions}
        for index, path in enumerate(paths):
            if path.stem in active:
                # Rotate long-running sessions as well; preserve their identity
                # in the fresh file instead of allowing unbounded growth.
                if path.stat().st_size > 16 * 1024**2:
                    rows = self.read(path.parent.name, path.stem)['events']
                    header = next((r for r in rows if r.get('event') == 'connected'), None)
                    if header is None:
                        header = next((dict(at=time.time(), event='connected', **self.public_session(s))
                                       for s in active_sessions if s['session'] == path.stem), None)
                    if header:
                        previous = path.with_suffix('.1.jsonl')
                        if previous.is_symlink():
                            raise OSError('Symlink rotated journal')
                        path.replace(previous)
                        path.write_text(json.dumps(header) + '\n' + json.dumps(dict(at=time.time(), event='journal_rotated')) + '\n', encoding='utf-8')
                        path.chmod(0o600)
                continue
            if path.stat().st_mtime < cutoff or total > self.max_bytes or index < len(paths) - 5000:
                total -= path.stat().st_size
                path.unlink()
        for directory in self.root.iterdir():
            if not directory.is_symlink() and directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()

    def sessions(self):
        items = {}
        # Newest 2,000 session files bound response size and work per query.
        paths = sorted((path for path in self._paths() if not path.name.endswith('.1.jsonl')),
                       key=lambda p: p.stat().st_mtime, reverse=True)[:2000]
        for path in paths:
            try:
                with path.open(encoding='utf-8') as source:
                    first = json.loads(source.readline())
                if first.get('event') == 'connected':
                    items[path.stem] = {k: first.get(k) for k in
                        ('player', 'session', 'username', 'role', 'room', 'authenticated', 'started')}
                    items[path.stem]['online'] = False
            except (OSError, ValueError):
                continue
        for session in tuple(self.active.values()):
            items[session['session']] = dict(self.public_session(session), online=True)
        return sorted(items.values(), key=lambda s: s['started'], reverse=True)[:2000]

    def read(self, player, session):
        if not SAFE.fullmatch(player) or not SAFE.fullmatch(session):
            raise ValueError('Invalid diagnostic identity')
        path = self.root / player / (session + '.jsonl')
        if self.root.is_symlink() or path.parent.is_symlink() or path.is_symlink():
            raise ValueError('Symlink diagnostic path')
        try:
            rows = deque(maxlen=3600)
            previous = path.with_suffix('.1.jsonl')
            for part in (previous, path):
                if part.is_symlink():
                    raise ValueError('Symlink diagnostic path')
                if part.is_file():
                    with part.open(encoding='utf-8') as source:
                        rows.extend(source)
            events = []
            for row in rows:
                try:
                    events.append(json.loads(row))
                except ValueError:
                    continue
            return {'events': events, 'truncated': len(rows) == 3600}
        except FileNotFoundError:
            return {'events': [], 'truncated': False}
