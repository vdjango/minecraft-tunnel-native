"""Public status and an authenticated, CSRF-protected operator console."""
import asyncio
import base64
from collections import OrderedDict, deque
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import math
import os
import re
from pathlib import Path
import secrets
import tempfile
import time
import urllib.error
import urllib.request

from aiohttp import web

from .federation import identity, TTL
from .federation_schedule import INTERVAL, FANOUT
from .bandwidth import Bandwidth
from .storage import atomic_json


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError('密码需要 12–256 个字符')
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + digest


def verify_password(password, encoded):
    try:
        return hmac.compare_digest(password_hash(password, encoded.split(':')[0]), encoded)
    except (ValueError, TypeError, AttributeError):
        return False






<<<<<<< HEAD
=======
def _trusted_proxy_ranges(state):
    ranges = _manual_proxy_ranges(state)
    if not state.get('trusted_proxy_enabled', bool(ranges)):
        return []
    cloudflare = state.get('cloudflare_proxy_cidrs', []) if state.get('cloudflare_proxy_enabled', False) else []
    if isinstance(cloudflare, list):
        ranges.extend(cloudflare)
    return _normalize_proxy_ranges(ranges)
>>>>>>> origin/main




def _read_cloudflare_ranges():
    normalized = []
    for version in (4, 6):
        url = f'https://www.cloudflare.com/ips-v{version}/'
        request = urllib.request.Request(url, headers={'User-Agent': 'buggcraft-node/1'})
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read(64 * 1024 + 1).decode('ascii')
        if len(payload) > 64 * 1024:
            raise ValueError('Proxy list exceeds limit')
        current = []
        for value in payload.splitlines():
            value = value.strip()
            if not value:
                continue
            network = ipaddress.ip_network(value, strict=False)
            if network.version != version or not network.prefixlen:
                raise ValueError('Unexpected proxy network')
            if str(network) not in current:
                current.append(str(network))
        if not current or len(current) > 100:
            raise ValueError('Invalid proxy list')
        normalized.extend(current)
    return normalized


from .http_surface import HttpSurface, _manual_proxy_ranges, _trusted_proxy_ranges, _normalize_proxy_ranges


