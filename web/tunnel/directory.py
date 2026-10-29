"""Bounded room metadata and public account-directory control channels."""
from collections import deque
import asyncio
import json
import re
import time

import asyncssh
from .chat import RoomChatSession
from .peer_signal import PeerSignalSession
from .room_lobby import manager_account

SUBSYSTEM = 'buggcraft-rooms-v1'
MAX_FRAME = 16384


def clean_text(value, limit, default=''):
    if not isinstance(value, str):
        raise ValueError('Text field required')
    return ''.join(ch for ch in value if ch.isprintable() and ch not in '[]')[:limit].strip() or default


def game_metadata(value):
    if not isinstance(value, dict):
        raise ValueError('Game metadata must be an object')
    result = {key: clean_text(value.get(key, ''), limit) for key, limit in
              [('version', 64), ('loader', 32), ('loader_version', 64), ('mods_hash', 64)]}
    protocol = value.get('protocol')
    if protocol is not None and (type(protocol) is not int or not 0 <= protocol <= 2147483647):
        raise ValueError('Invalid game protocol')
    result['protocol'] = protocol
    mods = value.get('mods', [])
    count = value.get('mods_count', len(mods) if isinstance(mods, list) else 0)
    if not isinstance(mods, list) or len(mods) > 24 or type(count) is not int or not 0 <= count <= 10000:
        raise ValueError('Invalid mod manifest')
    cleaned = []
    for item in mods:
        if not isinstance(item, dict):
            raise ValueError('Invalid mod entry')
        name, digest, size = item.get('name'), item.get('sha256'), item.get('size')
        if (not isinstance(name, str) or not 1 <= len(name) <= 64 or
                '/' in name or chr(92) in name or name in ('.', '..') or
                not name.lower().endswith('.jar') or any(not ch.isprintable() for ch in name) or
                not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or
                type(size) is not int or not 0 <= size <= 2147483647):
            raise ValueError('Invalid mod entry')
        cleaned.append({'name': name, 'sha256': digest, 'size': size})
    result['mods_count'], result['mods'] = count, cleaned
    resources = value.get('resources', [])
    resource_count = value.get('resources_count', len(resources) if isinstance(resources, list) else 0)
    if (not isinstance(resources, list) or len(resources) > 24 or
            type(resource_count) is not int or not 0 <= resource_count <= 2000):
        raise ValueError('Invalid resource manifest')
    resource_preview = []
    for item in resources:
        if not isinstance(item, dict):
            raise ValueError('Invalid resource entry')
        name, path, digest, size = (item.get(key) for key in ('name', 'path', 'sha256', 'size'))
        if (not isinstance(name, str) or not 1 <= len(name) <= 240 or
                any(ch in name for ch in '/\\:') or any(not ch.isprintable() for ch in name) or
                name in ('.', '..') or not isinstance(path, str) or
                path not in ('resourcepacks/' + name, 'shaderpacks/' + name) or
                not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or
                type(size) is not int or not 0 <= size <= 2147483647):
            raise ValueError('Invalid resource entry')
        resource_preview.append({'name': name, 'path': path, 'sha256': digest, 'size': size})
    resource_hash = value.get('resources_hash', '')
    if not isinstance(resource_hash, str) or (resource_hash and not re.fullmatch('[0-9a-f]{64}', resource_hash)):
        raise ValueError('Invalid resource hash')
    result.update(resources_count=resource_count, resources_hash=resource_hash, resources=resource_preview)
    return result


def describe(key, room, preview=True, *, relay):
    game = room.game if preview else {key: value for key, value in room.game.items() if key not in ('mods', 'resources')}
    federation = relay.federation
    return {'id': key, 'title': room.title, 'public': room.public,
            'node_name': federation.name if federation else '',
            'node_region': federation.region if federation else '',
            'node_id': federation.id if federation else '',
            'relay_compression': relay.compression_capability(room) if hasattr(relay, 'compression_capability') else None,
            'diagnostics': bool(relay.diagnostics),
            'capabilities': ['room-lobby-v1'],
            'config_id': room.lobby.configuration if room.lobby else '',
            'require_invite': not room.public or room.require_invite,
            'capacity': relay.capacity.room_limit(room), 'connections': len(room.streams), 'game': game,
            'members': relay.capacity.members(key),
            'game_players': room.game_players if room.available and time.monotonic() - room.game_reported < 15 else None,
            'availability': 'ready' if room.available and room.port else 'waiting'}


