"""Native control-service adapter; game bytes remain in the existing relay core."""
import asyncio
import json
from pathlib import Path
import random
import ssl
import time
from urllib.parse import urlsplit

from aiohttp import ClientSession, ClientTimeout, TCPConnector, web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature


def control_origin(value):
    """Validate one configurable HTTPS authority, shared by setup and runtime."""
    origin = value.rstrip('/')
    url = urlsplit(origin)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or
            url.query or url.fragment or url.path or any(c.isspace() for c in origin)):
        raise ValueError('Control address must be an HTTPS origin')
    _ = url.port
    return origin


class ControlConnection:
    """One bounded, strictly verified HTTPS transport per control adapter."""
    def __init__(self, settings, base):
        self.origin = control_origin(settings['origin'])
        self.public_origin = control_origin(settings.get('public_origin', self.origin))
        self.base = Path(base)
        self.ca = (self.base / settings['ca_file']).read_text(encoding='utf-8') if settings.get('ca_file') else ''
        self.public_ca = (self.base / settings['public_ca_file']).read_text(encoding='utf-8') if settings.get('public_ca_file') else self.ca
        self.public = settings['signing_public_key']
        if len(bytes.fromhex(self.public)) != 32:
            raise ValueError('Invalid control signing key')
        self.verifier = Ed25519PublicKey.from_public_bytes(bytes.fromhex(self.public))
        self.session = None

    def metadata(self):
        return {'origin': self.public_origin, 'signing_public_key': self.public, 'ca_pem': self.public_ca,
                'protocol': 1}

    async def request(self, path, body, token):
        if not self.session:
            context = ssl.create_default_context(cadata=self.ca or None)
            self.session = ClientSession(connector=TCPConnector(ssl=context, limit=4),
                                         timeout=ClientTimeout(total=6), trust_env=False)
        headers = {'Authorization': 'Bearer ' + token}
        async with self.session.post(self.origin + '/v1/' + path, json=body, headers=headers,
                                     allow_redirects=False) as response:
            raw = await response.content.read(262145)
            if len(raw) > 262144:
                raise ValueError('Control response limit')
            value = json.loads(raw)
            if response.status != 200:
                code = value.get('error', {}).get('code', 'unavailable')
                raise ValueError('control-' + str(code)[:32])
            return value

    async def close(self):
        if self.session:
            await self.session.close()
            self.session = None


class IdentityAuthority(ControlConnection):
    """Control-host-only browser account adapter, never installed on relay nodes."""
    def __init__(self, portal, settings, base):
        super().__init__(settings, base)
        self.portal = portal
        self.admin = (self.base / settings['admin_token_file']).read_text(encoding='utf-8').strip()
        if len(self.admin) < 32:
            raise ValueError('Invalid control administrator credential')
        self.region, self.operator = settings.get('region', 'cn-north'), settings.get('operator', 'unknown')

    def metadata(self):
        return dict(super().metadata(), region=self.region, operator=self.operator)

    async def authorize(self, device):
        record = self.portal.account_api.store.get(device['grant']['a'])
        await self.request('admin/device/authorize', dict(certificate=record['certificate'],
            issuer_signature=record['issuer_signature'], device_id=device['grant']['r'],
            device_grant=device['grant'], delegation_signature=device['signature'],
            expires_at=min(device['grant']['e'], int(device['last_used'] + 30 * 86400))), self.admin)

    async def revoke(self, device):
        await self.request('admin/device/revoke', dict(account_id=device['grant']['a'],
                           device_id=device['grant']['r']), self.admin)


