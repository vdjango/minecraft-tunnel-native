"""Authenticated preparation state and management, independent of game transport."""
import asyncio
import hashlib
import json
import math
import re
import secrets
import time

CAPABILITY = 'room-lobby-v1'
STATES = {'idle', 'checking', 'downloading', 'installing', 'verifying', 'ready',
          'failed', 'cancelled', 'starting', 'playing'}
STAGES = {'game', 'java', 'libraries', 'assets', 'loader', 'mods', 'resources',
          'configuration', 'verify', 'waiting'}


def identity(room, proof):
    return hashlib.sha256((room + ':' + proof).encode()).hexdigest()[:24]


def config_id(game):
    keys = ('version', 'loader', 'loader_version', 'mods_hash', 'mods_count',
            'resources_hash', 'resources_count')
    value = {key: game.get(key) for key in keys}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def manager_account(room):
    if room is None:
        return None
    lobby = getattr(room, 'lobby', None)
    manager = lobby.active(lobby.manager) if lobby else room.owner
    return getattr(manager, 'account', None)


class RoomLobby:
    def __init__(self, relay, room):
        self.relay, self.room = relay, room
        self.members = {}
        self.manager = room.owner.member_id
        self.provider = room.owner.member_id
        self.revision = 0
        self.config_revision = 0
        self.configuration = config_id(room.game)
        self.transfer = None
        self.blocked = {}
        from .room_logs import RoomLogs
        self.logs = RoomLogs(self)

    def active(self, member):
        return next((p for p in self.relay.peers if p.authenticated and
                     p.room == self.room.owner.room and p.member_id == member and
                     not p.conn.is_closed()), None)

    def join(self, peer):
        member = peer.member_id
        previous = self.members.get(member, {})
        self.members[member] = dict(previous, id=member, name=previous.get('name', '玩家'),
            online=True, state=previous.get('state', 'idle'),
            updated=int(time.time()), _seen=time.monotonic(), _peer=peer)
        # Bound reconnect history independently of the node's live connection limit.
        for key, value in list(self.members.items()):
            if (len(self.members) > 1024 and not value.get('online') and
                    key not in (self.manager, self.provider) and key not in self.blocked):
                del self.members[key]
        self.emit()

    def leave(self, peer):
        self.logs.clear(peer.member_id)
        value = self.members.get(peer.member_id)
        if value and value.get('_peer') is peer:
            value.update(online=False, updated=int(time.time()), path='unknown', rtt_ms=None)
        if self.transfer and peer.member_id in (self.transfer['from'], self.transfer['to']):
            self.transfer = None
        self.emit()

    def changed_config(self, game):
        new_id = config_id(game)
        if new_id == self.configuration:
            return
        self.configuration = new_id
        self.config_revision += 1
        for value in self.members.values():
            value.update(state='idle', config_id=new_id, task='', done=0, total=0,
                         speed=0, seq=-1, updated=int(time.time()))
        self.emit()

    def emit(self):
        self.revision += 1
        for session in tuple(self.room.chat_sessions):
            session.send({'type': 'members', 'revision': self.revision})

    def require_manager(self, peer):
        if peer.member_id != self.manager:
            raise ValueError('Only the room manager may do this')

    def public(self, value):
        stale = not value.get('online') or time.monotonic() - value.get('_seen', 0) > 20
        result = {key: item for key, item in value.items() if key in (
            'id', 'name', 'online', 'state', 'stage', 'task', 'seq', 'done', 'total',
            'speed', 'files_done', 'files_total', 'code', 'updated', 'config_id')}
        result.update(manager=value['id'] == self.manager, provider=value['id'] == self.provider,
                      stale=stale, path='unknown' if stale else value.get('path', 'unknown'),
                      rtt_ms=None if stale else value.get('rtt_ms'),
                      rtt_kind=value.get('rtt_kind', 'unknown'))
        return result

    def snapshot(self, peer, request):
        offset, limit = request.get('offset', 0), request.get('limit', 32)
        if type(offset) is not int or not 0 <= offset <= 2048 or type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError('Invalid member pagination')
        if self.transfer and self.transfer['expires'] <= time.time():
            self.transfer = None
        values = sorted(self.members.values(), key=lambda v: (not v.get('online'), v['id'] != self.manager, v['id']))
        online = [v for v in values if v.get('online')]
        ready = sum(v.get('state') in ('ready', 'starting', 'playing') and
                    v.get('config_id') == self.configuration for v in online)
        end = min(len(values), offset + limit)
        return dict(revision=self.revision, self_id=peer.member_id, manager_id=self.manager,
            provider_id=self.provider, config_id=self.configuration, config_revision=self.config_revision,
            members=[self.public(v) for v in values[offset:end]], offset=offset,
            next=end if end < len(values) else None, total=len(values), online=len(online), ready=ready,
            transfer=self.transfer if self.transfer and peer.member_id in
                (self.transfer['from'], self.transfer['to']) else None,
            logs=self.logs.view(peer),
            blocked=[dict(id=k, name=v['name']) for k, v in self.blocked.items()]
                if peer.member_id == self.manager else [])

    def dispatch(self, peer, request):
        op = request['op']
        if not peer.authenticated or peer.conn.is_closed() or peer.member_id in self.blocked:
            raise ValueError('Room membership ended')
        value = self.members.get(peer.member_id)
        if value is None or value.get('_peer') is not peer:
            raise ValueError('Room membership ended')
        if op in ('logs_request', 'logs_answer', 'logs_revoke', 'logs_append', 'logs_read'):
            return self.logs.dispatch(peer, request)
        if op == 'members':
            if 'path' in request:
                self.dispatch(peer, dict(request, op='member_path'))
            return self.snapshot(peer, request)
        if op == 'member_update':
            name = request.get('name', value['name'])
            if not isinstance(name, str) or not 1 <= len(name) <= 32 or any(not c.isprintable() or c == '§' for c in name):
                raise ValueError('Invalid member name')
            state, stage = request.get('state', value['state']), request.get('stage', 'waiting')
            if state not in STATES or stage not in STAGES:
                raise ValueError('Invalid preparation state')
            seq, task = request.get('seq', 0), request.get('task', '')
            if type(seq) is not int or not 0 <= seq < 2**53 or not isinstance(task, str) or not re.fullmatch('[a-zA-Z0-9_-]{0,64}', task):
                raise ValueError('Invalid preparation sequence')
            if request.get('config_id') != self.configuration:
                raise ValueError('Room configuration changed; refresh before preparing')
            if seq <= value.get('seq', -1):
                return dict(accepted=False, revision=self.revision)
            retired = value.setdefault('_retired_tasks', [])
            if task in retired:
                return dict(accepted=False, revision=self.revision)
            if task != value.get('task', task) and value.get('task'):
                retired.append(value['task'])
                del retired[:-8]
            fields = {}
            for key in ('done', 'total', 'speed', 'files_done', 'files_total'):
                number = request.get(key, 0)
                if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number < 2**53:
                    raise ValueError('Invalid preparation counters')
                fields[key] = int(number)
            if fields['total'] and fields['done'] > fields['total']:
                raise ValueError('Invalid preparation progress')
            code = request.get('code', '')
            if code not in ('', 'download_failed', 'install_failed', 'cancelled', 'configuration_changed', 'optional_failed'):
                raise ValueError('Invalid preparation result')
            value.update(fields, name=name, state=state, stage=stage, seq=seq, task=task,
                         code=code, config_id=self.configuration, updated=int(time.time()), _seen=time.monotonic())
            self.emit()
            return dict(accepted=True, revision=self.revision)
        if op == 'member_path':
            path, rtt, kind = request.get('path'), request.get('rtt_ms'), request.get('rtt_kind', 'unknown')
            if path not in ('p2p', 'relay', 'unknown', 'local') or kind not in ('game', 'node', 'unknown', 'local'):
                raise ValueError('Invalid member path')
            if rtt is not None and (type(rtt) not in (int, float) or not math.isfinite(rtt) or not 0 <= rtt <= 1000000):
                raise ValueError('Invalid member latency')
            value.update(path=path, rtt_ms=rtt, rtt_kind=kind, _seen=time.monotonic())
            return dict(accepted=True)
        if op in ('member_kick', 'member_unblock', 'owner_offer', 'owner_cancel'):
            self.require_manager(peer)
        target = request.get('member')
        if op == 'member_unblock':
            self.blocked.pop(target, None)
            self.emit()
            return dict(unblocked=True)
        if op == 'member_kick':
            victim = self.active(target)
            if not victim or target in (self.manager, self.provider):
                raise ValueError('Cannot remove this member; close the world separately')
            if len(self.blocked) >= 256:
                raise ValueError('Room block limit reached')
            self.blocked[target] = dict(name=self.members[target]['name'], account=getattr(victim, 'account', None))
            for session in tuple(self.room.chat_sessions):
                if session.peer is victim:
                    session.send(dict(type='removed', reason='你已被房主移出房间。'))
            signal = getattr(victim, 'peer_signal', None)
            if signal:
                signal.close()  # Provider must also close existing direct game/file channels.
            victim.authenticated = False
            for stream in tuple(victim.streams):
                stream.close()
            self.leave(victim)
            asyncio.get_running_loop().call_later(.15, victim.conn.close)
            return dict(removed=True)
        if op == 'owner_offer':
            if not self.active(target) or target == self.manager:
                raise ValueError('Choose another online member')
            self.transfer = dict(id=secrets.token_hex(12), **{'from': self.manager, 'to': target},
                                 expires=int(time.time()) + 30)
            self.emit()
            return dict(transfer=self.transfer)
        if op == 'owner_cancel':
            self.transfer = None
            self.emit()
            return dict(cancelled=True)
        if op == 'owner_answer':
            offer = self.transfer
            if (not offer or offer['to'] != peer.member_id or offer['id'] != request.get('offer') or
                    offer['expires'] <= time.time() or not self.active(offer['from'])):
                raise ValueError('Ownership offer expired or unavailable')
            accept = request.get('accept')
            if type(accept) is not bool:
                raise ValueError('Acceptance required')
            if accept:
                self.logs.clear()
                self.manager = peer.member_id
            self.transfer = None
            self.emit()
            return dict(transferred=accept, manager_id=self.manager)
        raise ValueError('Unknown room management operation')