class Portal(HttpSurface):
    def __init__(self, relay, federation, state_path):
        self.relay, self.federation = relay, federation
        self.path = Path(state_path)
        self.state = json.loads(self.path.read_text(encoding='utf-8'))
        self.state['trusted_proxy_cidrs'] = _manual_proxy_ranges(self.state)
        self.state.setdefault('trusted_proxy_enabled', bool(self.state['trusted_proxy_cidrs']))
        self.state.setdefault('cloudflare_proxy_enabled', False)
        self.state.setdefault('issued_profiles', {})
        self.federation.region = self.state.get('region','')
        self.started = time.monotonic()
        try:
            saved_uptime = float(self.state.get('total_uptime_seconds', 0))
        except (TypeError, ValueError, OverflowError):
            saved_uptime = 0.0
        self.total_uptime_seconds = saved_uptime if math.isfinite(saved_uptime) and saved_uptime >= 0 else 0.0
        self.last_uptime_checkpoint = self.started
        self.sessions = {}
        self.attempts = OrderedDict()
        self.login_lock = asyncio.Semaphore(2)
        self.node_action_lock = asyncio.Lock()
        self.runner = None
        self.proxy_refresh_task = None
        self.proxy_refresh_event = asyncio.Event()
        self.federation.on_change = self.save
        self.relay.draining = self.state.get('draining', False)
        self.relay.capacity.configure(self.state.get('capacity', self.relay.capacity.policy))
        self.app = web.Application(client_max_size=32768, middlewares=[self.guard])
        self.bandwidth = Bandwidth()
        self.federation.bandwidth = self.bandwidth
        self.app.cleanup_ctx.append(self.bandwidth.lifecycle)
        from .diagnostics import Diagnostics
        self.diagnostics = Diagnostics(self.path.parent / 'logs' / 'players', relay)
        self.relay.diagnostics = self.diagnostics
        self.app.cleanup_ctx.append(self.diagnostics.lifecycle)
        self.app.add_routes([
            web.get('/api/status', self.status),
            web.get('/api/admin/diagnostics', self.diagnostics_sessions),
            web.get('/api/admin/diagnostics/{player}/{session}', self.diagnostics_events),
            web.post('/api/login', self.login), web.post('/api/logout', self.logout),
            web.get('/api/admin', self.admin), web.post('/api/settings', self.settings),
            web.get('/api/federation/invite', self.invite),
            web.post('/api/federation/join', self.join),
            web.post('/api/federation/leave', self.leave),
            web.post('/api/federation/block', self.block),
            web.post('/api/profile', self.profile),
            web.post('/api/profile/redeem', self.profile_redeem),
            web.get('/api/account/players', self.account_players),
        ])
        from .accounts_web import AccountAPI
        self.account_api = AccountAPI(self)
        self.app.add_routes(self.account_api.routes)
        from .content_cache_admin import ContentCacheAdmin
        self.content_cache_admin = ContentCacheAdmin(self)
        self.app.add_routes(self.content_cache_admin.routes)
        from .feedback import Feedback
        self.feedback = Feedback(self)
        self.app.add_routes(self.feedback.routes)
        self.app.cleanup_ctx.append(self.feedback.lifecycle)
        self.app.cleanup_ctx.append(self.uptime_lifecycle)
        from .control_plane import ControlPlane
        self.control = ControlPlane(self)
        self.app.add_routes(self.control.routes)
        self.app.cleanup_ctx.append(self.control.lifecycle)

    def save(self):
        now = time.monotonic()
        self.total_uptime_seconds += max(0.0, now - self.last_uptime_checkpoint)
        self.last_uptime_checkpoint = now
        self.state['total_uptime_seconds'] = round(self.total_uptime_seconds, 3)
        self.state.update(name=self.federation.name, network_key=self.federation.network_key,
                          seeds=list(self.federation.seeds.values()), blocked=sorted(self.federation.blocked))
        atomic_json(self.path, self.state)

    async def uptime_lifecycle(self, _app):
        async def checkpoint():
            while True:
                await asyncio.sleep(60)
                try:
                    self.save()
                except OSError as error:
                    logging.getLogger(__name__).warning('Could not persist cumulative uptime: %s', error)

        task = asyncio.create_task(checkpoint())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            try:
                self.save()
            except OSError as error:
                logging.getLogger(__name__).warning('Could not persist uptime on shutdown: %s', error)











    async def diagnostics_sessions(self, request):
        self.session(request)
        self.throttle(request, limit=180)
        sessions = await asyncio.to_thread(self.diagnostics.sessions)
        return web.json_response({'sessions': sessions, 'dropped_events': self.diagnostics.dropped,
                                  'write_errors': self.diagnostics.write_errors,
                                  'retention_days': self.diagnostics.days,
                                  'max_bytes': self.diagnostics.max_bytes})

    async def diagnostics_events(self, request):
        self.session(request)
        self.throttle(request, limit=180)
        result = await asyncio.to_thread(self.diagnostics.read, request.match_info['player'], request.match_info['session'])
        return web.json_response(result)






    def public_state(self):
        live = self.federation.live()
        now = time.monotonic()
        available = [room for room in self.relay.rooms.values() if room.available]
        reported = [room for room in available if room.game_players is not None
                    and now - room.game_reported < 15]
        paths = self.diagnostics.transport_counts()
        return {'name': self.federation.name, 'id': self.federation.id,
                'status': 'maintenance' if self.relay.draining else 'online',
                'uptime_seconds': int(now - self.started),
                'total_uptime_seconds': int(self.total_uptime_seconds + max(0.0, now - self.last_uptime_checkpoint)),
                'rooms': len(self.relay.rooms), 'connections': len(self.relay.peers),
                'players': self.federation.online_players(),
                'capacity': self.relay.capacity.snapshot(),
                'players_include_p2p': True,
                'p2p_players': paths['p2p'],
                'relay_players': paths['relay'],
                'unconfirmed_players': paths['unknown'],
                'game_players': sum(room.game_players for room in reported),
                'game_counts_reported': len(reported),
                'game_counts_unknown': len(available) - len(reported),
                'game_players_source': 'host_minecraft_status',
                'streams': len(self.relay.streams), 'max_rooms': self.relay.max_rooms,
                'max_connections': self.relay.max_connections, 'network_nodes': len(live),
                'public_join': self.state.get('public_join', False),
                'launcher_login': 0 if getattr(self.relay, 'managed_control', None) else 1,
                'registration_open': False if getattr(self.relay, 'managed_control', None) else self.state.get('registration_open', False),
                'email_verification': self.account_api.mail.required,
                'network_mode': 'federated' if self.federation.seeds else 'standalone',
                'heartbeat_interval': INTERVAL, 'heartbeat_timeout': TTL, 'heartbeat_fanout': FANOUT,
                'fingerprint': self.federation.key.get_fingerprint(),
                'nodes': [node for node in self.federation.health()
                          if node['status'] in ('online', 'indirect')]}





    async def status(self, request):
        return web.json_response(self.public_state())


    def session(self, request, write=False):
        now = time.monotonic()
        self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > now}
        session = self.sessions.get(request.cookies.get('node_session', ''))
        if not session:
            raise web.HTTPUnauthorized(text='请先登录管理员账户。')
        if write and not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
            raise web.HTTPForbidden(text='CSRF rejected')
        return session

    async def login(self, request):
        self.throttle(request)
        data = await request.json()
        if self.login_lock.locked():
            raise web.HTTPTooManyRequests(text='认证繁忙，请稍后重试。')
        async with self.login_lock:
            valid = await asyncio.to_thread(verify_password, data.get('password'), self.state['admin_hash'])
        if data.get('username') != self.state.get('admin_user', 'admin') or not valid:
            raise web.HTTPUnauthorized(text='用户名或密码错误。')
        if len(self.sessions) >= 64:
            self.sessions.pop(next(iter(self.sessions)))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.sessions[token] = {'csrf': csrf, 'expires': time.monotonic() + 3600}
        response = web.json_response({'csrf': csrf})
        response.set_cookie('node_session', token, httponly=True, secure=request['external_scheme'] == 'https',
                            samesite='Strict', max_age=3600, path='/')
        return response

    async def logout(self, request):
        self.session(request, True)
        self.sessions.pop(request.cookies.get('node_session', ''), None)
        response = web.json_response({'ok': True})
        response.del_cookie('node_session')
        return response

    async def admin(self, request):
        session = self.session(request)
        section = request.query.get('section', 'all')
        presence = (await self.federation.network_players() if section in ('all', 'players') else
                    {'players': [], 'total': self.federation.online_players(), 'partial': False})
        store = self.account_api.store
        accounts = {}
        if section in ('all', 'users'):
            accounts.update(store.replication.directory)
            accounts.update(store.records)
        users = []
        for account, record in sorted(accounts.items(), key=lambda item: item[1]['certificate']['username'].casefold()):
            local = account in store.records
            temporary = account in store.replication.temporary
            home = record['certificate']['home']
            users.append({'account': account, 'username': record['certificate']['username'], 'home': home,
                          'home_name': self.federation.name if home == self.federation.id else
                            self.federation.records.get(home, {}).get('body', {}).get('name', home[:12]),
                          'blocked': account in self.account_api.blocked,
                          'directory_synced': account in store.replication.directory,
                          'storage': 'home' if local and home == self.federation.id else
                            'replica' if local else 'temporary' if temporary else 'directory',
                          'backup': store.replication.status(account)})
        return web.json_response({'csrf': session['csrf'], 'name': self.federation.name,
                                  'bandwidth': self.bandwidth.snapshot(),
                                  'region':self.federation.region,
                                  'public_join': self.state.get('public_join', False),
                                  'capacity': self.relay.capacity.snapshot(),
                                  'registration_open': self.state.get('registration_open', False),
                                  'accounts': users,
                                  **presence,
                                  'trusted_proxy_cidrs': '\n'.join(self.state.get('trusted_proxy_cidrs', [])),
                                  'trusted_proxy_enabled': self.state.get('trusted_proxy_enabled', bool(_manual_proxy_ranges(self.state))),
                                  'cloudflare_proxy_enabled': self.state.get('cloudflare_proxy_enabled', False),
                                  'cloudflare_proxy_cidrs': self.state.get('cloudflare_proxy_cidrs', []),
                                  'cloudflare_proxy_updated': self.state.get('cloudflare_proxy_updated'),
                                  'cloudflare_proxy_last_attempt': self.state.get('cloudflare_proxy_last_attempt'),
                                  'cloudflare_proxy_last_result': self.state.get('cloudflare_proxy_last_result'),
                                  'cloudflare_proxy_last_error': self.state.get('cloudflare_proxy_last_error'),
                                  'draining': self.relay.draining,
                                  'node_id': self.federation.id,
                                  'peers': [dict(node, blocked=node['id'] in self.federation.blocked,
                                                 manageable=node['id'] in self.federation.seeds and not node['local'])
                                            for node in self.federation.health()],
                                  'node_decisions': list(reversed(self.state.get('node_decisions', [])[-20:]))})

    async def settings(self, request):
        self.session(request, True)
        data = await request.json()
        name, public_join, draining = data.get('name'), data.get('public_join'), data.get('draining')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError('Invalid name')
        if type(public_join) is not bool or type(draining) is not bool:
            raise ValueError('Invalid settings')
        registration = data.get('registration_open', self.state.get('registration_open', False))
        if type(registration) is not bool:
            raise ValueError('Invalid registration policy')
        from .capacity import policy
        next_capacity = policy(data.get('capacity', self.relay.capacity.policy))
        region=data.get('region',self.state.get('region',''))
        if not isinstance(region,str) or len(region.strip())>80:raise ValueError('Invalid region')
        proxy_enabled = data.get('trusted_proxy_enabled', self.state.get('trusted_proxy_enabled', bool(_manual_proxy_ranges(self.state))))
        cloudflare_enabled = data.get('cloudflare_proxy_enabled', self.state.get('cloudflare_proxy_enabled', False))
        if type(proxy_enabled) is not bool or type(cloudflare_enabled) is not bool:
            raise ValueError('Invalid trusted proxy settings')
        if 'trusted_proxy_cidrs' in data:
            proxies=data['trusted_proxy_cidrs']
        else:
            stored=self.state.get('trusted_proxy_cidrs',[])
            proxies='\n'.join(value for value in stored if isinstance(value,str)) if isinstance(stored,list) else ''
        if not isinstance(proxies,str) or len(proxies)>8192:raise ValueError('Invalid trusted proxy ranges')
        proxy_ranges=[]
        for line in proxies.splitlines():
            line=line.strip()
            if not line or line.startswith('#'):continue
            try: network=ipaddress.ip_network(line,strict=False)
            except ValueError:raise ValueError('Invalid trusted proxy range') from None
            if network.prefixlen==0:raise ValueError('A default route cannot be trusted as a proxy')
            if network not in proxy_ranges:proxy_ranges.append(network)
            if len(proxy_ranges)>100:raise ValueError('Too many trusted proxy ranges')
        self.state['region']=region.strip()
        self.federation.region=region.strip()
        self.federation.routing_sample=None
        self.federation.name = name.strip()
        self.relay.capacity.configure(next_capacity)
        self.state['capacity'] = next_capacity
        self.state.update(public_join=public_join, draining=draining, registration_open=registration)
        self.state['trusted_proxy_cidrs']=[str(network) for network in proxy_ranges]
        proxy_changed = (proxy_enabled != self.state.get('trusted_proxy_enabled', bool(_manual_proxy_ranges(self.state)))
                         or cloudflare_enabled != self.state.get('cloudflare_proxy_enabled', False))
        self.state.update(trusted_proxy_enabled=proxy_enabled, cloudflare_proxy_enabled=cloudflare_enabled)
        self.relay.draining = draining
        self.save()
        if proxy_changed and getattr(self, 'proxy_refresh_event', None):
            self.proxy_refresh_event.set()
        return web.json_response({'ok': True})

    async def invite(self, request):
        self.session(request)
