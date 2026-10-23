"""User-signed placement policy and a public directory without encrypted vaults."""
import hashlib
import re
import time

import asyncssh

from .accounts import MAX_ACCOUNTS, canonical, decode, public_key, verify

MAX_COPIES = 8  # Additional durable copies; the home is an extra copy.


def certificate_valid(cert, signature):
    from .federation import identity
    if (not isinstance(cert, dict) or set(cert) !=
            {'uid', 'home', 'home_key', 'username', 'public_key', 'created'} or
            not isinstance(cert['uid'], str) or not re.fullmatch('[0-9a-f]{32}', cert['uid']) or
            identity(cert['home_key']) != cert['home'] or
            not isinstance(cert['username'], str) or not re.fullmatch('[A-Za-z0-9_-]{3,32}', cert['username'])):
        raise ValueError('Invalid identity certificate')
    public_key(cert['public_key'])
    if not asyncssh.import_public_key(cert['home_key']).verify(canonical(cert), decode(signature)):
        raise ValueError('Invalid identity issuer')


def header(record):
    return {k: record[k] for k in ('certificate', 'issuer_signature', 'routing', 'routing_signature')}


def validate_header(value):
    if not isinstance(value, dict) or set(value) != {'certificate', 'issuer_signature', 'routing', 'routing_signature'}:
        raise ValueError('Invalid account route')
    if len(canonical(value)) > 4096:
        raise ValueError('Account route too large')
    cert, route = value['certificate'], value['routing']
    certificate_valid(cert, value['issuer_signature'])
    if (not isinstance(route, dict) or set(route) != {'account', 'revision', 'copies', 'digest'} or
            route['account'] != cert['uid'] + '.' + cert['home'] or
            type(route['revision']) is not int or not 1 <= route['revision'] <= 2147483647 or
            type(route['copies']) is not int or not 0 <= route['copies'] <= MAX_COPIES or
            not isinstance(route['digest'], str) or not re.fullmatch('[0-9a-f]{64}', route['digest'])):
        raise ValueError('Invalid signed backup policy')
    verify(cert['public_key'], canonical(route), value['routing_signature'])


