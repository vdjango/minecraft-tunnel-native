"""Minimal managed surfaces: browser identity on the control host, proof on relays.

Both run on one asyncio loop. No legacy admin, gossip, replication, content cache,
feedback, social polling or node-login handlers are constructed here.
"""
from collections import OrderedDict
import json
from pathlib import Path

from aiohttp import web

from .http_surface import HttpSurface
from .managed_control import IdentityAuthority, ManagedControl


class ServicePortal(HttpSurface):
    def __init__(self, relay, state_path):
        self.relay, self.path = relay, Path(state_path)
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.attempts, self.runner = OrderedDict(), None
        self.app = web.Application(client_max_size=32768, middlewares=[self.guard])
        self.app.router.add_get('/api/status', self.status)

    async def status(self, request):
        return web.json_response(self.public_state())


class IdentityPortal(ServicePortal):
    def __init__(self, relay, federation, state_path, settings, base):
        super().__init__(relay, state_path)
        # Reuse the persisted issuer/key format through AccountIssuer, without
        # constructing the old network membership/replication runtime.
        self.federation = federation
        from .accounts_web import AccountAPI
        self.account_api = AccountAPI(self, local_only=True)
        self.identity_authority = IdentityAuthority(self, settings, base)
        for path in ('/', '/login', '/register', '/account', '/account/access',
                     '/account/devices', '/account/email', '/launcher-login'):
            self.app.router.add_get(path, self.account_page)
        self.app.router.add_get('/{asset:vue-page-account-[A-Za-z0-9_-]+\\.js|vue-runtime-[A-Za-z0-9_-]+\\.js|vue-[A-Za-z0-9_-]+\\.css}', self.asset)
        self.app.add_routes(self.account_api.routes)

    async def account_page(self, request):
        if request.path == '/':
            raise web.HTTPFound('/account')
        return web.FileResponse(Path(__file__).with_name('web') / 'account.html')

    def public_state(self):
        return {'role': 'control', 'launcher_login': 1,
                'registration_open': self.state.get('registration_open', False),
                'email_verification': self.account_api.mail.required}

    async def close(self):
        try:
            await super().close()
        finally:
            await self.identity_authority.close()


class RelayPortal(ServicePortal):
    def __init__(self, relay, signing_key, state_path, settings, base):
        super().__init__(relay, state_path)
        relay.draining = self.state.get('draining', False)
        relay.capacity.configure(self.state.get('capacity', relay.capacity.policy))
        # A managed relay has no federation/public-key login or account store.
        relay.federation = None
        self.control = ManagedControl(self, settings, base, signing_key=signing_key)
        from .diagnostics import Diagnostics
        relay.diagnostics = Diagnostics(Path(base) / 'logs' / 'players', relay)
        self.app.cleanup_ctx.append(relay.diagnostics.lifecycle)
        self.app.cleanup_ctx.append(self.control_lifecycle)

    def public_state(self):
        return {'role': 'relay', 'launcher_login': 0, 'registration_open': False,
                'control_status': self.control.status}

    async def control_lifecycle(self, app):
        await self.control.start()
        try:
            yield
        finally:
            await self.control.close()
