"""Pair a resource origin from the authenticated node console, without restart."""
import asyncio
import base64
import ipaddress
import json
import re
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout, TCPConnector, web
from aiohttp.resolver import DefaultResolver


def invitation(value):
    if not isinstance(value, str) or len(value) > 4096 or not value.startswith('buggcache1.'):
        raise ValueError('请粘贴源站后台生成的接入邀请。')
    try:
        encoded = value.split('.', 1)[1]
        data = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4), altchars=b'-_', validate=True))
        url, ticket = data['url'], data['ticket']
        parsed = urlsplit(url)
        if (set(data) != {'url', 'ticket'} or parsed.scheme != 'https' or not parsed.hostname
                or parsed.port not in (None, 443) or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment
                or not re.fullmatch('[A-Za-z0-9_-]{32,128}', ticket)
                or any(c in url for c in ('\\', '\r', '\n', '\x00'))):
            raise ValueError()
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                raise ValueError('private origin')
        except ValueError as error:
            if str(error) == 'private origin':
                raise
        return url, ticket
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        raise ValueError('接入邀请无效，请从源站后台重新复制。') from None


class PublicResolver(DefaultResolver):
    async def resolve(self, *args, **kwargs):
        rows = await super().resolve(*args, **kwargs)
        if not rows or any(not ipaddress.ip_address(row['host']).is_global for row in rows):
            raise OSError('Resource origin must use a public address')
        return rows


async def claim(url, ticket, node_id):
    async with ClientSession(timeout=ClientTimeout(total=6), connector=TCPConnector(resolver=PublicResolver())) as http:
        async with http.post(url + '/room-cache/nodes/claim', json=dict(ticket=ticket, node_id=node_id),
                             allow_redirects=False) as response:
            raw = bytearray()
            async for block in response.content.iter_chunked(4096):
                raw.extend(block)
                if len(raw) > 8192:
                    raise ValueError('源站返回内容过大，无法接入。')
            if response.status == 403:
                raise ValueError('邀请已使用、过期或撤销，请重新生成。')
            if response.status != 200:
                raise ValueError('源站暂不可用，请检查地址和服务状态后重试。')
            data = json.loads(raw)
    if (not isinstance(data, dict) or data.get('protocol') != 'room-content-cache-v1'
            or not re.fullmatch('[A-Za-z0-9_-]{1,64}', str(data.get('issuer', '')))
            or not isinstance(data.get('secret'), str) or not 32 <= len(data['secret']) <= 256):
        raise ValueError('源站接入响应无效。')
    return dict(url=url, issuer=data['issuer'], secret=data['secret'], name=str(data.get('name', ''))[:80], enabled=True)


class ContentCacheAdmin:
    def __init__(self, portal):
        self.portal = portal
        self.lock = asyncio.Lock()
        portal.relay.content_cache_config = portal.state.get('room_cache')
        self.routes = [web.get('/api/admin/room-cache', self.handle), web.post('/api/admin/room-cache', self.handle)]

    async def handle(self, request):
        portal = self.portal
        portal.session(request, write=request.method == 'POST')
        if request.method == 'POST':
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError('接入请求格式无效。')
            async with self.lock:
                if data.get('action') == 'connect':
                    url, ticket = invitation(data.get('invitation'))
                    try:
                        value = await claim(url, ticket, portal.federation.id)
                    except (ClientError, asyncio.TimeoutError, OSError):
                        raise ValueError('无法连接源站，请检查域名、HTTPS 和服务状态；当前配置已保留。') from None
                elif data.get('action') == 'disconnect':
                    value = dict(enabled=False)
                else:
                    raise ValueError('未知资源源站操作。')
                previous = portal.state.get('room_cache')
                portal.state['room_cache'] = value
                try:
                    portal.save()
                except OSError:
                    portal.state['room_cache'] = previous
                    raise
                portal.relay.content_cache_config = value
        value = portal.state.get('room_cache') or {}
        return web.json_response(dict(configured=bool(value.get('enabled') and value.get('secret')),
                                      url=value.get('url', ''), name=value.get('name', ''), issuer=value.get('issuer', '')),
                                 headers={'Cache-Control': 'no-store'})
