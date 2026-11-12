"""HTTP challenge authentication: browser-held P-256 keys, no password transport."""
import asyncio
import hmac
import json
import secrets
import time

from aiohttp import web
from .accounts import Accounts, verify


class AccountAPI:
    def __init__(self, portal, *, local_only=False):
        self.portal = portal
        self.store = Accounts(portal.federation, portal.path.with_name('accounts-replica.json'), local_only=local_only)
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
            web.post('/api/account/challenge', self.challenge),
            web.post('/api/account/login', self.login),
            web.post('/api/account/logout', self.logout),
            web.get('/api/account/me', self.me),
        ] + self.store.launcher_devices.routes

    @property
    def blocked(self):
        return self.portal.state.get('blocked_accounts', [])

    def allowed(self, account):
        record = self.store.get(account) if isinstance(account, str) else None
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
        if not self.store.local_only and (type(data.get('copies')) is not int or not isinstance(data.get('routing_signature'), str)):
            raise ValueError('Signed backup consent is required')
        async with self.registration_lock:
            record = self.store.enroll(challenge['account'].split('.')[0], data['username'],
                                      data['public_key'], data['state'], data['signature'],
                                      None if self.store.local_only else data['copies'],
                                      None if self.store.local_only else data['routing_signature'])
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


    async def challenge(self, request):
        self.portal.throttle(request)
        data = await request.json()
        record = await self.store.resolve(data.get('account'))
        account = record['state']['account']
        if not self.allowed(account):
            raise web.HTTPUnauthorized(text='此账号已在本节点停用。')
        challenge = self.store.challenge('login', account, self.portal.external_origin(request), data.get('delegation'))
        return web.json_response(dict(challenge, record=record))


    async def login(self, request):
        self.portal.throttle(request)
        data = await request.json()
        challenge = self.store.consume(data['nonce'], 'login')
        account = challenge['account']
        if not self.allowed(account):
            raise web.HTTPUnauthorized()
        verify(self.store.get(account)['certificate']['public_key'],
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
        record = self.store.get(session['account'])
        from .friend_codes import friend_code
        return web.json_response({'account': session['account'], 'friendCode': friend_code(session['account']), 'username': record['certificate']['username'],
                                  'csrf': session['csrf'], 'revision': record['state']['revision'],
                                  'home': record['certificate']['home']})

    async def logout(self, request):
        self.session(request, True)
        self.store.sessions.pop(request.cookies.get('account_session', ''), None)
        response = web.json_response({'ok': True})
        response.del_cookie('account_session')
        return response