<<<<<<< HEAD
        if getattr(self, 'control', None) and self.control.mode != 'mesh':
            raise ValueError('受管节点请使用短期控制中心入网凭证。')
=======
>>>>>>> origin/main
        from .content_cache import configuration
        from .content_cache_admin import federation_configuration
        value = self.federation.invitation()
        value['room_cache'] = federation_configuration(configuration(self.relay))
        return web.json_response(value, headers={'Cache-Control': 'no-store'})

    async def join(self, request):
        self.session(request, True)
<<<<<<< HEAD
        if getattr(self, 'control', None) and self.control.mode != 'mesh':
            raise ValueError('受管节点请通过 control 配置加入，不能直接更换联邦。')
=======
>>>>>>> origin/main
        from .content_cache_admin import federation_configuration
        data = await request.json()
        if not isinstance(data, dict) or 'room_cache' not in data:
            raise ValueError('联邦邀请缺少资源加速配置，请对方升级节点后重新生成邀请。')
        value = federation_configuration(data['room_cache'])
        async with self.content_cache_admin.lock:
            previous = dict(self.state)
            self.state['room_cache'] = value
            try:
                # Federation.join persists membership and the adopted settings together.
                self.federation.join(data)
            except Exception:
                self.state.clear()
                self.state.update(previous)
                raise
            self.relay.content_cache_config = value
        return web.json_response({'ok': True, 'resource_settings_synced': True})

    async def block(self, request):
        self.session(request, True)
        data = await request.json()
        node_id = data.get('id')
        if node_id == self.federation.id or node_id not in self.federation.seeds:
            raise ValueError('Unknown peer')
        if type(data.get('blocked')) is not bool:
            raise ValueError('Invalid block state')
        reason = data.get('reason')
        if (not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 300 or
                data.get('confirmation') != node_id):
            raise ValueError('Confirm the exact node ID and provide a reason')
        self.throttle(request)
        if self.login_lock.locked():
            raise web.HTTPTooManyRequests(text='认证繁忙，请稍后重试。')
        async with self.login_lock:
            valid = await asyncio.to_thread(verify_password, data.get('password'), self.state['admin_hash'])
        if not valid:
            raise web.HTTPUnauthorized(text='管理员密码错误。')
        async with self.node_action_lock:
            if (node_id in self.federation.blocked) == data['blocked']:
                raise web.HTTPConflict(text='节点状态已经改变，请刷新后重试。')
            name = self.federation.records.get(node_id, {}).get('body', {}).get('name', '已配置节点')
            if data['blocked']:
                self.federation.blocked.add(node_id)
                # This decision is local: no other operator receives an expulsion command.
                for peer in tuple(self.relay.peers):
                    if peer.role == 'federation' and peer.conn.get_extra_info('username') == 'federation:' + node_id:
                        peer.conn.close()
            else:
                self.federation.blocked.discard(node_id)
            self.state.setdefault('node_decisions', []).append({
                'at': int(time.time()), 'id': node_id, 'name': name,
                'action': 'isolate' if data['blocked'] else 'restore', 'reason': reason.strip()})
            self.state['node_decisions'] = self.state['node_decisions'][-100:]
            self.save()
        return web.json_response({'ok': True, 'scope': 'local'})

    async def leave(self, request):
        self.session(request, True)
        if getattr(self, 'control', None) and self.control.mode != 'mesh':
            raise ValueError('请先在维护窗口切换为 mesh 模式，再退出网络。')
        await self.federation.close()
        for peer in tuple(self.relay.peers):
            if peer.role == 'federation':
                peer.conn.close()
        self.federation.leave()
        self.federation.task = asyncio.create_task(self.federation.run())
        return web.json_response({'ok': True})

    async def profile(self, request):
        data = await request.json()
        if not isinstance(data, dict) or type(data.get('format', 1)) is not int or data.get('format', 1) not in (1, 2):
            raise web.HTTPBadRequest(text='Invalid access code format')
        account = None
        authorization = None
        try:
            session = self.account_api.session(request)
            account = session['account']
            authorization = session.get('authorization')
        except web.HTTPUnauthorized:
            pass
        if not account and not self.state.get('public_join', False):
            session = self.sessions.get(request.cookies.get('node_session', ''))
            if not session or session['expires'] <= time.monotonic():
                self.throttle(request)
                data = await request.json()
                if self.login_lock.locked():
                    raise web.HTTPTooManyRequests(text='认证繁忙，请稍后重试。')
                async with self.login_lock:
                    valid = await asyncio.to_thread(verify_password, data.get('password'), self.state['access_hash'])
                if not valid:
                    raise web.HTTPUnauthorized(text='节点访问密码错误。')
        profile = dict(self.federation.profile)
        if account:
            profile['token'] = self.account_api.store.credential(account, authorization)
        if data.get('format', 1) == 1:
            code = 'buggnode1-' + base64.urlsafe_b64encode(json.dumps(profile, separators=(',', ':')).encode()).decode().rstrip('=')
            return web.json_response({'code': code, 'profile': profile, 'credential': profile.get('token', ''),
                'identity': 'account' if account else 'anonymous', 'fingerprint': self.federation.key.get_fingerprint()},
                headers={'Cache-Control': 'no-store'})
        issued = self.state.get('issued_profiles', {})
        if not isinstance(issued, dict):
            issued = {}
        now = time.time()
        issued = {key: value for key, value in issued.items()
                  if isinstance(value, dict) and isinstance(value.get('expires'), (int, float))
                  and value['expires'] > now and isinstance(value.get('profile'), dict)}
        if len(issued) >= 4096:
            issued = dict(sorted(issued.items(), key=lambda item: item[1]['expires'])[-3072:])
        reference = secrets.token_urlsafe(32)
        issued[reference] = {'expires': now + 7 * 86400, 'profile': profile}
        self.state['issued_profiles'] = issued
        self.save()
        issuer = self.external_origin(request)
        code = 'buggnode2-' + base64.urlsafe_b64encode(issuer.encode()).decode().rstrip('=') + '.' + reference
        return web.json_response({'code': code, 'credential': profile.get('token', ''),
                                  'identity': 'account' if account else 'anonymous',
                                  'fingerprint': self.federation.key.get_fingerprint()},headers={'Cache-Control':'no-store'})

    async def profile_redeem(self, request):
        self.throttle(request, limit=16)
        reference=(await request.json()).get('reference','')
        if not isinstance(reference,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,64}',reference):
            raise web.HTTPBadRequest(text='Invalid profile reference')
        profiles=self.state.get('issued_profiles',{})
        issued=profiles.get(reference) if isinstance(profiles,dict) else None
        if (not isinstance(issued,dict) or not isinstance(issued.get('expires'),(int,float))
                or not issued['expires']>time.time() or not isinstance(issued.get('profile'),dict)):
            raise web.HTTPNotFound(text='Access code expired or unavailable')
        return web.json_response({'profile':issued['profile']},headers={'Cache-Control':'no-store'})

    async def account_players(self, request):
        self.account_api.session(request)
        return web.json_response(await self.federation.network_players(),headers={'Cache-Control':'no-store'})

    async def start(self, host, port, ssl_context=None):
        self.federation.publish()
        self.proxy_refresh_task = asyncio.create_task(self.proxy_refresh_loop())
        return await super().start(host, port, ssl_context)

    async def close(self):
        if self.proxy_refresh_task:
            self.proxy_refresh_task.cancel()
            await asyncio.gather(self.proxy_refresh_task, return_exceptions=True)
            self.proxy_refresh_task = None
        await super().close()

    async def refresh_cloudflare_proxies(self):
        if not (self.state.get('trusted_proxy_enabled', bool(_manual_proxy_ranges(self.state)))
                and self.state.get('cloudflare_proxy_enabled', False)):
            return False
        self.state['cloudflare_proxy_last_attempt'] = time.time()
        self.state['cloudflare_proxy_last_result'] = 'running'
        self.state.pop('cloudflare_proxy_last_error', None)
        try:
            ranges = await asyncio.to_thread(_read_cloudflare_ranges)
            if not ranges:
                raise ValueError('empty Cloudflare range list')
        except (OSError, ValueError, UnicodeError) as error:
            if isinstance(error, urllib.error.HTTPError):
                reason = f'Cloudflare 返回 HTTP {error.code}'
            elif isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError):
                reason = '连接超时'
            elif isinstance(error, TimeoutError):
                reason = '连接超时'
            elif isinstance(error, UnicodeError):
                reason = '返回内容编码无效'
            elif isinstance(error, ValueError):
                reason = '返回的网段列表无效'
            else:
                reason = '网络连接失败'
            self.state['cloudflare_proxy_last_result'] = 'failed'
            self.state['cloudflare_proxy_last_error'] = reason
            self.save()
            return False
        self.state['cloudflare_proxy_cidrs'] = ranges
        self.state['cloudflare_proxy_updated'] = time.time()
        self.state['cloudflare_proxy_last_result'] = 'success'
        self.state.pop('cloudflare_proxy_last_error', None)
        self.save()
        return True

    async def proxy_refresh_loop(self):
        while True:
            self.proxy_refresh_event.clear()
            await self.refresh_cloudflare_proxies()
            enabled = self.state.get('trusted_proxy_enabled') and self.state.get('cloudflare_proxy_enabled')
            try:
                await asyncio.wait_for(self.proxy_refresh_event.wait(), timeout=24 * 60 * 60 if enabled else None)
            except asyncio.TimeoutError:
                pass