class ManagedControl(ControlConnection):
    def __init__(self, portal, settings, base, *, signing_key=None):
        super().__init__(settings, base)
        self.portal, self.relay = portal, portal.relay
        self.key = serialization.load_ssh_private_key((signing_key or portal.federation.key).export_private_key(), password=None)
        self.public_key = self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        self.path = self.base / 'managed-node.json'
        self.saved = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        # Restart changes the heartbeat sequence: obtain a new authenticated epoch.
        self.saved['expires_at'] = 0
        self.credential_file = self.base / settings['credential_file'] if settings.get('credential_file') else None
        self.region, self.operator = settings.get('region', 'cn-north'), settings.get('operator', 'unknown')
        self.endpoint, self.proof_endpoint = settings['endpoint'], settings['proof_endpoint']
        self.slots, self.bandwidth = int(settings.get('slots', '64')), int(settings.get('bandwidth', '6000000'))
        if not 1 <= self.slots <= 2048 or not 1 <= self.bandwidth <= 1000000000:
            raise ValueError('Invalid managed node resource budget')
        self.session = None
        self.status, self.last_error = 'connecting', None
        self.sequence = 0
        self.allocations = {}
        self.lock = asyncio.Lock()
        self.task = None
        self.renew_task = None
        self.renew_cursor = ''
        self.batch_refresh = True
        self.lease_error = None
        self.portal.app.router.add_post('/.well-known/buggcraft-node-proof', self.proof)
        self.relay.managed_control = self

    def metadata(self):
        return {'origin': self.origin, 'signing_public_key': self.public, 'ca_pem': self.ca,
                'region': self.region, 'operator': self.operator, 'protocol': 1}

    def claims(self, token, audience):
        if not isinstance(token, str) or not 128 <= len(token) <= 8192:
            raise ValueError('Invalid managed authorization')
        encoded, signature = token.split('.')
        try:
            self.verifier.verify(bytes.fromhex(signature), encoded.encode())
        except InvalidSignature:
            raise ValueError('Invalid managed signature') from None
        data = json.loads(bytes.fromhex(encoded))
        if data.get('aud') != audience or type(data.get('exp')) is not int or data['exp'] <= time.time():
            raise ValueError('Expired managed authorization')
        return data

    def player(self, token):
        data = self.claims(token, 'buggcraft-control-v1')
        if data.get('kind') != 'player':
            raise ValueError('Player identity required')
        return data

    async def call(self, path, body, token=None):
        try:
            return await self.request(path, body, self.saved.get('token', '') if token is None else token)
        except ValueError as error:
            if token is None and str(error) in ('control-revoked', 'control-expired', 'control-unauthorized'):
                self.saved['expires_at'] = 0
            raise

    async def enroll(self):
        body = {'public_key': self.public_key, 'protocol': 1, 'region': self.region,
                'operator': self.operator, 'endpoint': self.endpoint,
                'slots': self.slots, 'bandwidth': self.bandwidth}
        if self.saved.get('id'):
            body['node_id'] = self.saved['id']
        elif self.credential_file:
            body['credential'] = self.credential_file.read_text(encoding='utf-8').strip()
        else:
            raise ValueError('First managed start requires credential_file')
        challenge = await self.call('enrollment/challenge', body, token='')
        grant = await self.call('enrollment/prove', {'challenge_id': challenge['challenge_id'],
            'signature': self.key.sign(challenge['message'].encode()).hex()}, token='')
        if grant['signing_public_key'] != self.public:
            raise ValueError('Control signing identity mismatch')
        from .storage import atomic_json
        self.saved = grant
        atomic_json(self.path, grant)
        self.sequence = 0
        try:
            await self.call('node/activate', {'proof_endpoint': self.proof_endpoint})
        except ValueError as error:
            # Recovery retains active status; a fresh registration must pass proof.
            if str(error) != 'control-conflict':
                raise

    async def proof(self, request):
        data = await request.json()
        nonce = data.get('nonce')
        if not isinstance(nonce, str) or not 16 <= len(nonce) <= 128 or data.get('endpoint') != self.endpoint:
            raise web.HTTPBadRequest()
        message = 'buggcraft-node-proof-v1\n' + nonce + '\n' + self.endpoint
        return web.json_response({'signature': self.key.sign(message.encode()).hex()},
                                 headers={'Cache-Control': 'no-store'})

    async def admit(self, peer, token, ticket, role, room):
        identity = self.player(token)
        data = self.claims(ticket, 'buggcraft-control-v1:allocation')
        if (data.get('node_id') != self.saved.get('id') or data.get('public_key') != self.public_key or
                data.get('endpoint') != self.endpoint or data.get('room_id') != room or
                data.get('sub') != identity.get('sub') or data.get('device') != identity.get('device') or
                (role == 'host' and data.get('intent') not in ('new', 'recover')) or
                (role == 'client' and data.get('intent') != 'join')):
            raise ValueError('Allocation binding mismatch')
        reservation = data['reservation_id']
        async with self.lock:
            existing = self.allocations.get(reservation)
            if existing and existing['peer'] is not peer and not existing['peer'].conn.is_closed():
                raise ValueError('Allocation already consumed')
            await self.call('placement/confirm', {'reservation_id': reservation, 'ticket': ticket, 'admission': True})
            self.allocations[reservation] = {'peer': peer, 'ticket': ticket, 'account': data['sub'],
                                             'disconnected': None}
            peer.managed_reservation = reservation
        return data['sub']

    def disconnected(self, peer):
        reservation = getattr(peer, 'managed_reservation', None)
        if reservation in self.allocations and self.allocations[reservation]['peer'] is peer:
            self.allocations[reservation]['disconnected'] = time.monotonic()

    async def renew_allocations(self):
        # Rotate before I/O: a slow/failing prefix must not starve later players.
        keys = sorted(self.allocations)
        keys = [key for key in keys if key > self.renew_cursor] + [key for key in keys if key <= self.renew_cursor]
        if not keys:
            return
        selected = keys[:256]
        self.renew_cursor = selected[-1]
        # Admission and release of the same reservation cannot overlap. Heartbeat
        # has its own task and never waits on this lock or the renewal request.
        async with self.lock:
            now = time.monotonic()
            entries = {key: self.allocations[key] for key in selected if key in self.allocations}
            batch = [dict(reservation_id=key, release=entry['disconnected'] is not None and
                          now - entry['disconnected'] > 60) for key, entry in entries.items()]
            if not batch:
                return
            if self.batch_refresh:
                try:
                    response = await self.call('placement/refresh', dict(reservations=batch))
                except ValueError as error:
                    if str(error) != 'control-not_found':
                        raise
                    # Third-party older controls retain the existing protocol.
                    self.batch_refresh = False
                else:
                    rows = response.get('reservations')
                    if not isinstance(rows, list) or len(rows) != len(batch):
                        raise ValueError('Invalid allocation refresh response')
                    indexed = {row.get('reservation_id'): row for row in rows if isinstance(row, dict)}
                    if set(indexed) != set(entries):
                        raise ValueError('Invalid allocation refresh identities')
                    for key, row in indexed.items():
                        if row.get('state') == 'released' or row.get('error') in ('expired', 'stale', 'not_found', 'not_active'):
                            self.allocations.pop(key, None)
                        elif row.get('state') != 'active':
                            raise ValueError('Allocation refresh rejected')
                    return
            # Compatibility only; the shipped control batches up to 256 IDs in
            # one authenticated request, without sending player tokens/tickets.
            async def refresh(item):
                key, entry = item['reservation_id'], entries[item['reservation_id']]
                async with slots:
                    try:
                        if item['release']:
                            await self.call('placement/release', dict(reservation_id=key))
                            self.allocations.pop(key, None)
                        else:
                            await self.call('placement/confirm', dict(reservation_id=key, ticket=entry['ticket']))
                    except ValueError as error:
                        if str(error) in ('control-expired', 'control-stale', 'control-not_found'):
                            self.allocations.pop(key, None)
                        else:
                            raise
            slots = asyncio.Semaphore(4)
            results = await asyncio.gather(*(refresh(item) for item in batch), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    async def renew_loop(self):
        while True:
            try:
                if self.saved.get('token') and self.saved.get('expires_at', 0) > time.time():
                    await self.renew_allocations()
                    self.lease_error = None
            except asyncio.CancelledError:
                raise
            except Exception:
                self.lease_error = 'allocation-refresh-unavailable'
                self.status = 'degraded'
            await asyncio.sleep(5 + random.random())

    async def run(self):
        failures = 0
        while True:
            try:
                if self.saved.get('expires_at', 0) < time.time() + 60:
                    await self.enroll()
                self.sequence += 1
                await self.call('node/heartbeat', {'sequence': self.sequence,
                    'observed_slots': len([p for p in self.relay.peers if p.role in ('host', 'client')]),
                    'observed_bandwidth': 0})
                self.status, self.last_error, failures = ('degraded' if self.lease_error else 'ready'), self.lease_error, 0
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status, self.last_error = 'degraded', 'control-unavailable'
                failures = min(5, failures + 1)
            await asyncio.sleep(min(30, max(5, 2 ** failures)) + random.random())

    async def start(self):
        self.task = asyncio.create_task(self.run())
        self.renew_task = asyncio.create_task(self.renew_loop())

    async def close(self):
        tasks = [task for task in (self.task, self.renew_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.task = self.renew_task = None
        await super().close()
