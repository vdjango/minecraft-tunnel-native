"""One-time HTTPS onboarding, used by the normal node entry point."""
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hmac
import html
import ipaddress
import logging
import os
from pathlib import Path
import secrets
import socket
import ssl
import tempfile

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .provisioning import initialize

ASSETS = Path(__file__).with_name('web')


def setup_tls(directory):
    """Create a short-lived certificate for the setup listener, never plain HTTP."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Buggcraft setup')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([
                       x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = Path(directory) / 'cert.pem', Path(directory) / 'key.pem'
    for path, value in [(cert_path, certificate.public_bytes(serialization.Encoding.PEM)),
                        (key_path, key.private_bytes(serialization.Encoding.PEM,
                                                    serialization.PrivateFormat.PKCS8,
                                                    serialization.NoEncryption()))]:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(value)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context, certificate.fingerprint(hashes.SHA256()).hex()


class SetupServer:
    def __init__(self, directory, *, web_port=18443):
        self.directory = Path(directory).resolve()
        self.web_port = web_port
        self.token = secrets.token_urlsafe(32)
        self.finished = asyncio.Event()
        self.lock = asyncio.Lock()
        self.app = web.Application(client_max_size=16384)
        self.app.on_response_prepare.append(self.headers)
        self.app.add_routes([web.get('/', self.page), web.get('/setup.css', self.asset),
                             web.get('/setup.js', self.asset), web.post('/setup', self.submit)])

    async def headers(self, request, response):
        response.headers.update({
            'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; "
                                       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})

    def authorize(self, token):
        if not isinstance(token, str) or not hmac.compare_digest(token.encode(), self.token.encode()):
            raise web.HTTPForbidden(text='请使用服务启动日志中的完整初始化链接。')
        if (self.directory / 'server.conf').exists():
            raise web.HTTPGone(text='初始化已完成，请访问节点首页。')

    async def page(self, request):
        self.authorize(request.query.get('token'))
        text = (ASSETS / 'setup.html').read_text(encoding='utf-8')
        text = text.replace('{{TOKEN}}', html.escape(self.token, quote=True))
        text = text.replace('{{WEB_PORT}}', str(self.web_port))
        return web.Response(text=text, content_type='text/html')

    async def asset(self, request):
        return web.FileResponse(ASSETS / request.path.lstrip('/'))

    async def submit(self, request):
        self.authorize(request.headers.get('X-Setup-Token'))
        if not request.secure:
            raise web.HTTPForbidden(text='初始化必须使用 HTTPS。')
        if request.headers.get('Origin', request.scheme + '://' + request.host) != request.scheme + '://' + request.host:
            raise web.HTTPForbidden(text='Origin rejected')
        if request.content_type != 'application/json':
            raise web.HTTPUnsupportedMediaType()
        if self.lock.locked():
            raise web.HTTPConflict(text='正在保存配置，请稍候。')
        async with self.lock:
            self.authorize(request.headers.get('X-Setup-Token'))
            try:
                data = await request.json()
                if not isinstance(data, dict):
                    raise ValueError()
                host = data['host'].strip()
                port, web_port = int(data['port']), int(data['web_port'])
                if data['password'] != data['password_confirm']:
                    raise ValueError('两次输入的管理员密码不一致。')
                if not 1024 <= port <= 65535 or not 1024 <= web_port <= 65535 or port == web_port:
                    raise ValueError('Invalid listen ports')
                # Catch existing services before committing a configuration which
                # would immediately fail to boot. Keep reservations until saved.
                with ExitStack() as reservations:
                    try:
                        for candidate, kind in [(port, socket.SOCK_STREAM), (port, socket.SOCK_DGRAM)] + (
                                [(web_port, socket.SOCK_STREAM)] if web_port != self.web_port else []):
                            listener = reservations.enter_context(socket.socket(socket.AF_INET, kind))
                            listener.bind(('0.0.0.0', candidate))
                    except OSError:
                        return web.json_response({'error': '所选端口已被占用或无权监听，请更换端口。'}, status=409)
                    await self.initialize(data, host, port, web_port)
            except (ValueError, TypeError, KeyError, AttributeError):
                return web.json_response({'error': '配置无效：请检查地址、端口、容量和密码。管理员密码需要 12–256 个字符。'}, status=400)
            except OSError:
                logging.getLogger(__name__).exception('Unable to write node configuration')
                return web.json_response({'error': '无法保存配置，请检查数据目录权限和磁盘空间。'}, status=500)
            url_host = '[' + host + ']' if ':' in host else host
            response = web.json_response({'url': f'https://{url_host}:{web_port}/admin', 'complete': True})
            response.force_close()
            # Flush the response before the caller replaces this listener with Portal.
            await response.prepare(request)
            await response.write_eof()
            self.finished.set()
            return response

    async def initialize(self, data, host, port, web_port):
        # Hashing and key generation must not stall the HTTP event loop.
        await asyncio.to_thread(initialize, self.directory, host, port,
            name=data['name'].strip(), web_port=web_port,
            admin_username=data['username'].strip(), admin_password=data['password'],
            entry_host=data.get('entry_host', '').strip() or None,
            entry_port=int(data['entry_port']) if data.get('entry_port') else None,
            public_join=data.get('public_join', False),
            capacity={'max_rooms': int(data['max_rooms']), 'room_capacity': int(data['room_capacity']),
                      'bandwidth_mb_s': float(data['bandwidth_mb_s'])})


async def first_run(directory, *, host='0.0.0.0', web_port=18443, open_browser=False):
    server = SetupServer(directory, web_port=web_port)
    runner = web.AppRunner(server.app, access_log=None)
    with tempfile.TemporaryDirectory(prefix='buggcraft-setup-') as temporary:
        tls, fingerprint = setup_tls(temporary)
        await runner.setup()
        try:
            await web.TCPSite(runner, host, web_port, ssl_context=tls).start()
            display_host = 'SERVER_IP' if host in ('0.0.0.0', '::') else host
            if ':' in display_host:
                display_host = '[' + display_host + ']'
            print(f'\nFirst setup: https://{display_host}:{web_port}/?token={server.token}', flush=True)
            print(f'Setup certificate SHA256: {fingerprint}', flush=True)
            print('Replace SERVER_IP with your server address. Keep this setup link private.\n', flush=True)
            if open_browser:
                import webbrowser
                try:
                    await asyncio.to_thread(webbrowser.open,
                        f'https://127.0.0.1:{web_port}/?token={server.token}')
                except Exception:
                    logging.getLogger(__name__).warning('Cannot open browser; use the setup link above.')
            await server.finished.wait()
        finally:
            await runner.cleanup()
    return Path(directory).resolve() / 'server.conf'