class Replication:
    def __init__(self, store):
        self.store = store
        self.path = store.path.with_name('accounts-directory.json')
        self.directory = {}
        self.temporary = {}
        self.name_reservations = {}
        import json
        self.names_path = store.path.with_name('username-reservations.json')
        if self.names_path.exists():
            self.name_reservations = json.loads(self.names_path.read_text(encoding='utf-8'))
        import json
        self.shared_path = store.path.with_name('accounts-shared.json')
        shared = json.loads(self.shared_path.read_text(encoding='utf-8')) if self.shared_path.exists() else []
        if not isinstance(shared, list) or len(shared) > MAX_ACCOUNTS or any(not isinstance(a, str) for a in shared):
            raise ValueError('Invalid published account list')
        self.shared = set(shared)
        if self.path.exists():
            import json
            values = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(values, list) or len(values) > MAX_ACCOUNTS:
                raise ValueError('Invalid account directory')
            for value in values:
                self.accept(value)
        for value in store.records.values():
            if 'routing' in value:
                self.accept(header(value))

    @staticmethod
    def order(value):
        route = value['routing']
        return route['revision'], route['digest']

    def accept(self, value):
        validate_header(value)
        account = value['routing']['account']
        previous = self.directory.get(account)
        if previous:
            if previous['certificate'] != value['certificate']:
                raise ValueError('Identity certificate changed')
            if self.order(value) <= self.order(previous):
                return False
        elif len(self.directory) >= MAX_ACCOUNTS:
            raise ValueError('Account directory full')
        self.directory[account] = value
        self.shared.add(account)
        return True

    def save(self):
        from .portal import atomic_json
        atomic_json(self.path, list(self.directory.values()))
        atomic_json(self.shared_path, sorted(self.shared))

    def ingest(self, entries):
        if not isinstance(entries, list) or len(entries) > MAX_ACCOUNTS:
            raise ValueError('Too many account routes')
        changed = False
        import hmac
        fed = self.store.federation
        for entry in entries:
            try:
                value = entry['header']
                home = value['certificate']['home']
                if home in fed.blocked or not hmac.compare_digest(entry['proof'], fed.proof(home)):
                    continue
                changed = self.accept(value) or changed
                self.shared.add(value['routing']['account'])
            except (ValueError, TypeError, KeyError, asyncssh.Error):
                continue
        if changed:
            self.save()

    def snapshot(self):
        fed = self.store.federation
        # Public names/keys are network-wide, regardless of vault placement.
        return [{'header': h, 'proof': fed.proof(h['certificate']['home'])}
                for h in self.directory.values() if h['certificate']['home'] not in fed.blocked]

    def reserve_name(self, username, account):
        if (not isinstance(username, str) or not re.fullmatch('[A-Za-z0-9_-]{3,32}', username) or
                not isinstance(account, str) or not re.fullmatch(r'[0-9a-f]{32}\.[0-9a-f]{32}', account)):
            raise ValueError('用户名只能使用 3–32 位英文字母、数字、下划线或短横线。')
        name = username.casefold()
        if any(h['certificate']['username'].casefold() == name and a != account for a, h in self.directory.items()):
            raise ValueError('用户名已被使用，请更换。')
        now = time.time()
        self.name_reservations = {k: v for k, v in self.name_reservations.items() if v['until'] > now}
        old = self.name_reservations.get(name)
        if old and old['account'] != account:
            raise ValueError('用户名正在注册，请稍后重试或更换。')
        if not old and len(self.name_reservations) >= 1024:
            raise ValueError('注册请求过多，请稍后重试。')
        self.name_reservations[name] = {'account': account, 'until': now + 120}
        from .portal import atomic_json
        atomic_json(self.names_path, self.name_reservations)
        return {'reserved': True}

    def targets(self, account, value=None):
        value = value or self.directory.get(account)
        if not value:
            return set()  # Legacy records retain their previous replication behavior.
        home = value['certificate']['home']
        copies = value['routing']['copies']
        if not copies:
            return {home}
        fed = self.store.federation
        live = set(fed.live()) | {fed.id}
        ordered = sorted(live - {home}, key=lambda n: hashlib.sha256((account + n).encode()).digest(), reverse=True)
        # If the home fails, replace its copy too. Re-evaluated every gossip cycle.
        return ({home} if home in live else set()) | set(ordered[:copies if home in live else copies + 1])

    def current(self, record):
        value = self.directory.get(record['state']['account'])
        return not value or ('routing' in record and self.order(record) == self.order(value))

    def inventory(self):
        return [{'account': a, 'digest': r['routing']['digest']} for a, r in self.store.records.items()
                if 'routing' in r and r['routing']['copies'] > 0 and self.current(r)]

    def reconcile(self):
        fed = self.store.federation
        changed = False
        for account, record in list(self.store.records.items()):
            value = self.directory.get(account)
            if not value or record['certificate']['home'] == fed.id:
                continue
            targets = self.targets(account)
            if fed.id in targets:
                continue
            digest = value['routing']['digest']
            confirmed = {node for node, member in fed.live().items()
                         if any(x == {'account': account, 'digest': digest}
                                for x in member['body'].get('account_copies', []))}
            # Keep a handoff copy until every currently selected target confirms.
            if value['routing']['copies'] == 0 or targets <= confirmed:
                self.store.records.pop(account)
                changed = True
        if changed:
            self.store.save()

    def get(self, account):
        record = self.store.records.get(account)
        if record and self.current(record):
            return record
        entry = self.temporary.get(account)
        if entry and entry[0] > time.monotonic() and self.current(entry[1]):
            return entry[1]
        return None

    async def resolve(self, identifier):
        if not isinstance(identifier, str) or len(identifier) > 65:
            raise ValueError('Invalid account identifier')
        matches = {a for a, r in self.store.records.items()
                   if a == identifier or r['certificate']['username'].casefold() == identifier.casefold()}
        matches.update(a for a, h in self.directory.items() if
                       (a == identifier or h['certificate']['username'].casefold() == identifier.casefold()))
        if len(matches) != 1:
            raise ValueError('账号不存在、尚未同步，或用户名重名；请使用完整账号 ID。')
        account = matches.pop()
        if self.get(account):
            return self.get(account)
        value = self.directory.get(account)
        if not value:
            raise ValueError('账号目录尚未同步，请稍后重试。')
        record = await self.store.federation.fetch_account(account)
        if record:
            # Verify without storing a durable copy on this login-only node.
            self.store.validate_record(record)
            if not self.current(record):
                raise ValueError('账号副本尚未更新，请稍后重试。')
            self.temporary = {a: v for a, v in self.temporary.items() if v[0] > time.monotonic()}
            if len(self.temporary) >= 256:
                self.temporary.pop(next(iter(self.temporary)))
            self.temporary[account] = (time.monotonic() + 3600, record)
            return record
        raise ValueError('账号的注册节点和备份节点暂时均不可达。')

    def status(self, account):
        value = self.directory.get(account)
        if not value:
            return {'copies': 'legacy', 'available': None, 'target': None, 'nodes': []}
        digest = value['routing']['digest']
        fed = self.store.federation
        nodes = [{'id': node, 'name': r['body']['name']} for node, r in fed.live().items()
                 if any(x == {'account': account, 'digest': digest} for x in r['body'].get('account_copies', []))]
        if self.get(account) and account in self.store.records and not any(n['id'] == fed.id for n in nodes):
            nodes.append({'id': fed.id, 'name': fed.name})
        return {'copies': value['routing']['copies'], 'available': len(nodes),
                'target': value['routing']['copies'] + 1, 'nodes': nodes}
