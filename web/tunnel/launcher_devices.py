"""Browser approved launcher keys. Legacy account/code endpoints stay unchanged."""
import hashlib
import json
import re
import secrets
import time

from aiohttp import web
from .accounts import canonical, public_key, verify

DEVICE_GRANT = 'buggcraft-launcher-device-v1'
MAX_AGE = 180 * 86400
IDLE_AGE = 30 * 86400


class LauncherDevices:
    def __init__(self, api):
        self.api, self.store, self.portal = api, api.store, api.portal
        self.path = self.store.path.with_name('launcher-devices.json')
        self.devices = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        if not isinstance(self.devices, dict) or len(self.devices) > 512:
            raise ValueError('Invalid launcher device store')
        for identifier, device in self.devices.items():
            if (not re.fullmatch('[0-9a-f]{32}', identifier) or not isinstance(device, dict) or
                    set(device) != {'grant', 'signature', 'name', 'last_used', 'revoked'} or
                    not isinstance(device['name'], str) or not 1 <= len(device['name']) <= 64 or
                    type(device['last_used']) not in (int, float) or type(device['revoked']) is not bool):
                raise ValueError('Invalid launcher device entry')
            grant = device['grant']
            account = grant.get('a') if isinstance(grant, dict) else None
            record = self.store.replication.get(account) if isinstance(account, str) else None
            if (not record or grant.get('r') != identifier or grant.get('p') != DEVICE_GRANT or
                    not 0 < grant.get('e', 0) - grant.get('b', 0) <= MAX_AGE):
                raise ValueError('Invalid launcher device grant')
            verify(record['certificate']['public_key'], canonical(grant), device['signature'])
        self.pending, self.challenges = {}, {}
        self.routes = [web.post('/api/launcher/start', self.start),
                       web.get('/api/launcher/info', self.info),
                       web.post('/api/launcher/prepare', self.prepare),
                       web.post('/api/launcher/approve', self.approve),
                       web.post('/api/launcher/poll', self.poll),
                       web.post('/api/launcher/challenge', self.challenge),
                       web.post('/api/launcher/refresh', self.refresh),
                       web.get('/api/launcher/devices', self.list_devices),
                       web.post('/api/launcher/revoke', self.revoke)]

    def save(self):
        from .portal import atomic_json
        atomic_json(self.path, self.devices)

    def active(self, device):
        now = time.time()
        return (not device.get('revoked') and now < device['grant']['e'] and
                now < device['last_used'] + IDLE_AGE)

    def leases(self):
        return {key: min(d['grant']['e'], int(d['last_used'] + IDLE_AGE))
                for key, d in self.devices.items() if self.active(d)}

    def allowed(self, grant):
        if grant.get('i') == self.store.federation.id:
            device = self.devices.get(grant.get('r'))
            return bool(device and device['grant'] == grant and self.active(device) and
                        self.api.allowed(grant['a']))
        # Only the issuer's authenticated, signed heartbeat can grant a lease.
        record = self.store.federation.records.get(grant.get('i'), {}).get('body', {})
        leases = record.get('launcher_devices', {})
        expiry = leases.get(grant.get('r'), 0) if isinstance(leases, dict) else 0
        return (type(expiry) is int and expiry > time.time() and
                type(record.get('issued')) in (int, float) and record['issued'] > time.time() - 60)

    def pending_request(self, key):
        self.pending = {k: v for k, v in self.pending.items() if v['expires'] > time.time()}
        value = self.pending.get(key) if isinstance(key, str) else None
        if not value:
            raise web.HTTPGone(text='登录请求已过期，请回到启动器重新登录。')
        return value

    async def start(self, request):
        self.portal.throttle(request, limit=16)
        status = self.portal.public_state()
        if status.get('launcher_login') != 1:
            raise web.HTTPNotImplemented(text='此节点还未支持设备登录，请让管理员升级节点；也可使用兼容接入码。')
        data = await request.json()
        public_key(data.get('key'))
        name, remember = data.get('name', ''), data.get('remember', True)
        if not isinstance(name, str) or not 1 <= len(name) <= 64 or type(remember) is not bool:
            raise ValueError('Invalid device name')
        self.pending_request_cleanup()
        if len(self.pending) >= 256:
            raise web.HTTPTooManyRequests()
        identifier, nonce = secrets.token_hex(16), secrets.token_urlsafe(32)
        self.pending[identifier] = dict(key=data['key'], name=name, remember=remember,
                                        nonce=nonce, expires=time.time() + 600)
        return web.json_response(dict(id=identifier, nonce=nonce, expires_in=600, interval=3,
            verification_uri=self.portal.external_origin(request) + '/account/access?launcher=' + identifier),
            headers={'Cache-Control': 'no-store'})

    def pending_request_cleanup(self):
        self.pending = {k: v for k, v in self.pending.items() if v['expires'] > time.time()}

    async def info(self, request):
        value = self.pending_request(request.query.get('id'))
        fingerprint = hashlib.sha256(canonical(value['key'])).hexdigest()[:16]
        return web.json_response(dict(name=value['name'], remember=value['remember'], fingerprint=fingerprint),
                                 headers={'Cache-Control': 'no-store'})

    async def prepare(self, request):
        session = self.api.session(request, True)
        identifier = (await request.json()).get('id')
        value = self.pending_request(identifier)
        if 'device' in value:
            raise web.HTTPConflict(text='此设备已完成授权。')
        now = int(time.time())
        grant = dict(a=session['account'], i=self.store.federation.id,
                     n=self.store.federation.proof('account-audience')[:24],
                     p=DEVICE_GRANT, o=self.portal.external_origin(request), r=identifier,
                     b=now, e=now + (MAX_AGE if value['remember'] else 8 * 3600), d=value['key'])
        value['grant'] = grant
        return web.json_response(dict(grant=grant), headers={'Cache-Control': 'no-store'})

    async def approve(self, request):
        session = self.api.session(request, True)
        data = await request.json()
        identifier = data.get('id')
        value = self.pending_request(identifier)
        grant = value.get('grant')
        if not grant or grant['a'] != session['account'] or 'device' in value:
            raise web.HTTPConflict(text='登录请求已变更，请重新确认。')
        verify(self.store.replication.get(session['account'])['certificate']['public_key'],
               canonical(grant), data.get('signature'))
        self.devices = {k: d for k, d in self.devices.items()
                        if d['grant']['e'] > time.time() and not d.get('revoked')}
        if len(self.devices) >= 512 or sum(d['grant']['a'] == session['account'] and self.active(d)
                                         for d in self.devices.values()) >= 8:
            raise web.HTTPConflict(text='登录设备数量已达上限，请先撤销旧设备。')
        self.devices[identifier] = dict(grant=grant, signature=data['signature'], name=value['name'],
                                        last_used=time.time(), revoked=False)
        try:
            self.save()
        except BaseException:
            del self.devices[identifier]
            raise
        value['device'] = identifier
        self.store.federation.publish()
        return web.json_response({'ok': True})

    def profile(self, identifier):
        device = self.devices.get(identifier)
        if not device or not self.active(device) or not self.api.allowed(device['grant']['a']):
            raise web.HTTPUnauthorized(text='设备授权已失效，请重新登录。')
        profile = dict(self.store.federation.profile)
        profile['token'] = self.store.credential(device['grant']['a'],
                                                (device['grant'], device['signature']))
        return dict(profile=profile, id=identifier, name=self.store.federation.name,
                    region=self.store.federation.region,
                    expires_at=min(device['grant']['e'], device['last_used'] + IDLE_AGE))

    async def poll(self, request):
        data = await request.json()
        value = self.pending_request(data.get('id'))
        verify(value['key'], canonical(dict(p='buggcraft-launcher-poll-v1', id=data['id'],
                                           nonce=value['nonce'])), data.get('signature'))
        if 'device' not in value:
            return web.json_response({'status': 'pending'}, headers={'Cache-Control': 'no-store'})
        return web.json_response(dict(status='approved', **self.profile(value['device'])),
                                 headers={'Cache-Control': 'no-store'})

    async def challenge(self, request):
        self.portal.throttle(request, limit=32)
        identifier = (await request.json()).get('id')
        if not isinstance(identifier, str) or not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('Invalid device ID')
        self.challenges = {k: v for k, v in self.challenges.items() if v['expires'] > time.time()}
        if len(self.challenges) >= 512:
            raise web.HTTPTooManyRequests()
        nonce = secrets.token_urlsafe(32)
        message = dict(p='buggcraft-launcher-refresh-v1', id=identifier, nonce=nonce,
                       i=self.store.federation.id, o=self.portal.external_origin(request))
        self.challenges[nonce] = dict(message=message, expires=time.time() + 60)
        return web.json_response(dict(message=message), headers={'Cache-Control': 'no-store'})

    async def refresh(self, request):
        data = await request.json()
        challenge = self.challenges.pop(data.get('nonce'), None)
        if not challenge or challenge['expires'] <= time.time():
            raise web.HTTPUnauthorized(text='设备验证已过期。')
        identifier = challenge['message']['id']
        device = self.devices.get(identifier)
        if not device:
            raise web.HTTPUnauthorized(text='设备授权已失效，请重新登录。')
        verify(device['grant']['d'], canonical(challenge['message']), data.get('signature'))
        self.profile(identifier)  # Check inactivity before extending last use.
        previous = device['last_used']
        device['last_used'] = time.time()
        try:
            self.save()
        except BaseException:
            device['last_used'] = previous
            raise
        self.store.federation.publish()
        return web.json_response(self.profile(identifier), headers={'Cache-Control': 'no-store'})

    async def list_devices(self, request):
        session = self.api.session(request)
        return web.json_response({'devices': [dict(id=k, name=d['name'], created=d['grant']['b'],
            last_used=d['last_used'], expires_at=min(d['grant']['e'], int(d['last_used'] + IDLE_AGE)), active=self.active(d))
            for k, d in self.devices.items() if d['grant']['a'] == session['account']]},
            headers={'Cache-Control': 'no-store'})

    async def revoke(self, request):
        session = self.api.session(request, True)
        device = self.devices.get((await request.json()).get('id'))
        if not device or device['grant']['a'] != session['account']:
            raise web.HTTPNotFound()
        old = device['revoked']
        device['revoked'] = True
        try:
            self.save()
        except BaseException:
            device['revoked'] = old
            raise
        self.store.federation.publish()
        return web.json_response({'ok': True})
