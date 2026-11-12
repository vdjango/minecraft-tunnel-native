"""Shared HTTP security, proxy policy, bounded throttling and runner ownership."""
import ipaddress
from collections import deque
import json
from pathlib import Path
import re
import time
from aiohttp import web

def _manual_proxy_ranges(state):
    ranges = state.get('trusted_proxy_cidrs')
    if ranges is None:
        ranges = []
        legacy = state.get('trusted_proxy_ips', [])
        for value in legacy if isinstance(legacy, list) else []:
            if not isinstance(value, str):
                continue
            try:
                address = ipaddress.ip_address(value)
                ranges.append(f'{address}/{address.max_prefixlen}')
            except ValueError:
                continue
    if not isinstance(ranges, list):
        ranges = []
    return _normalize_proxy_ranges(ranges)


def _trusted_proxy_ranges(state):
    ranges = _manual_proxy_ranges(state)
    if not state.get('trusted_proxy_enabled', bool(ranges)):
        return []
    cloudflare = state.get('cloudflare_proxy_cidrs', []) if state.get('cloudflare_proxy_enabled', False) else []
    if isinstance(cloudflare, list):
        ranges.extend(cloudflare)
    return _normalize_proxy_ranges(ranges)


def _normalize_proxy_ranges(ranges):
    normalized = []
    for value in ranges:
        if not isinstance(value, str):
            continue
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            continue
        if network.prefixlen and str(network) not in normalized:
            normalized.append(str(network))
        if len(normalized) == 100:
            break
    return normalized


class HttpSurface:
    @web.middleware
    async def guard(self, request, handler):
        request['external_scheme'] = self.external_scheme(request)
        protected = request.path.startswith('/api/') and request.path != '/api/status'
        if protected and request['external_scheme'] != 'https' and request.remote not in ('127.0.0.1', '::1'):
            raise web.HTTPForbidden(text='请使用 HTTPS 或 SSH 本地转发访问管理和授权功能。')
        try:
            if request.method == 'POST':
                origin = request.headers.get('Origin')
                if origin and origin != self.external_origin(request):
                    raise web.HTTPForbidden(text='Origin rejected')
                upload = bool(re.fullmatch(r'/api/feedback/[0-9a-f]{32}/attachments', request.path))
                if upload:
                    self.feedback.viewer(request, True)
                    if request.content_type != 'multipart/form-data':
                        raise web.HTTPUnsupportedMediaType()
                elif request.content_type != 'application/json':
                    raise web.HTTPUnsupportedMediaType()
                elif not isinstance(await request.json(), dict):
                    raise ValueError('Expected an object')
            response = await handler(request)
        except web.HTTPException as exc:
            response = exc
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            response = web.json_response({'error': '输入无效，请检查后重试。'}, status=400)
        if request.path.startswith(('/admin','/account','/login','/register','/launcher-login','/api/','/feedback')):
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        response.headers.update({
            'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; "
                                       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        if request.path.endswith(('.js', '.css')) and isinstance(response, web.FileResponse):
            versioned = re.fullmatch(r'[a-f0-9]{12}', request.query.get('v', ''))
            hashed = re.fullmatch(r'/vue-(?:runtime|page-account)-[A-Za-z0-9_-]+\.js|/vue-[A-Za-z0-9_-]+\.css', request.path)
            response.headers['Cache-Control'] = ('public, max-age=31536000, immutable' if versioned or hashed
                                                else 'public, max-age=0, must-revalidate')
        if request['external_scheme'] == 'https':
            response.headers['Strict-Transport-Security'] = 'max-age=86400'
        if isinstance(response, web.HTTPException):
            raise response
        return response


    def trusted_proxy(self, request):
        try:
            source=ipaddress.ip_address(request.remote or '')
        except ValueError:
            return False
        ranges = _trusted_proxy_ranges(self.state)
        if not isinstance(ranges, list):
            return False
        for value in ranges:
            if not isinstance(value, str):
                continue
            try:
                if source in ipaddress.ip_network(value, strict=False):
                    return True
            except ValueError:
                continue
        return False


    def external_scheme(self, request):
        if self.trusted_proxy(request) and request.headers.get('X-Forwarded-Proto', '').strip().lower() == 'https':
            return 'https'
        return request.scheme


    def external_origin(self, request):
        return self.external_scheme(request) + '://' + request.host


    def client_address(self, request):
        source=request.remote or 'unknown'
        if self.trusted_proxy(request):
            try:
                return str(ipaddress.ip_address(request.headers.get('CF-Connecting-IP', '')))
            except ValueError:
                pass
        return source


    def throttle(self, request, limit=8):
        now = time.monotonic()
        source = self.client_address(request)
        # Enrollment, email, login challenges and login are separate operations;
        # a normal registration must not exhaust its own subsequent login.
        key = (source, request.path)
        calls = self.attempts.setdefault(key, deque())
        self.attempts.move_to_end(key)
        while len(self.attempts) > 2048:
            self.attempts.popitem(last=False)
        while calls and calls[0] < now - 300:
            calls.popleft()
        if len(calls) >= limit:
            raise web.HTTPTooManyRequests(text='尝试过于频繁，请稍后重试。')
        calls.append(now)


    async def asset(self, request):
        return web.FileResponse(Path(__file__).with_name('web') / request.path.lstrip('/'))


    async def start(self, host, port, ssl_context=None):
        self.runner = web.AppRunner(self.app, access_log=None)
        try:
            await self.runner.setup()
            site = web.TCPSite(self.runner, host, port, ssl_context=ssl_context)
            await site.start()
            return site
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
