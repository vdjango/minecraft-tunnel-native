"""HTTP challenge authentication: browser-held P-256 keys, no password transport."""
import asyncio
import hmac
import json
import re
import secrets
import time

from aiohttp import web
from .accounts import Accounts, canonical, verify


class AccountAPI:
    def __init__(self, portal):
        self.portal = portal
        self.store = Accounts(portal.federation, portal.path.with_name('accounts-replica.json'))
        from .mail import VerificationMail
        self.mail = VerificationMail(portal.path.with_name('smtp.json'), portal.federation.name)
        self.registration_lock = asyncio.Lock()
        self.email_grants = {}
        portal.federation.accounts = self.store
        portal.relay.account_authenticate = self.authenticate
        portal.relay.account_blocked = lambda account: account in self.blocked
        from .launcher_devices import LauncherDevices
        self.store.launcher_devices = LauncherDevices(self)
        self.routes = [
            web.post('/api/account/enroll', self.enroll),
            web.post('/api/account/register', self.register),
            web.post('/api/account/email-code', self.email_code),
            web.post('/api/account/email-challenge', self.email_challenge),
            web.post('/api/account/email-accounts', self.email_accounts),
            web.get('/api/admin/mail', self.mail_settings),
            web.post('/api/admin/mail', self.save_mail_settings),
            web.post('/api/admin/mail/test', self.test_mail),
            web.post('/api/account/challenge', self.challenge),
            web.post('/api/account/recover', self.recover),
            web.post('/api/account/login', self.login),
            web.post('/api/account/logout', self.logout),
            web.get('/api/account/me', self.me),
            web.get('/api/account/social', self.social),
            web.post('/api/account/social', self.social_action),
            web.post('/api/account/lookup', self.lookup),
            web.get('/api/account/record/{account}', self.record),
            web.post('/api/account/backup', self.backup),
            web.post('/api/account/block', self.block),
        ] + self.store.launcher_devices.routes

    @property
    def blocked(self):
        return self.portal.state.get('blocked_accounts', [])

    def allowed(self, account):
        record = self.store.replication.get(account) if isinstance(account, str) else None
        return bool(record and account not in self.blocked and
                    record['certificate']['home'] not in self.portal.federation.blocked)

    async def authenticate(self, token):
        return self.store.authenticate(token, self.blocked)

    def session(self, request, write=False):
        now = time.monotonic()
        self.store.sessions = {k: v for k, v in self.store.sessions.items() if v['expires'] > now}
        session = self.store.sessions.get(request.cookies.get('account_session', ''))
        if not session or not self.allowed(session['account']):
            raise web.HTTPUnauthorized(text='请先登录用户账号。')
        if write and not hmac.compare_digest(request.headers.get('X-Account-CSRF', ''), session['csrf']):
            raise web.HTTPForbidden(text='CSRF rejected')
        return session

    async def enroll(self, request):
        if not self.portal.state.get('registration_open', False):
            raise web.HTTPForbidden(text='本节点暂未开放注册。')
        self.portal.throttle(request)
        uid = secrets.token_hex(16)
        account = uid + '.' + self.portal.federation.id
        return web.json_response(dict(self.store.challenge('enroll', account, self.portal.external_origin(request)),
                                      account=account, iterations=600000))

    async def register(self, request):
        try:
            return await self._register(request)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None

    async def _register(self, request):
        if not self.portal.state.get('registration_open', False):
            raise web.HTTPForbidden(text='本节点暂未开放注册。')
        self.portal.throttle(request)
        data = await request.json()
        if self.mail.required and not self.mail.enabled:
            raise ValueError('本节点尚未配置发信邮箱，暂不能完成注册。请联系管理员。')
        email = None
        if self.mail.required or data.get('email_ticket'):
            email = self.mail.verify(data.get('email_ticket'), data.get('email_code'), data.get('username'), data.get('nonce'), purpose='register')
        challenge = self.store.consume(data['nonce'], 'enroll')
        verify(data['public_key'], challenge['message'].encode(), data['proof'])
        if not isinstance(data.get('state'), dict) or data['state'].get('account') != challenge['account'] or data['state'].get('revision') != 1:
            raise ValueError('Invalid initial account state')
        if type(data.get('copies')) is not int or not isinstance(data.get('routing_signature'), str):
            raise ValueError('Signed backup consent is required')
        async with self.registration_lock:
            record = self.store.enroll(challenge['account'].split('.')[0], data['username'],
                                      data['public_key'], data['state'], data['signature'],
                                      data['copies'], data['routing_signature'])
            if email:
                self.store.email_accounts.link(email, challenge['account'])
        return web.json_response({'account': record['state']['account']}, status=201)

    async def email_code(self, request):
        self.portal.throttle(request)
        data = await request.json()
        challenge = self.store.challenges.get(data.get('nonce'))
        purpose = data.get('purpose', 'register')
        if purpose not in ('register', 'login', 'link'):
            raise ValueError('邮箱操作无效，请重试。')
        if purpose == 'register' and not self.portal.state.get('registration_open', False):
            raise web.HTTPForbidden(text='本节点暂未开放注册。')
        if purpose == 'register':
            if not challenge or challenge['kind'] != 'enroll' or challenge['expires'] <= time.monotonic():
                raise ValueError('请重新开始注册。')
            challenge['expires'] = time.monotonic() + 600
        elif purpose == 'link':
            session = self.session(request, write=True)
            challenge = None
        else:
            challenge = None
        nonce = data['nonce'] if challenge else session['account'] if purpose == 'link' else secrets.token_urlsafe(24)
        from .mail import MailDeliveryError
        try:
            ticket = await self.mail.issue(data.get('email'), data.get('username', '') if purpose == 'register' else '',
                                           nonce, purpose=purpose)
        except MailDeliveryError as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from None
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        return web.json_response({'ticket': ticket, 'nonce': nonce, 'expires_in': 600, 'retry_after': 60})

    async def email_challenge(self, request):
        self.portal.throttle(request)
        data = await request.json()
        if not self.mail.enabled:
            raise web.HTTPServiceUnavailable(text='本节点邮箱验证服务暂不可用。')
        try:
            email = self.mail.verify(data.get('ticket'), data.get('code'), '', data.get('nonce'), purpose='login')
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        mailbox = self.store.email_accounts.mailbox(email)
        now = time.monotonic()
        self.email_grants = {k: v for k, v in self.email_grants.items() if v['expires'] > now}
        if len(self.email_grants) >= 256:
            raise web.HTTPTooManyRequests(text='邮箱验证请求过多，请稍后重试。')
        grant = secrets.token_urlsafe(32)
        self.email_grants[grant] = {'mailbox': mailbox, 'expires': now + 600}
        return web.json_response({'grant': grant, 'accounts': self.store.email_accounts.candidates(mailbox)})

    async def email_accounts(self, request):
        session = self.session(request, write=True)
        self.portal.throttle(request)
        data = await request.json()
        account = session['account']
        try:
            email = self.mail.verify(data.get('ticket'), data.get('code'), '', account, purpose='link')
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        self.store.email_accounts.link(email, account)
        mailbox = self.store.email_accounts.mailbox(email)
        self.store.email_accounts.choose(mailbox, account)
        return web.json_response({'accounts': self.store.email_accounts.candidates(mailbox)})

    async def mail_settings(self, request):
        self.portal.session(request)
        return web.json_response(self.mail.public_settings())

    async def save_mail_settings(self, request):
        self.portal.session(request, True)
        self.mail.configure(await request.json())
        return web.json_response(self.mail.public_settings())

    async def test_mail(self, request):
        self.portal.session(request, True)
        self.portal.throttle(request)
        await self.mail.send((await request.json()).get('email'), '123456', testing=True)
        return web.json_response({'sent': True})

    async def challenge(self, request):
        self.portal.throttle(request)
        data = await request.json()
        record = await self.store.replication.resolve(data.get('account'))
        account = record['state']['account']
        if not self.allowed(account):
            raise web.HTTPUnauthorized(text='此账号已在本节点停用。')
        challenge = self.store.challenge('login', account, self.portal.external_origin(request), data.get('delegation'))
        return web.json_response(dict(challenge, record=record))

    async def recover(self, request):
        self.portal.throttle(request)
        data = await request.json()
        record = data['record']
        account = self.store.validate_record(record)
        home = record['certificate']['home']
        fed = self.portal.federation
        if home != fed.id and home not in fed.seeds and account not in self.store.replication.directory:
            raise web.HTTPForbidden(text='恢复文件的注册节点不属于本节点信任的网络。')
        if (account in self.blocked or record['certificate']['home'] in self.portal.federation.blocked or
                not self.store.replication.current(record)):
            raise web.HTTPForbidden(text='恢复文件已过期或账号被停用。')
        temporary = self.store.replication.temporary
        if len(temporary) >= 256:
            temporary.pop(next(iter(temporary)))
        temporary[account] = (time.monotonic() + 3600, record)
        challenge = self.store.challenge('login', account, self.portal.external_origin(request), data.get('delegation'))
        return web.json_response(dict(challenge, record=record))

    async def login(self, request):
        self.portal.throttle(request)
        data = await request.json()
        challenge = self.store.consume(data['nonce'], 'login')
        account = challenge['account']
        if not self.allowed(account):
            raise web.HTTPUnauthorized()
        verify(self.store.replication.get(account)['certificate']['public_key'],
               challenge['message'].encode(), data['signature'])
        if data.get('email_grant'):
            grant = self.email_grants.pop(data['email_grant'], None)
            if not grant or grant['expires'] <= time.monotonic():
                raise web.HTTPBadRequest(text='邮箱验证已过期，请重新发送验证码。')
            self.store.email_accounts.choose(grant['mailbox'], account)
        self.store.authorize(account, challenge, data['signature'])
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        if len(self.store.sessions) >= 256:
            self.store.sessions.pop(next(iter(self.store.sessions)))
        self.store.sessions[token] = {'account': account, 'csrf': csrf, 'expires': time.monotonic() + 3600,
                                      'authorization': (json.loads(challenge['message']), data['signature'])}
        response = web.json_response({'account': account, 'csrf': csrf})
        response.set_cookie('account_session', token, httponly=True, secure=self.portal.external_scheme(request) == 'https',
                            samesite='Strict', max_age=3600, path='/')
        return response

    async def me(self, request):
        session = self.session(request)
        record = self.store.replication.get(session['account'])
        from .friend_codes import friend_code
        return web.json_response({'account': session['account'], 'friendCode': friend_code(session['account']), 'username': record['certificate']['username'],
                                  'csrf': session['csrf'], 'revision': record['state']['revision'],
                                  'home': record['certificate']['home'],
                                  'backup': self.store.replication.status(session['account'])})

    async def logout(self, request):
        self.session(request, True)
        self.store.sessions.pop(request.cookies.get('account_session', ''), None)
        response = web.json_response({'ok': True})
        response.del_cookie('account_session')
        return response

    async def social(self, request):
        return web.json_response(self.store.social.state(self.session(request)['account']))

    async def social_action(self, request):
        session = self.session(request, True)
        self.portal.throttle(request)
        event = (await request.json()).get('event')
        # This endpoint manages relationships only. Room invitations use the
        # room-scoped launcher API, never an arbitrary web-supplied room ID.
        if not isinstance(event, dict) or not isinstance(event.get('action'), dict) or event['action'].get('op') not in ('request', 'accept', 'remove'):
            raise ValueError('Invalid relationship action')
        try:
            result = self.store.social.apply(session['account'], event)
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from None
        return web.json_response(result)

    async def lookup(self, request):
        from .friend_codes import matches_account, valid_identifier
        self.session(request, True)
        self.portal.throttle(request)
        username = (await request.json()).get('username')
        if not valid_identifier(username):
            raise web.HTTPBadRequest(text='请输入好友码、用户名或完整账号 ID。')
        username = username.strip()
        results = await self.portal.federation.lookup_accounts(username)
        for account, entry in self.store.replication.directory.items():
            if matches_account(username, account, entry['certificate']['username']):
                results.append({'account': account, 'username': entry['certificate']['username']})
        unique = {item['account']: item for item in results
                  if item['account'] not in self.blocked and
                  item['account'].split('.')[-1] not in self.portal.federation.blocked}
        return web.json_response({'users': list(unique.values())[:20]})

    async def record(self, request):
        account = request.match_info['account']
        if not self.allowed(account):
            raise web.HTTPNotFound()
        # The encrypted identity is intentionally portable, including after its home fails.
        return web.json_response(self.store.replication.get(account))

    async def backup(self, request):
        session = self.session(request, True)
        data = await request.json()
        account = session['account']
        old = self.store.replication.get(account)
        state = data['state']
        if not isinstance(state, dict) or state.get('account') != account or state.get('revision') != old['state']['revision'] + 1:
            raise web.HTTPConflict(text='副本已更新，请重新登录后再保存。')
        record = dict(old, state=state, signature=data['signature'])
        record.update(routing=data['routing'], routing_signature=data['routing_signature'])
        self.store.validate_record(record)
        from .account_replication import header
        route = header(record)
        fed = self.portal.federation
        targets = self.store.replication.targets(account, route)
        if fed.id in targets:
            self.store.ingest_one(record)
            self.store.save()
        else:
            profiles = {n: r['body']['profile'] for n, r in fed.live().items()}
            profiles.update(fed.seeds)
            results = await asyncio.gather(*(fed.account_rpc(profiles[n], 'account_update', record=record)
                                           for n in targets if n in profiles), return_exceptions=True)
            if not any(isinstance(r, dict) and r.get('saved') for r in results):
                raise web.HTTPServiceUnavailable(text='没有可写入的备份节点，请稍后重试。关闭备份需要注册节点在线。')
            self.store.replication.accept(route)
            self.store.replication.temporary[account] = (time.monotonic() + 3600, record)
            self.store.replication.save()
        return web.json_response({'ok': True, 'revision': state['revision']})

    async def block(self, request):
        self.portal.session(request, True)
        data = await request.json()
        account = data.get('account')
        if (account not in self.store.records and account not in self.store.replication.directory) or type(data.get('blocked')) is not bool:
            raise ValueError('Invalid local account block')
        blocked = set(self.blocked)
        (blocked.add if data['blocked'] else blocked.discard)(account)
        self.portal.state['blocked_accounts'] = sorted(blocked)
        self.portal.save()
        if data['blocked']:
            for peer in tuple(self.portal.relay.peers):
                if getattr(peer, 'account', None) == account:
                    peer.conn.close()
        return web.json_response({'ok': True})
