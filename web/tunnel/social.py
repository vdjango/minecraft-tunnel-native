"""Signed bilateral relationships. Replicas never contain bearer credentials."""
import hashlib
import json
import re
import time
from .accounts import canonical, verify

ACCOUNT = re.compile(r'[0-9a-f]{32}\.[0-9a-f]{32}')
MAX_EDGES = 4096


class Social:
    def __init__(self, store):
        self.store = store
        self.path = store.path.with_name('friends-signed.json')
        self.edges, self.invitations, self.presence = {}, {}, {}
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
        if (not identity or identity['certificate']['home'] in fed.blocked or grant.get('i') in fed.blocked or
                grant.get('n') != fed.proof('account-audience')[:24] or grant.get('p') != 'buggcraft-account-login-v2' or
                type(grant.get('e')) is not int or (fresh and grant['e'] <= now)):
            raise ValueError('个人接入码已失效，请重新登录领取。')
        verify(identity['certificate']['public_key'], canonical(grant), event['proof'])
        verify(grant.get('d'), canonical(action), event['signature'])
        extra = {'request': set(), 'accept': {'ref'}, 'remove': set(), 'invite': {'room'}, 'join': {'ref'}, 'presence': set()}
        op, at = action.get('op'), action.get('at')
        if (op not in extra or set(action) != {'op', 'target', 'at', 'id'} | extra[op] or
                not isinstance(action['target'], str) or not ACCOUNT.fullmatch(action['target']) or
                not isinstance(action['id'], str) or not re.fullmatch('[0-9a-f]{32}', action['id']) or
                type(at) not in (int, float) or not grant['e'] - 7 * 86400 - 60 <= at <= min(now + 5, grant['e']) or
                (fresh and abs(now - at) > 30)):
            raise ValueError('Invalid friend action')
        if op == 'presence':
            if action['target'] != actor:
                raise ValueError('Invalid presence identity')
        elif action['target'] == actor or action['target'] not in self.store.replication.directory:
            raise ValueError('找不到这个用户名。')
        for key in extra[op]:
            if not isinstance(action[key], str) or not re.fullmatch('[0-9a-f]{32}', action[key]):
                raise ValueError('Invalid friend reference')
        return actor, action

    @staticmethod
    def order(event):
        return event['action']['at'], event['action']['id']

    def prune(self):
        now = time.time()
        for collection, lifetime, field in ((self.presence, 20, 'presence_version'),
                                             (self.invitations, 300, 'version')):
            expired = [key for key, event in collection.items()
                       if event['action']['at'] <= now - lifetime or event['grant']['e'] <= now]
            for key in expired:
                del collection[key]
            if expired:
                setattr(self, field, '')

    def ingest(self, entries, persist=True):
        if not isinstance(entries, list) or len(entries) > MAX_EDGES + 768:
            raise ValueError('Too many friend events')
        self.prune()
        changed = False
        for event in entries:
            try:
                action, actor = event['action'], event['grant']['a']
                op = action['op']
                collection = self.presence if op == 'presence' else self.invitations if op == 'invite' else self.edges
                key = actor if op == 'presence' else (actor, action['target'])
                old = collection.get(key)
                if old == event or (old and self.order(event) <= self.order(old)):
                    continue
                self.validate(event)
                if op == 'join' or (op == 'presence' and action['at'] < time.time() - 20) or (op == 'invite' and action['at'] < time.time() - 300):
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
        values = list(self.presence.values()) if presence else list(self.edges.values()) + list(self.invitations.values())
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

    def state(self, account):
        self.prune()
        friends, requests, invitations = [], [], []
        others = {b if a == account else a for a, b in self.edges if account in (a, b)}
        for other in sorted(others):
            item = {'account': other, 'username': self.name(other)}
            if self.friends(account, other):
                beat = self.presence.get(other)
                friends.append(dict(item, online=bool(beat and beat['action']['at'] > time.time() - 20 and beat['grant']['e'] > time.time())))
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
        return {'account': account, 'username': self.name(account), 'friends': friends, 'requests': requests, 'invitations': invitations}

    def apply(self, account, event):
        actor, action = self.validate(event, fresh=True)
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
        elif op in ('invite', 'join') and not self.friends(actor, target):
            raise ValueError('请先成为双向好友。')
        if op == 'join':
            invitation = self.invitations.get((target, actor))
            if not invitation or invitation['action']['id'] != action['ref'] or invitation['action']['at'] <= time.time() - 300:
                raise ValueError('邀请已过期，请好友重新邀请。')
            room = self.store.federation.relay.rooms.get(invitation['action']['room'])
            if not room or getattr(room.owner, 'account', None) != target or not room.available:
                raise ValueError('好友的房间暂不可用。')
            return {'invite': 'bugg1-' + room.secret}
        self.ingest([event])
        return self.state(actor)
