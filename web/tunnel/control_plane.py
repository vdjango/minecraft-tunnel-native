"""Optional managed control center, pinned enrollment and durable delivery lifecycle.

All state belongs to the portal's event loop. Existing Federation owns signed
membership, public account routing and transport; no game bytes enter this module.
"""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import secrets
import time

import asyncssh
from aiohttp import web

from .federation import canonical, identity, validate_profile
from .social_delivery import retry_pending


class ControlPlane:
    def __init__(self, portal):
        self.portal, self.fed = portal, portal.federation
        self.mode = 'mesh'
        self.bundle = None
        self.status = 'standalone'
        self.last_error = None
        self.task = None
        self.fed.control = self
        self.routes = [web.post('/api/control/invitations', self.invitation),
                       web.get('/api/control/status', self.health)]

    def configure(self, settings, base):
        mode = settings.get('mode', 'mesh')
        if mode == 'native':
            from .managed_control import ManagedControl
            self.mode = mode
            self.managed = ManagedControl(self.portal, settings, base)
            return
        if mode not in ('mesh', 'center', 'edge'):
            raise ValueError('control.mode must be mesh, center or edge')
        self.mode = mode
        filename = settings.get('enrollment_file', '')
        enrolled = self.portal.state.get('control_centers', [])
        if mode == 'edge' and enrolled and settings.get('centers_file'):
            path = Path(base) / settings['centers_file']
            if path.stat().st_size > 16384:
                raise ValueError('Control centers file exceeds limit')
            enrolled = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(enrolled, list) or not 1 <= len(enrolled) <= 3:
                raise ValueError('Expected one to three pinned control centers')
            for profile in enrolled:
                validate_profile(profile)
            if len({identity(p['server_public_key']) for p in enrolled}) != len(enrolled):
                raise ValueError('Duplicate control center identity')
            self.portal.state['control_centers'] = enrolled
            self.portal.save()
        if mode == 'edge' and not enrolled:
            if not filename:
                raise ValueError('First managed start requires control.enrollment_file')
            path = Path(base) / filename
            if path.stat().st_size > 16384:
                raise ValueError('Control enrollment file exceeds limit')
            bundle = json.loads(path.read_text(encoding='utf-8'))
            if (bundle.get('version') != 1 or not isinstance(bundle.get('token'), str) or
                    not 32 <= len(bundle['token']) <= 128):
                raise ValueError('Invalid enrollment bundle')
            validate_profile(bundle['center'])
            self.bundle = bundle
            self.fed.relay.draining = True
        if not isinstance(enrolled, list) or len(enrolled) > 3:
            raise ValueError('Invalid saved control centers')
        for profile in enrolled:
            validate_profile(profile)
        self.status = 'ready' if mode != 'edge' else 'enrolling' if self.bundle else 'connecting'

    def profiles(self):
        return [validate_profile(value) for value in self.portal.state.get('control_centers', [])
                if identity(value['server_public_key']) not in self.fed.blocked][:3]

    async def invitation(self, request):
        self.portal.session(request, True)
        if self.mode != 'center':
            raise ValueError('Only a control center issues enrollment tickets')
        value = await request.json()
        lifetime = value.get('lifetime', 600)
        if type(lifetime) is not int or not 60 <= lifetime <= 3600:
            raise ValueError('Enrollment lifetime must be 60–3600 seconds')
        now = time.time()
        saved = self.portal.state.get('control_tickets', {})
        tickets = {key: row for key, row in saved.items() if row['expires'] > now}
        if len(tickets) >= 64:
            raise ValueError('Too many active enrollment tickets')
        token = secrets.token_urlsafe(48)
        tickets[hashlib.sha256(token.encode()).hexdigest()] = {'expires': now + lifetime}
        self.portal.state['control_tickets'] = tickets
        try:
            self.portal.save()
        except BaseException:
            self.portal.state['control_tickets'] = saved
            raise
        return web.json_response({'version': 1, 'center': self.fed.profile,
                                  'token': token, 'expires': now + lifetime},
                                 headers={'Cache-Control': 'no-store'})

    def enroll(self, request):
        """Directory token plus single-use ticket and joining key possession proof."""
        if self.mode != 'center':
            raise ValueError('This node is not a control center')
        token = request.get('token')
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise ValueError('Invalid enrollment ticket')
        digest = hashlib.sha256(token.encode()).hexdigest()
        ticket = self.portal.state.get('control_tickets', {}).get(digest)
        if not ticket or ticket['expires'] <= time.time():
            raise ValueError('Enrollment ticket expired or unknown')
        profile = validate_profile(request['profile'])
        node_id = identity(profile['server_public_key'])
        public = asyncssh.import_public_key(profile['server_public_key'])
        signed = canonical({'token': token, 'profile': profile})
        if not public.verify(signed, base64.b64decode(request['signature'], validate=True)):
            raise ValueError('Invalid enrolling node signature')
        if node_id == self.fed.id or node_id in self.fed.blocked:
            raise ValueError('Enrolling node is not allowed')
        # Same-node retry recovers a response lost after commit. Another key or
        # changed address cannot reuse the ticket even before its expiry.
        binding = hashlib.sha256(canonical(profile)).hexdigest()
        if ticket.get('binding', binding) != binding:
            raise ValueError('Enrollment ticket already consumed')
        previous = dict(ticket)
        allowed = self.portal.state.setdefault('control_members', {})
        prior_member = allowed.get(node_id)
        ticket.update(binding=binding, node=node_id)
        allowed[node_id] = profile
        try:
            self.portal.save()
        except BaseException:
            ticket.clear()
            ticket.update(previous)
            if prior_member is None:
                allowed.pop(node_id, None)
            else:
                allowed[node_id] = prior_member
            raise
        from .content_cache import configuration
        from .content_cache_admin import federation_configuration
        result = self.fed.invitation()
        result['room_cache'] = federation_configuration(configuration(self.fed.relay))
        return result

    def admitted(self, node_id):
        return (self.mode != 'center' or node_id in self.portal.state.get('control_members', {}) or
                node_id in self.fed.seeds)

    async def bootstrap(self):
        bundle = self.bundle
        profile = bundle['center']
        request = {'op': 'control_enroll', 'token': bundle['token'], 'profile': self.fed.profile}
        request['signature'] = base64.b64encode(self.fed.key.sign(
            canonical({'token': bundle['token'], 'profile': self.fed.profile}), b'ssh-ed25519')).decode()
        async with asyncio.timeout(8):
            async with asyncssh.connect(profile['server_host'], profile['server_port'],
                    username='directory:rooms', password=json.dumps({'token': profile['token']}),
                    known_hosts=([asyncssh.import_public_key(profile['server_public_key'])], [], []),
                    client_keys=[], agent_path=None, config=None, connect_timeout=2, login_timeout=3,
                    x509_trusted_certs=None, x509_trusted_cert_paths=[]) as conn:
                async with conn.create_process(subsystem='buggcraft-rooms-v1') as process:
                    process.stdin.write(json.dumps(request) + '\n')
                    line = await process.stdout.readline()
                    if len(line) > 16384:
                        raise ValueError('Enrollment reply exceeds limit')
                    response = json.loads(line)
                    if not response.get('ok'):
                        raise ValueError('Enrollment rejected')
        invitation = response['result']
        from .content_cache_admin import federation_configuration
        room_cache = federation_configuration(invitation['room_cache'])
        before = dict(self.portal.state)
        self.portal.state.update(control_centers=[profile], room_cache=room_cache)
        try:
            self.fed.join(invitation)
        except BaseException:
            self.portal.state.clear()
            self.portal.state.update(before)
            raise
        self.fed.relay.content_cache_config = room_cache
        self.bundle = None
        self.fed.relay.draining = self.portal.state.get('draining', False)
        self.status = 'connecting'

    async def health(self, request):
        self.portal.session(request)
        return web.json_response(self.summary(), headers={'Cache-Control': 'no-store'})

    def summary(self):
        if self.mode == 'native':
            return {'mode': 'native', 'status': self.managed.status, 'last_error': self.managed.last_error,
                    'origin': self.managed.origin, 'node_id': self.managed.saved.get('id')}
        return {'mode': self.mode, 'status': self.status, 'last_error': self.last_error,
                'pending_deliveries': len(self.fed.accounts.social.pending) if self.fed.accounts else 0,
                'centers': [identity(p['server_public_key']) for p in self.profiles()],
                'members': len(self.portal.state.get('control_members', {}))}

    async def lifecycle(self, _app):
        if self.mode == 'native':
            await self.managed.start()
            try:
                yield
            finally:
                await self.managed.close()
            return
        self.task = asyncio.create_task(self.run())
        try:
            yield
        finally:
            await self.close()

    async def close(self):
        if self.mode == 'native':
            await self.managed.close()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def run(self):
        failures = 0
        while True:
            try:
                if self.bundle:
                    await self.bootstrap()
                if self.fed.accounts:
                    await retry_pending(self.fed)
                if self.mode == 'edge' and not self.bundle:
                    healthy = any(time.monotonic() - self.fed.last_success.get(identity(p['server_public_key']), -1e9) < 10
                                  for p in self.profiles())
                    self.status = 'ready' if healthy else 'degraded'
                failures = 0
                self.last_error = None
            except (ValueError, OSError, TimeoutError, asyncssh.Error):
                # Never include enrollment secrets or transport exception text.
                failures = min(failures + 1, 5)
                self.status, self.last_error = 'degraded', 'control-unavailable'
            await asyncio.sleep(min(30, 2 ** failures))