class RoomControlSession(asyncssh.SSHServerSession):
    def __init__(self, peer):
        self.peer, self.channel = peer, None
        self.buffer = bytearray()
        self.started = False
        self.chat = None
        self.lookup_task = None

    def connection_made(self, channel):
        self.channel = channel

    def subsystem_requested(self, subsystem):
        if subsystem == 'buggcraft-feedback-upload-v1':
            if (self.peer.role != 'directory' or not getattr(self.peer, 'account', None)
                    or not getattr(self.peer.relay.federation, 'feedback', None)):
                return False
            from .feedback_native import FeedbackUploadSession
            self.chat = FeedbackUploadSession(self.peer, self.channel)
            return True
        if subsystem == 'buggcraft-feedback-file-v1':
            if self.peer.role != 'federation' or not getattr(self.peer.relay.federation, 'feedback', None):
                return False
            from .feedback import FeedbackFileSession
            self.chat = FeedbackFileSession(self.peer, self.channel)
            return True
        if subsystem == 'buggcraft-peer-v1':
            if self.peer.role not in ('host', 'client'):
                return False
            self.chat = PeerSignalSession(self.peer, self.channel)
            return True
        if subsystem == 'buggcraft-chat-v1':
            self.chat = RoomChatSession(self.peer, self.channel)
            return self.peer.role in ('host', 'client')
        self.started = subsystem == SUBSYSTEM
        return self.started

    def session_started(self):
        if self.chat:
            self.chat.start()

    def pause_writing(self):
        if self.chat:
            self.chat.paused = True

    def resume_writing(self):
        if self.chat:
            self.chat.paused = False

    def eof_received(self):
        if self.chat and hasattr(self.chat, 'eof_received'):
            return self.chat.eof_received()
        return False

    def connection_lost(self, exc):
        self.peer.control_sessions -= 1
        if self.lookup_task is not None:
            self.lookup_task.cancel()
        if self.chat:
            self.chat.close()

    def data_received(self, data, datatype):
        if self.chat:
            self.chat.data_received(data, datatype)
            return
        if not self.started or datatype is not None:
            self.channel.close()
            return
        self.buffer.extend(data)
        if len(self.buffer) > MAX_FRAME:
            self.channel.close()
            return
        while b'\n' in self.buffer:
            line, _, remaining = self.buffer.partition(b'\n')
            self.buffer = bytearray(remaining)
            now = time.monotonic()
            calls = self.peer.control_calls
            while calls and calls[0] < now - 10:
                calls.popleft()
            if len(calls) >= 32:
                self.channel.close()
                return
            calls.append(now)
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError('Request must be an object')
                if self.lookup_task is not None and not self.lookup_task.done():
                    self.channel.close()
                    return
                if (request.get('op') == 'account_lookup' and self.peer.role == 'directory') or request.get('op') == 'social':
                    self.lookup_task = asyncio.create_task(self._async_response(request))
                    continue
                response = {'ok': True, 'result': self.dispatch(request)}
            except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                response = {'ok': False, 'error': str(exc)[:160]}
            self.channel.write(json.dumps(response, ensure_ascii=True).encode() + b'\n')

    async def _async_response(self, request):
        if request.get('op') == 'account_lookup':
            await self._account_lookup_response(request)
            return
        try:
            peer, fed = self.peer, self.peer.relay.federation
            if not fed or not fed.accounts:
                raise ValueError('节点尚未启用账号功能。')
            if peer.role == 'directory' and not getattr(peer, 'account', None):
                raise ValueError('匿名接入没有好友功能，请领取个人接入码。')
            if peer.role not in ('directory', 'federation'):
                raise ValueError('Invalid social role')
            envelope = request.get('event')
            if peer.role == 'federation':
                actor, _ = fed.accounts.social.validate(envelope, fresh=True)
                account = actor
            else:
                account = peer.account
            if envelope is None:
                result = fed.accounts.social.state(account)
            else:
                actor, action = fed.accounts.social.validate(envelope, fresh=True)
                if actor != account or self.peer.relay.account_blocked(account):
                    raise ValueError('Account access denied')
                if action['op'] == 'join':
                    invite = fed.accounts.social.invitations.get((action['target'], actor))
                    room_id = invite['action']['room'] if invite else ''
                else:
                    room_id = action.get('room', '')
                if room_id and room_id not in peer.relay.rooms:
                    target = fed.locate(room_id)
                    if peer.role != 'directory' or not target:
                        raise ValueError('好友的房间暂不可用。')
                    result = await fed.account_rpc(target, 'social', event=envelope)
                    fed.accounts.social.ingest([envelope])
                else:
                    if action['op'] in ('invite', 'accept_join'):
                        room = peer.relay.rooms.get(room_id)
                        if not room or manager_account(room) != actor or not room.port:
                            raise ValueError('只能邀请好友加入你创建的可用房间。')
                    if action['op'] == 'presence' and room_id:
                        room = peer.relay.rooms.get(room_id)
                        owner = bool(room and manager_account(room) == actor)
                        member = any(getattr(p, 'account', None) == actor and getattr(p, 'room', None) == room_id
                                     for p in peer.relay.peers)
                        if not room or (action['room_role'] == 'host' and not owner) or (not owner and not member):
                            raise ValueError('当前房间连接已结束，请刷新状态。')
                    result = fed.accounts.social.apply(account, envelope)
            response = {'ok': True, 'result': result}
        except (ValueError, TypeError, KeyError, OSError, asyncssh.Error) as exc:
            response = {'ok': False, 'error': str(exc)[:160]}
        try:
            self.channel.write(json.dumps(response, ensure_ascii=True).encode() + b'\n')
        except (OSError, asyncssh.Error):
            pass

    async def _account_lookup_response(self, request):
        try:
            relay = self.peer.relay
            if not relay.federation or not relay.federation.accounts:
                raise ValueError('Account directory unavailable')
            if not getattr(self.peer, 'account', None):
                raise ValueError('匿名接入没有好友功能，请领取个人接入码。')
            value = request.get('identifier', '')
            if not isinstance(value, str) or not re.fullmatch(
                    r'(?:[A-Za-z0-9_-]{3,32}|[0-9a-f]{32}\.[0-9a-f]{32})', value.strip()):
                raise ValueError('Invalid account identifier')
            value = value.strip()
            matches = await relay.federation.lookup_accounts(value)
            result = {'ok': True, 'result': {'accounts': matches}}
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            result = {'ok': False, 'error': str(exc)[:160]}
        try:
            self.channel.write(json.dumps(result, ensure_ascii=True).encode() + b'\n')
        except (OSError, asyncssh.Error):
            pass

    def dispatch(self, request):
        peer, relay = self.peer, self.peer.relay
        operation = request.get('op')
        room = relay.rooms.get(peer.room)
        if operation == 'online_players' and peer.role == 'federation' and relay.diagnostics:
            offset = request.get('offset', 0)
            if type(offset) is not int or not 0 <= offset < 1000:
                raise ValueError('Invalid presence offset')
            players = relay.diagnostics.online_players()
            return {'players': players[offset:offset + 48], 'more': len(players) > offset + 48}
        if peer.role == 'federation' and operation in ('feedback_index', 'feedback_event') and getattr(relay.federation, 'feedback', None):
            return relay.federation.feedback.rpc(request)
        if operation == 'diagnostics' and peer.role in ('host', 'client') and relay.diagnostics:
            return relay.diagnostics.report(peer, request.get('report'))
        if peer.role == 'federation' and relay.federation and operation == 'snapshot':
            return relay.federation.snapshot(getattr(peer, 'node_id', None), request.get('social_version'),
                request.get('presence_version'), request.get('record_versions'), request.get('snapshot_versions'),
                request.get('record_cursor', ''))
        if peer.role == 'federation' and relay.federation and relay.federation.accounts:
            store = relay.federation.accounts
            if operation == 'username_reserve':
                account = request.get('account', '')
                if not isinstance(account, str) or account.split('.')[-1] != peer.node_id:
                    raise ValueError('Invalid registration origin')
                return store.replication.reserve_name(request.get('username'), account)
            if operation == 'account_record':
                record = store.replication.get(request.get('account'))
                if record:
                    return {'record': record}
                return {'record': None}
            if operation == 'account_update':
                record = request['record']
                account = store.validate_record(record)
                from .account_replication import header
                value = header(record)
                previous = store.replication.directory.get(account)
                if previous and store.replication.order(value) <= store.replication.order(previous):
                    raise ValueError('Account revision conflict')
                if relay.federation.id not in store.replication.targets(account, value):
                    raise ValueError('This node is not a backup target')
                store.replication.accept(value)
                store.ingest_one(record)
                store.save()
                return {'saved': True}
        if operation == 'introduce' and peer.role == 'directory' and relay.federation:
            relay.federation.ingest([request.get('record')])
            return {'accepted': True}
        if operation == 'ping':
            from .diagnostics import player_identity
            return {'version': 1, 'rooms': len(relay.rooms), 'max_streams': relay.max_streams,
                    'routing': __import__('tunnel.node_health',fromlist=['snapshot']).snapshot(relay.federation) if relay.federation else {},
                    'diagnostic_player': player_identity(peer) if peer.role in ('host', 'client') else None,
                    'name': relay.federation.name if relay.federation else '社区节点',
                    'capabilities': ['room-invite-policy-v1', 'room-list-page-v1', 'account-lookup-v1',
                                     'webrtc-peer-v1', 'room-lobby-v1'] + (['relay-zstd-v1'] if hasattr(relay, 'compression_capability') and relay.compression_capability() else [])}
        if operation == 'state' and peer.role == 'directory':
            key = request.get('id')
            if not isinstance(key, str) or len(key) != 32 or any(c not in '0123456789abcdef' for c in key):
                raise ValueError('Invalid room ID')
            if key in relay.closed_rooms:
                return {'availability': 'closed'}
            target = relay.rooms.get(key)
            if not target and relay.federation:
                return {'availability': 'ready' if relay.federation.locate(key) else 'missing'}
            return {'availability': ('ready' if target.available and target.port else 'waiting')
                    if target else 'missing'}
        if operation == 'list' and peer.role == 'directory':
            offset = request.get('offset', 0)
            if type(offset) is not int or not 0 <= offset <= 4096:
                raise ValueError('Invalid page offset')
            limit = request.get('limit', 50)
            query = request.get('query', '')
            if type(limit) is not int or not 1 <= limit <= 50:
                raise ValueError('Invalid page limit')
            if not isinstance(query, str) or len(query) > 100:
                raise ValueError('Invalid room search')
            query = query.strip().casefold()
            rooms = [(key, value) for key, value in relay.rooms.items() if value.public]
            listings = [describe(key, value, preview=False, relay=relay) for key, value in rooms]
            if relay.federation:
                listings.extend(relay.federation.public_rooms())
            listings = [listing for listing in listings if query in ' '.join([
                listing['title'], listing['game'].get('version', ''),
                listing['game'].get('loader', '')]).casefold()]
            listings.sort(key=lambda listing: listing['id'])
            page, size = [], 0
            for listing in listings[offset:offset + limit]:
                length = len(json.dumps(listing, ensure_ascii=True).encode())
                if page and size + length > 48000:
                    break
                page.append(listing)
                size += length
            end = offset + len(page)
            return {'rooms': page, 'next': end if end < len(listings) else None,
                    'total': len(listings)}
        if operation == 'topology' and peer.role == 'directory':
            offset, limit = request.get('offset', 0), request.get('limit', 32)
            if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 32:
                raise ValueError('Invalid topology pagination')
            records = sorted(relay.federation.live().items()) if relay.federation else []
            records = [(node, record) for node, record in records if node != relay.federation.id]
            nodes, routes, size = [], [], 0
            for _, record in records[offset:offset + limit]:
                profile = relay.federation.player_profile(record['body']['profile'],
                    getattr(peer, 'account', None), getattr(peer, 'credential', None))
                route = dict(profile=profile, name=record['body']['name'], routing=record['body'].get('routing', {}))
                length = len(json.dumps([profile, route], ensure_ascii=True).encode())
                if size + length > 48000:
                    break
                nodes.append(profile)
                routes.append(route)
                size += length
            end = offset + len(nodes)
            return {'nodes': nodes, 'routes': routes,
                    'next': end if end < len(records) else None, 'total': len(records)}
        if operation == 'account_lookup' and peer.role == 'federation':
            if not relay.federation or not relay.federation.accounts:
                raise ValueError('Account directory unavailable')
            value = request.get('identifier', '')
            if not isinstance(value, str) or not re.fullmatch(
                    r'(?:[A-Za-z0-9_-]{3,32}|[0-9a-f]{32}\.[0-9a-f]{32})', value.strip()):
                raise ValueError('Invalid account identifier')
            value = value.strip()
            store = relay.federation.accounts
            accounts = dict(store.replication.directory)
            matches = []
            for account, header in accounts.items():
                certificate = header.get('certificate', {})
                username = certificate.get('username', '')
                if relay.account_blocked(account):
                    continue
                if certificate.get('home') in relay.federation.blocked:
                    continue
                from .friend_codes import matches_account
                if isinstance(username, str) and matches_account(value, account, username):
                    matches.append(header)
            return {'entries': matches[:8]}
        if operation == 'locate' and peer.role == 'directory':
            key = request.get('id')
            if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{32}', key):
                raise ValueError('Invalid room ID')
            if key in relay.rooms and relay.federation:
                target = relay.federation.profile
            else:
                target = relay.federation.locate(key) if relay.federation else None
            return {'profile': relay.federation.player_profile(target, getattr(peer, 'account', None), getattr(peer, 'credential', None))
                    if relay.federation else None}
        if operation == 'resolve' and peer.role == 'directory':
            key = request.get('id')
            if not isinstance(key, str):
                raise ValueError('Invalid room ID')
            public_room = relay.rooms.get(key)
            if not public_room and relay.federation:
                remote = relay.federation.public_room(key)
                if remote:
                    return {'room': remote, 'profile': relay.federation.player_profile(
                        relay.federation.locate(key), getattr(peer, 'account', None), getattr(peer, 'credential', None))}
            if not public_room or not public_room.public:
                raise ValueError('Room unavailable')
            if public_room.require_invite:
                return {'room': describe(key, public_room, relay=relay)}
            return {'secret': public_room.secret, 'room': describe(key, public_room, relay=relay)}
        if operation == 'describe' and peer.role in ('client', 'host') and peer.room in relay.closed_rooms:
            return {'availability': 'closed', 'reason': '房主已结束联机，房间已关闭。'}
        if operation == 'describe' and room and peer.role in ('client', 'host'):
            return describe(peer.room, room, relay=relay)
        if room and room.lobby and peer.role in ('client', 'host') and operation in (
                'members', 'member_update', 'member_path', 'member_kick', 'member_unblock',
                'owner_offer', 'owner_cancel', 'owner_answer', 'logs_request', 'logs_answer',
                'logs_revoke', 'logs_append', 'logs_read'):
            return room.lobby.dispatch(peer, request)
        if operation == 'availability' and room and room.owner is peer:
            available = request.get('available')
            if type(available) is not bool:
                raise ValueError('Invalid game availability')
            if available != room.available:
                room.available = available
                relay.room_event(peer.room, room)
            return describe(peer.room, room, relay=relay)
        if operation == 'presence' and room and room.owner is peer:
            players, maximum = request.get('players'), request.get('maximum')
            if players is not None and (type(players) is not int or not 0 <= players <= 100000):
                raise ValueError('Invalid game player count')
            if maximum is not None and (type(maximum) is not int or not 0 <= maximum <= 100000):
                raise ValueError('Invalid game player maximum')
            room.game_players, room.game_max_players = players, maximum
            room.game_reported = time.monotonic()
            return describe(peer.room, room, relay=relay)
        manager = bool(room and (room.lobby.manager == peer.member_id if room.lobby else room.owner is peer))
        if operation == 'leave' and room and peer.role in ('host', 'client'):
            if manager:
                relay.end_room(peer.room)
            return {'availability': 'closed' if manager else 'left'}
        if operation == 'close' and room and manager:
            relay.end_room(peer.room)
            return {'availability': 'closed'}
        if operation == 'configure' and room and (manager or room.owner is peer):
            managerial = ('title', 'public', 'require_invite', 'capacity')
            if not manager and any(key in request and request[key] != getattr(room, key) for key in managerial):
                raise ValueError('Only the room manager may change room settings')
            if 'game' in request and room.owner is not peer:
                raise ValueError('Only the world provider may change the configuration')
            title = clean_text(request.get('title', room.title), 80, room.title)
            public = request.get('public', room.public)
            require_invite = request.get('require_invite', room.require_invite)
            capacity = request.get('capacity', room.capacity)
            if (type(public) is not bool or type(require_invite) is not bool or
                    type(capacity) is not int or not 1 <= capacity <= 1000):
                raise ValueError('Invalid room visibility or capacity')
            game = game_metadata(request['game']) if 'game' in request else room.game
            changed = room.game != game
            room.title, room.public, room.capacity, room.game = title, public, capacity, game
            room.require_invite = not public or require_invite
            if room.lobby:
                room.lobby.changed_config(game)
            if changed:
                relay.room_event(peer.room, room)
            return describe(peer.room, room, relay=relay)
        raise ValueError('Operation not allowed or room offline')
