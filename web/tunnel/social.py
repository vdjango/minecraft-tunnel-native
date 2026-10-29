"""Signed bilateral relationships. Replicas never contain bearer credentials."""
import hashlib
import json
import re
import time
from .accounts import canonical, verify
from .room_lobby import manager_account

ACCOUNT = re.compile(r'[0-9a-f]{32}\.[0-9a-f]{32}')
MAX_EDGES = 4096


class Social:
    def __init__(self, store):
        self.store = store
        self.path = store.path.with_name('friends-signed.json')
        self.edges, self.invitations, self.presence, self.join_requests = {}, {}, {}, {}
        self.version = self.presence_version = ''
        if self.path.exists():
            self.ingest(json.loads(self.path.read_text(encoding='utf-8')), persist=False)

    def validate(self, event, fresh=False):
        if not isinstance(event, dict) or set(event) != {'grant', 'proof', 'action', 'signature'} or len(canonical(event)) > 4096:
            raise ValueError('Invalid signed friend action')
        grant, action = event['grant'], event['action']
        if not isinstance(grant, dict) or not isinstance(action, dict):
            raise ValueError('Invalid friend grant')
        actor, now = grant.get('a'), time.time()
        fed = self.store.federation
        identity = self.store.replication.directory.get(actor)
        device = grant.get('p') == 'buggcraft-launcher-device-v1'
        if (not identity or identity['certificate']['home'] in fed.blocked or grant.get('i') in fed.blocked or
                grant.get('n') != fed.proof('account-audience')[:24] or
                grant.get('p') not in ('buggcraft-account-login-v2', 'buggcraft-launcher-device-v1') or
                type(grant.get('e')) is not int or (fresh and grant['e'] <= now)):
            raise ValueError('个人接入码已失效，请重新登录领取。')
        if device and (type(grant.get('b')) is not int or not 0 < grant['e'] - grant['b'] <= 180 * 86400 or
                       (fresh and not self.store.launcher_devices.allowed(grant))):
            raise ValueError('设备授权已失效，请重新登录。')
        verify(identity['certificate']['public_key'], canonical(grant), event['proof'])
        verify(grant.get('d'), canonical(action), event['signature'])
        extra = {'request': set(), 'accept': {'ref'}, 'remove': set(), 'invite': {'room'}, 'join': {'ref'},
                 'request_join': {'room'}, 'accept_join': {'ref', 'room'}, 'decline_join': {'ref', 'room'},
                 'presence': set()}
        op, at = action.get('op'), action.get('at')
        room_presence = {'room', 'room_role', 'room_title', 'room_state', 'network_profile'}
        if op == 'presence' and set(action) & room_presence:
            extra['presence'] = set(action) & room_presence
        if (op not in extra or set(action) != {'op', 'target', 'at', 'id'} | extra[op] or
                not isinstance(action['target'], str) or not ACCOUNT.fullmatch(action['target']) or
                not isinstance(action['id'], str) or not re.fullmatch('[0-9a-f]{32}', action['id']) or
                type(at) not in (int, float) or not (grant['b'] - 60 if device else grant['e'] - 7 * 86400 - 60) <= at <= min(now + 5, grant['e']) or
                (fresh and abs(now - at) > 30)):
            raise ValueError('Invalid friend action')
        if op == 'presence':
            if action['target'] != actor:
                raise ValueError('Invalid presence identity')
        elif action['target'] == actor or action['target'] not in self.store.replication.directory:
            raise ValueError('找不到这个用户名。')
        required_fields = extra[op] - {'room_role', 'room_title', 'room_state', 'network_profile'}
        if op == 'presence':
            required_fields.discard('room')
        for key in required_fields:
            if not isinstance(action[key], str) or not re.fullmatch('[0-9a-f]{32}', action[key]):
                raise ValueError('Invalid friend reference')
        if op == 'presence' and extra['presence']:
            room_fields = {'room', 'room_role', 'room_title', 'room_state'}
            if room_fields & set(action) and (
                    not room_fields <= set(action) or action['room_role'] not in ('host', 'client') or
                    action['room_state'] not in ('ready', 'waiting') or
                    not isinstance(action['room_title'], str) or not 1 <= len(action['room_title']) <= 80):
                raise ValueError('Invalid room presence')
            network = action.get('network_profile')
            if network is not None and (
                    not isinstance(network, dict) or set(network) != {
                        'type', 'direct_successes', 'relay_successes'} or
                    network.get('type') not in ('direct_easier', 'relay_likely', 'unknown') or
                    any(type(network.get(key)) is not int or not 0 <= network[key] <= 100000
                        for key in ('direct_successes', 'relay_successes'))):
                raise ValueError('Invalid network profile')
        return actor, action

    @staticmethod
    def order(event):
        return event['action']['at'], event['action']['id']

    def prune(self):
        now = time.time()
        for collection, lifetime, field in ((self.presence, 20, 'presence_version'),
                                             (self.invitations, 300, 'version'),
                                             (self.join_requests, 120, 'version')):
            expired = [key for key, event in collection.items()
                       if event['action']['at'] <= now - lifetime or event['grant']['e'] <= now]
            for key in expired:
                del collection[key]
            if expired:
                setattr(self, field, '')

    def ingest(self, entries, persist=True):
        if not isinstance(entries, list) or len(entries) > MAX_EDGES + 1024:
            raise ValueError('Too many friend events')
        self.prune()
        changed = False
        for event in entries:
            try:
                action, actor = event['action'], event['grant']['a']
                op = action['op']
                collection = (self.presence if op == 'presence' else self.invitations if op in ('invite', 'accept_join')
                              else self.join_requests if op in ('request_join', 'decline_join') else self.edges)
                key = actor if op == 'presence' else (action['target'], actor) if op == 'decline_join' else (actor, action['target'])
                old = collection.get(key)
                if old == event or (old and self.order(event) <= self.order(old)):
                    continue
                self.validate(event)
                if (op == 'join' or (op == 'presence' and action['at'] < time.time() - 20) or
                        (op in ('invite', 'accept_join') and action['at'] < time.time() - 300) or
                        (op in ('request_join', 'decline_join') and action['at'] < time.time() - 120)):
                    continue
                limit = MAX_EDGES if collection is self.edges else 512 if collection is self.presence else 256
                if key not in collection and len(collection) >= limit:
                    continue
                collection[key] = event
                if collection is self.presence:
                    self.presence_version = ''
                else:
                    self.version = ''
                changed = changed or collection is self.edges
            except (KeyError, TypeError, ValueError):
                continue
        if changed and persist:
            from .portal import atomic_json
            atomic_json(self.path, list(self.edges.values()))

    def snapshot(self, presence=False):
        self.prune()
        values = (list(self.presence.values()) if presence else
                  list(self.edges.values()) + list(self.invitations.values()) + list(self.join_requests.values()))
        return sorted(values, key=lambda e: (e['grant']['a'], e['action']['target'], e['action']['op']))

    def digest(self, presence=False):
        self.prune()
        field = 'presence_version' if presence else 'version'
        if not getattr(self, field):
            setattr(self, field, hashlib.sha256(canonical(self.snapshot(presence))).hexdigest())
        return getattr(self, field)

    def friends(self, account, other):
        first, second = self.edges.get((account, other)), self.edges.get((other, account))
        if not first or not second:
            return False
        a, b = first['action'], second['action']
        return ((a['op'] == 'accept' and b['op'] == 'request' and a['ref'] == b['id'] and a['at'] >= b['at']) or
                (b['op'] == 'accept' and a['op'] == 'request' and b['ref'] == a['id'] and b['at'] >= a['at']))

    def name(self, account):
        entry = self.store.replication.directory.get(account)
        return entry['certificate']['username'] if entry else account[:8]

    @staticmethod
    def invited_after(request, invitation):
        if not invitation:
            return False
        action, invite = request['action'], invitation['action']
        return (invite.get('ref') == action['id'] or
                invite.get('room') == action['room'] and invite['at'] >= action['at'])

    def state(self, account):
        self.prune()
        friends, requests, invitations, join_requests = [], [], [], []
        others = {b if a == account else a for a, b in self.edges if account in (a, b)}
        for other in sorted(others):
            item = {'account': other, 'username': self.name(other)}
            if self.friends(account, other):
                beat = self.presence.get(other)
                online = bool(beat and beat['action']['at'] > time.time() - 20 and beat['grant']['e'] > time.time())
                room = None
                network_profile = None
                if online and beat['action'].get('room'):
                    action = beat['action']
                    room = dict(id=action['room'], title=action['room_title'], role=action['room_role'],
                                availability=action['room_state'], can_request=action['room_role'] == 'host' and action['room_state'] == 'ready')
                if online:
                    network_profile = beat['action'].get('network_profile')
                friends.append(dict(item, online=online, room=room, network_profile=network_profile))
            else:
                outgoing, incoming = self.edges.get((account, other)), self.edges.get((other, account))
                for event, inbound, opposite in ((incoming, True, outgoing), (outgoing, False, incoming)):
                    if not event or event['action']['op'] != 'request':
                        continue
                    if opposite and opposite['action']['op'] == 'remove' and self.order(opposite) >= self.order(event):
                        continue
                    requests.append(dict(item, id=event['action']['id'], incoming=inbound))
        for (actor, target), event in self.invitations.items():
            if target == account and self.friends(actor, target) and event['action']['at'] > time.time() - 300:
                invitations.append({'id': event['action']['id'], 'account': actor, 'username': self.name(actor), 'room': event['action']['room']})
        for (requester, host), event in self.join_requests.items():
            if account not in (requester, host) or not self.friends(requester, host):
                continue
            action = event['action']
            accepted = self.invitations.get((host, requester))
            if self.invited_after(event, accepted):
                continue
            if action['op'] == 'decline_join':
                if account == requester:
                    join_requests.append(dict(id=action['ref'], account=host, username=self.name(host),
                                              room=action['room'], incoming=False, status='declined'))
                continue
            other = requester if account == host else host
            join_requests.append(dict(id=action['id'], account=other, username=self.name(other),
                                      room=action['room'], incoming=account == host, status='pending'))
        own_beat = self.presence.get(account)
        own_profile = own_beat['action'].get('network_profile') if own_beat and own_beat['action']['at'] > time.time() - 20 else None
        return {'account': account, 'username': self.name(account), 'friends': friends, 'requests': requests,
                'networkProfile': own_profile,
                'invitations': invitations, 'joinRequests': join_requests,
                'capabilities': {'room_presence': True, 'join_requests': True, 'friend_code': True}}

    def apply(self, account, event):
        actor, action = self.validate(event, fresh=True)
        self.prune()
        if actor != account:
            raise ValueError('Friend identity does not match authenticated account')
        target, op = action['target'], action['op']
        if op == 'accept':
            request = self.edges.get((target, actor))
            current = self.edges.get((actor, target))
            if (not request or request['action']['op'] != 'request' or request['action']['id'] != action['ref'] or
                    (current and current['action']['op'] == 'remove' and self.order(current) >= self.order(request))):
                raise ValueError('好友申请已变更，请刷新列表。')
        elif op == 'request':
            if self.friends(actor, target):
                raise ValueError('你们已经是好友。')
            own, other = self.edges.get((actor, target)), self.edges.get((target, actor))
            if own and own['action']['op'] == 'request' and (not other or other['action']['op'] != 'remove' or self.order(other) < self.order(own)):
                raise ValueError('已发送申请，请等待对方同意。')
            if not own and (sum(a == actor for a, b in self.edges) >= 64 or len(self.edges) >= MAX_EDGES):
                raise ValueError('好友列表已达到容量上限。')
        elif op in ('invite', 'join', 'request_join', 'accept_join', 'decline_join') and not self.friends(actor, target):
            raise ValueError('请先成为双向好友。')
        if op in ('request_join', 'accept_join', 'decline_join'):
            room = self.store.federation.relay.rooms.get(action['room'])
            owner = target if op == 'request_join' else actor
            if not room or manager_account(room) != owner or not room.port:
                raise ValueError('好友的房间暂不可用，请等待房主开放世界。')
            key = (actor, target) if op == 'request_join' else (target, actor)
            pending = self.join_requests.get(key)
            if op == 'request_join':
                if pending and pending['action']['op'] == 'request_join' and pending['action']['at'] > time.time() - 120:
                    accepted = self.invitations.get((target, actor))
                    if not self.invited_after(pending, accepted):
                        raise ValueError('已申请加入，请等待房主回应。')
            elif (not pending or pending['action']['op'] != 'request_join' or
                  pending['action']['id'] != action['ref'] or pending['action']['room'] != action['room'] or
                  pending['action']['at'] <= time.time() - 120):
                raise ValueError('加入申请已过期或已处理，请刷新列表。')
            elif self.invited_after(pending, self.invitations.get((actor, target))):
                raise ValueError('加入申请已过期或已处理，请刷新列表。')
        if op == 'join':
            invitation = self.invitations.get((target, actor))
            if not invitation or invitation['action']['id'] != action['ref'] or invitation['action']['at'] <= time.time() - 300:
                raise ValueError('邀请已过期，请好友重新邀请。')
            room = self.store.federation.relay.rooms.get(invitation['action']['room'])
            if not room or manager_account(room) != target or not room.available:
                raise ValueError('好友的房间暂不可用。')
            return {'invite': 'bugg1-' + room.secret}
        self.ingest([event])
        return self.state(actor)
