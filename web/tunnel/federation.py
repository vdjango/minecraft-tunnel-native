"""Bounded, signed gossip between explicitly trusted community operators."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time

import asyncssh

from .directory import describe, game_metadata
from .federation_schedule import Schedule, EXCHANGE_TIMEOUT
from .federation_transport import Transport
from .federation_lookup import AccountLookup
from . import federation_wire as wire

MAX_NODES = 32
MAX_RECORD = 512 * 1024
MAX_SNAPSHOT = MAX_NODES * MAX_RECORD + 10 * 1024 * 1024
TTL = 30
LOG = logging.getLogger(__name__)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def identity(public_key):
    key = asyncssh.import_public_key(public_key).export_public_key().decode().strip()
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def validate_profile(value):
    if not isinstance(value, dict) or set(value) != {'server_host', 'server_port', 'token', 'server_public_key'}:
        raise ValueError('Invalid node profile')
    host, port, token = value['server_host'], value['server_port'], value['token']
    if (not isinstance(host, str) or not host or len(host) > 253 or
            any(c.isspace() or c in '/\\@?#' for c in host) or
            type(port) is not int or not 1 <= port <= 65535 or
            not isinstance(token, str) or not 32 <= len(token) <= 2048):
        raise ValueError('Invalid node address or credential')
    asyncssh.import_public_key(value['server_public_key'])
    return dict(value)


class Federation:
    """Operators share admission authority, but each origin signs its own state.

    No replicated game state and no leader: room ownership stays at the origin.
    The network key is never sent to launcher clients or the public website.
    """
    def __init__(self, key, profile, *, network_key, name='Community node', blocked=()):
        self.key = key
        self.profile = validate_profile(profile)
        self.id = identity(profile['server_public_key'])
        self.network_key = network_key
        if not isinstance(network_key, str) or len(network_key) < 32:
            raise ValueError('Invalid federation network key')
        self.name = name[:80]
        self.blocked = set(blocked)
        self.records = {}
        self.seen = {}
        self.seeds = {}
        self.transport = Transport(key, self.id)
        self.connections = self.transport.connections
        self.schedule = Schedule()
        self.inflight = {}
        self.sync_lock = asyncio.Lock()
        self.snapshot_versions = {}
        self.health_states = {}
        self.stats = dict(exchanges_started=0, exchanges_failed=0, snapshot_received_bytes=0)
        self.task = None
        self.relay = None
        self.last_success = {}
        self.latencies = {}
        self.region = ''
        self.routing_sample = None
        self.routing_sample_at = 0
        self.last_failure = {}
        self.accounts = None
        self.account_lookup = AccountLookup(self)
        self.on_change = lambda: None

    def proof(self, node_id):
        return hmac.new(self.network_key.encode(), node_id.encode(), hashlib.sha256).hexdigest()

    def accepts(self, node_id, key):
        # Admission requires a previously verified signed membership record.
        record = self.records.get(node_id)
        return bool(record and node_id not in self.blocked and
                    key.export_public_key() == asyncssh.import_public_key(
                        record['body']['profile']['server_public_key']).export_public_key())

    def publish(self):
        if not self.relay:
            return
        rooms = []
        for room_id, room in self.relay.rooms.items():
            item = {'id': room_id}
            if room.public and room.port:
                item['room'] = describe(room_id, room, preview=False, relay=self.relay)
            rooms.append(item)
        body = {'id': self.id, 'profile': self.profile, 'name': self.name,
                'issued': time.time(), 'rooms': rooms}
        # Quantize/cache capacity hints: never gossip a fresh byte counter on
        # every payload packet or create a second all-node probe loop.
        if self.routing_sample is None or time.monotonic()-self.routing_sample_at>=10:
            from .node_health import snapshot
            self.routing_sample=snapshot(self)
            self.routing_sample_at=time.monotonic()
        body['routing']=dict(self.routing_sample,region=self.region,
            accepting=not getattr(self.relay,'draining',False) and len(self.relay.rooms)<getattr(self.relay,'max_rooms',128))
        if self.accounts:
            body['account_copies'] = self.accounts.replication.inventory()
        previous = self.records.get(self.id)
        if previous:
            body['issued'] = max(body['issued'], previous['body']['issued'] + .000001)
        encoded = canonical(body)
        if len(encoded) > MAX_RECORD:
            raise ValueError('Federation record exceeds limit')
        if (previous and body['issued'] - previous['body']['issued'] < 2 and
                wire.state_digest(previous['body']) == wire.state_digest(body)):
            return
        self.records[self.id] = {'body': body, 'proof': self.proof(self.id),
                                'signature': base64.b64encode(self.key.sign(encoded, b'ssh-ed25519')).decode()}
        self.seen[self.id] = time.monotonic()

    def ingest(self, records):
        if not isinstance(records, list) or len(records) > MAX_NODES:
            raise ValueError('Too many federation members')
        for record in records:
            try:
                record = wire.expand(record, self.records)
                body = record['body']
                if not isinstance(body, dict) or len(canonical(body)) > MAX_RECORD:
                    continue
                profile = validate_profile(body['profile'])
                node_id = identity(profile['server_public_key'])
                if node_id != body['id'] or node_id == self.id or node_id in self.blocked:
                    continue
                if not hmac.compare_digest(record['proof'], self.proof(node_id)):
                    continue
                issued = body['issued']
                if type(issued) not in (int, float) or not time.time() - TTL <= issued <= time.time() + 5:
                    continue
                if not isinstance(body.get('name'), str) or not 1 <= len(body['name']) <= 80:
                    continue
                if 'routing' in body:
                    from .node_health import validate
                    validate(body['routing'])
                public = asyncssh.import_public_key(profile['server_public_key'])
                if not public.verify(canonical(body), base64.b64decode(record['signature'], validate=True)):
                    continue
                rooms = body['rooms']
                copies = body.get('account_copies', [])
                if (not isinstance(copies, list) or len(copies) > 512 or
                        any(not isinstance(c, dict) or set(c) != {'account', 'digest'} or
                            not isinstance(c['account'], str) or len(c['account']) != 65 or
                            not isinstance(c['digest'], str) or len(c['digest']) != 64 for c in copies)):
                    continue
                if not isinstance(rooms, list) or len(rooms) > 128:
                    continue
                if any(not isinstance(r, dict) or not isinstance(r.get('id'), str) or
                       len(r['id']) != 32 or any(c not in '0123456789abcdef' for c in r['id'])
                       for r in rooms):
                    continue
                for item in rooms:
                    if 'room' not in item:
                        continue
                    room = item['room']
                    if (not isinstance(room, dict) or len(canonical(room)) > 4096 or
                            room.get('id') != item['id'] or
                            not isinstance(room.get('title'), str) or len(room['title']) > 80 or
                            room.get('public') is not True or
                            type(room.get('require_invite', False)) is not bool or
                            type(room.get('capacity')) is not int or not 1 <= room['capacity'] <= 1000 or
                            type(room.get('connections')) is not int or not 0 <= room['connections'] <= 100000 or
                            room.get('availability') not in ('ready', 'waiting')):
                        raise ValueError('Invalid remote room')
                    game_metadata(room.get('game', {}))
                    for key in ('members', 'game_players'):
                        if room.get(key) is not None and (type(room[key]) is not int or not 0 <= room[key] <= 100000):
                            raise ValueError('Invalid remote player count')
                previous = self.records.get(node_id)
                if previous and previous['body']['issued'] >= issued:
                    continue  # Replaying a signed heartbeat must not extend its lease.
                if not previous and len(self.records) >= MAX_NODES:
                    continue
                self.records[node_id] = record
                self.seen[node_id] = time.monotonic()
                if self.seeds.get(node_id) != profile:
                    self.seeds[node_id] = profile
                    self.on_change()
            except (KeyError, ValueError, TypeError, asyncssh.Error):
                continue

    def live(self):
        now = time.monotonic()
        return {k: v for k, v in self.records.items() if k not in self.blocked and
                now - self.seen.get(k, 0) < TTL and v['body']['issued'] > time.time() - TTL}

    def snapshot(self, recipient=None, social_version=None, presence_version=None,
                 record_versions=None, snapshot_versions=None):
        self.publish()
        result = {'records': wire.compact(self.live(), record_versions),
                'accounts': self.accounts.snapshot(recipient) if self.accounts else [],
                'account_routes': self.accounts.replication.snapshot() if self.accounts else [],
                'social': self.accounts.social.snapshot() if self.accounts and social_version != self.accounts.social.digest() else [],
                'presence': self.accounts.social.snapshot(True) if self.accounts and presence_version != self.accounts.social.digest(True) else []}
        known = snapshot_versions if isinstance(snapshot_versions, dict) else {}
        result['snapshot_versions'] = {}
        for section in ('accounts', 'account_routes'):
            version = wire.digest(result[section])
            result['snapshot_versions'][section] = version
            if known.get(section) == version:
                result[section] = []
        return result

    def health(self):
        now = time.monotonic()
        live = self.live()
        result = []
        for node_id in sorted(set(self.seeds) | set(self.records)):
            record = self.records.get(node_id)
            age = now - self.seen[node_id] if node_id in self.seen else None
            direct = now - self.last_success[node_id] if node_id in self.last_success else None
            status = ('blocked' if node_id in self.blocked else 'online' if node_id == self.id or
                      (direct is not None and direct < TTL) else 'indirect' if node_id in live else
                      'suspect' if age is not None and age < TTL * 2 else 'offline')
            routing=record['body'].get('routing',{}) if record else {}
            result.append({'id': node_id, 'name': record['body']['name'] if record else '已配置节点',
                           'region':routing.get('region',''),
                           'latency_ms':0 if node_id==self.id else round(self.latencies[node_id],1) if direct is not None and direct<TTL and node_id in self.latencies else None,
                           'utilization':routing.get('utilization'),
                           'remaining_visitors':routing.get('remaining_visitors'),
                           'accepting':routing.get('accepting',True),
                           'local': node_id == self.id, 'status': status,
                           'heartbeat_age': 0 if node_id == self.id else round(age, 1) if age is not None else None,
                           'direct_age': round(direct, 1) if direct is not None else None})
        return result

    def locate(self, room_id):
        owners = [record['body']['profile'] for node_id, record in self.live().items()
                  if node_id != self.id and any(r['id'] == room_id for r in record['body']['rooms'])]
        return owners[0] if len(owners) == 1 else None

    def public_rooms(self):
        result = {}
        duplicates = set()
        for node_id, record in sorted(self.live().items()):
            if node_id == self.id:
                continue
            for item in record['body']['rooms']:
                if 'room' in item and isinstance(item['room'], dict):
                    if item['id'] in result:
                        duplicates.add(item['id'])
                    else:
                        result[item['id']] = item['room']
        return [room for key, room in result.items() if key not in duplicates]

    def public_room(self, room_id):
        return next((r for r in self.public_rooms() if r.get('id') == room_id), None)

    def topology(self):
        return [r['body']['profile'] for node_id, r in sorted(self.live().items()) if node_id != self.id]

    def player_profile(self, profile, account=None, credential=None):
        if not profile or not account:
            return profile
        result = dict(profile)
        result['token'] = credential or self.accounts.credential(account)
        return result

    def leave(self):
        import secrets
        self.account_lookup.reset()
        self.network_key = secrets.token_urlsafe(48)
        self.seeds.clear()
        self.records.clear()
        self.seen.clear()
        self.last_success.clear()
        self.latencies.clear()
        self.last_failure.clear()
        self.schedule = Schedule()
        self.snapshot_versions.clear()
        self.health_states.clear()
        self.blocked.clear()
        self.publish()
        self.on_change()

    def invitation(self):
        # This is an administrator credential, deliberately separate from player profiles.
        return {'version': 1, 'network_key': self.network_key, 'seed': self.profile}

    def join(self, invitation):
        if not isinstance(invitation, dict) or invitation.get('version') != 1:
            raise ValueError('Invalid federation invitation')
        key = invitation.get('network_key')
        if not isinstance(key, str) or not 32 <= len(key) <= 512:
            raise ValueError('Invalid federation key')
        profile = validate_profile(invitation['seed'])
        node_id = identity(profile['server_public_key'])
        if node_id == self.id:
            raise ValueError('Cannot join this node to itself')
        if key != self.network_key:
            if len(self.seeds) or (self.relay and self.relay.rooms):
                raise ValueError('已有联邦或活动房间，不能直接更换网络；请先停服并重新配置。')
            self.network_key = key
            self.records.clear()
            self.seen.clear()
        if len(self.seeds) >= MAX_NODES - 1 and node_id not in self.seeds:
            raise ValueError('Federation member limit reached')
        self.seeds[node_id] = profile
        self.publish()
        self.on_change()

    async def introduce(self, profile, public):
        # Bootstrap uses the seed's player credential ONLY to introduce our
        # signed membership. Subsequent pulls use SSH public-key authentication.
        async with asyncssh.connect(
            profile['server_host'], profile['server_port'], username='directory:rooms',
            password=json.dumps({'token': profile['token']}), known_hosts=([public], [], []),
            client_keys=[], agent_path=None, preferred_auth=['password'],
            connect_timeout=2, login_timeout=3, encoding='utf-8', config=None,
            x509_trusted_certs=None, x509_trusted_cert_paths=[]) as conn:
            async with conn.create_process(subsystem='buggcraft-rooms-v1') as process:
                body = dict(self.records[self.id]['body'], rooms=[])
                membership = {'body': body, 'proof': self.proof(self.id),
                              'signature': base64.b64encode(self.key.sign(canonical(body), b'ssh-ed25519')).decode()}
                process.stdin.write(json.dumps({'op': 'introduce', 'record': membership}) + '\n')
                line = await asyncio.wait_for(process.stdout.readline(), 3)
                if len(line) > 2048 or not json.loads(line).get('ok'):
                    raise ValueError('Federation admission rejected')

    async def exchange(self, profile):
        started=time.monotonic()
        public = asyncssh.import_public_key(profile['server_public_key'])
        node_id = identity(profile['server_public_key'])
        try:
            await self.pull(profile, public)
        except asyncssh.PermissionDenied:
            await self.introduce(profile, public)
            await self.pull(profile, public)
        self.last_success[node_id] = time.monotonic()
        elapsed=(time.monotonic()-started)*1000
        self.latencies[node_id]=self.latencies.get(node_id,elapsed)*.75+elapsed*.25
        self.last_failure.pop(node_id, None)

    async def pull(self, profile, public):
        node_id = identity(profile['server_public_key'])
        async with self.transport.connection(profile, node_id) as conn:
            async with conn.create_process(subsystem='buggcraft-rooms-v1', encoding=None) as process:
                request = {'op': 'snapshot', 'record_versions': wire.versions(self.live()),
                           'snapshot_versions': self.snapshot_versions.get(node_id, {})}
                if self.accounts:
                    request.update(social_version=self.accounts.social.digest(), presence_version=self.accounts.social.digest(True))
                process.stdin.write(json.dumps(request).encode() + b'\n')
                response = bytearray()
                while not response.endswith(b'\n'):
                    chunk = await asyncio.wait_for(process.stdout.read(65536), 3)
                    if not chunk or len(response) + len(chunk) > MAX_SNAPSHOT:
                        raise ValueError('Federation snapshot exceeds limit')
                    response.extend(chunk)
                value = json.loads(response)
                if not value.get('ok'):
                    raise ValueError('Federation snapshot rejected')
                self.stats['snapshot_received_bytes'] += len(response)
                self.ingest(value['result']['records'])
                if self.accounts:
                    self.accounts.replication.ingest(value['result'].get('account_routes', []))
                    self.accounts.ingest(value['result'].get('accounts', []))
                    self.accounts.social.ingest(value['result'].get('social', []))
                    self.accounts.social.ingest(value['result'].get('presence', []))
                self.snapshot_versions[node_id] = value['result'].get('snapshot_versions', {})

    async def account_rpc(self, profile, operation, **payload):
        node_id = identity(profile['server_public_key'])
        async with self.transport.connection(profile, node_id) as conn:
            async with conn.create_process(subsystem='buggcraft-rooms-v1', encoding=None) as process:
                process.stdin.write(json.dumps(dict(op=operation, **payload)).encode() + b'\n')
                response = bytearray()
                while not response.endswith(b'\n'):
                    chunk = await asyncio.wait_for(process.stdout.read(16384), 3)
                    if not chunk or len(response) + len(chunk) > 32768:
                        raise ValueError('Invalid account RPC response')
                    response.extend(chunk)
                value = json.loads(response)
                if not value.get('ok'):
                    raise ValueError('Account update rejected')
                return value['result']

    async def lookup_accounts(self, identifier):
        return await self.account_lookup.lookup(identifier)

    async def reserve_username(self, username, account):
        self.accounts.replication.reserve_name(username, account)
        peers = {n: p for n, p in self.seeds.items() if n not in self.blocked and n != self.id}
        peers.update({n: r['body']['profile'] for n, r in self.live().items() if n != self.id})
        # Fail closed during a partition. A name is never registered on only
        # one side of a known network. Reservations survive process restarts.
        results = await asyncio.gather(*(self.account_rpc(p, 'username_reserve', username=username, account=account)
                                         for p in peers.values()), return_exceptions=True)
        if any(not isinstance(r, dict) or r.get('reserved') is not True for r in results):
            raise ValueError('用户名已被使用，或网络暂时无法确认注册，请稍后重试。')

    async def fetch_account(self, account):
        targets = self.accounts.replication.targets(account)
        home = account.split('.')[-1]
        profiles = {n: r['body']['profile'] for n, r in self.live().items() if n != self.id and
                    (n == home or n in targets or any(c.get('account') == account for c in r['body'].get('account_copies', [])))}
        if home != self.id and home in self.seeds and home not in self.blocked:
            profiles.setdefault(home, self.seeds[home])
        results = await asyncio.gather(*(self.account_rpc(p, 'account_record', account=account)
                                         for p in profiles.values()), return_exceptions=True)
        for result in results:
            if isinstance(result, dict) and result.get('record'):
                try:
                    record = result['record']
                    self.accounts.validate_record(record)
                    if self.accounts.replication.current(record):
                        return record
                except (ValueError, TypeError, KeyError, asyncssh.Error):
                    continue
        return None

    async def _exchange_one(self, node_id, profile):
        try:
            async with asyncio.timeout(EXCHANGE_TIMEOUT):
                await self.exchange(profile)
        except Exception as exc:
            self.stats['exchanges_failed'] += 1
            self.last_failure[node_id] = time.monotonic()
            self.schedule.failure(node_id, self.last_failure[node_id])
            LOG.debug('Federation exchange failed node=%s reason=%s', node_id, type(exc).__name__)
        else:
            self.schedule.success(node_id)
        finally:
            self.inflight.pop(node_id, None)

    async def sync_once(self, *, wait=True):
        async with self.sync_lock:
            self.publish()
            profiles = {identity(p['server_public_key']): p for p in self.topology()}
            profiles.update(self.seeds)
            profiles = {node: profile for node, profile in profiles.items()
                        if node != self.id and node not in self.blocked}
            now = time.monotonic()
            healthy = {node for node, at in self.last_success.items() if now - at < TTL}
            selected = self.schedule.select(profiles, self.inflight, healthy, now, immediate=wait)
            for node in selected:
                self.stats['exchanges_started'] += 1
                self.inflight[node] = asyncio.create_task(self._exchange_one(node, profiles[node]))
            tasks = list(self.inflight.values())
        if wait:
            await asyncio.gather(*tasks)
        if self.accounts:
            self.accounts.replication.reconcile()

    async def run(self):
        LOG.info('Federation heartbeat started with %d configured peers', len(self.seeds))
        await asyncio.sleep(self.schedule.rng.uniform(0, 1.2))
        while True:
            try:
                async with asyncio.timeout(15):
                    await self.sync_once(wait=False)
                for node in self.health():
                    previous = self.health_states.get(node['id'])
                    if previous != node['status']:
                        self.health_states[node['id']] = node['status']
                        LOG.info('federation_health node=%s previous=%s status=%s',
                                 node['id'], previous, node['status'])
            except Exception:
                # A failed local write must not silently stop all heartbeats.
                # Cancellation still propagates so shutdown remains prompt.
                LOG.exception('Federation heartbeat failed; retrying')
            await asyncio.sleep(self.schedule.delay())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        tasks = list(self.inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.account_lookup.close()
        await self.transport.close()
