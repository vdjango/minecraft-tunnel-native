"""Node isolation belongs to the local operator, not the federation."""
import asyncio
import base64
import importlib.util
import json
import re
from pathlib import Path
import secrets
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import asyncssh
from aiohttp import CookieJar
from aiohttp.test_utils import TestClient, TestServer
from tunnel.federation import Federation
from tunnel.portal import Portal, password_hash
from tunnel.relay import RelayServer


class GovernanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'node-state.json'
        self.password = secrets.token_urlsafe(24)
        self.path.write_text(json.dumps({'admin_hash': password_hash(self.password)}), encoding='utf-8')
        network = secrets.token_urlsafe(48)
        self.relays, self.feds = [], []
        for name in ('A', 'B', 'C'):
            key = asyncssh.generate_private_key('ssh-ed25519')
            relay = await RelayServer('127.0.0.1', 0, host_key=key, token=secrets.token_urlsafe(48)).start()
            fed = Federation(key, dict(server_host='127.0.0.1', server_port=relay.port,
                token=relay.token, server_public_key=key.export_public_key().decode()), network_key=network, name=name)
            relay.federation, fed.relay = fed, relay
            fed.publish()
            self.relays.append(relay)
            self.feds.append(fed)
        self.a, self.b, self.c = self.feds
        for fed in self.feds:
            fed.ingest([f.records[f.id] for f in self.feds])
        self.portal = Portal(self.relays[0], self.a, self.path)
        self.client = TestClient(TestServer(self.portal.app), cookie_jar=CookieJar(unsafe=True))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await asyncio.gather(*(f.close() for f in self.feds))
        await asyncio.gather(*(r.close() for r in self.relays))
        self.directory.cleanup()

    async def login(self):
        response = await self.client.post('/api/login', json={'username':'admin','password':self.password})
        self.assertEqual(response.status, 200)
        return {'X-CSRF-Token':(await response.json())['csrf']}

    def action(self, **changes):
        return dict({'id':self.b.id, 'blocked':True, 'confirmation':self.b.id,
                     'reason':'Reviewed suspicious traffic', 'password':self.password}, **changes)

    async def test_pages_are_public_but_admin_data_is_not(self):
        home = await (await self.client.get('/')).text()
        self.assertIn('href="/admin"', home)
        self.assertIn('href="/developers"', home)
        self.assertNotIn('class="entry-strip"', home)
        self.assertNotIn('id="account-dialog"', home)
        self.assertIn('href="/account"', home)
        self.assertIn('href="/connect"', home)
        self.assertNotIn('admin-dialog', home)
        for path, expected in [('/admin','id="admin-peers"'),('/developers','account_lookup'),
                               ('/admin.js','confirmation'),('/console.css','.admin-node'),
                               ('/account','data-account-view="friends"'),
                               ('/admin/diagnostics','连接诊断'),
                               ('/feedback','问题反馈'),
                               ('/developers','节点管理的权限与流程')]:
            response = await self.client.get(path)
            self.assertEqual(response.status, 200, path)
            self.assertIn(expected, await response.text())
        for path, location in [('/launcher-example.py', '/developers#examples'),
                               ('/protocol.md', '/developers#protocol')]:
            response = await self.client.get(path, allow_redirects=False)
            self.assertEqual(response.status, 302)
            self.assertEqual(response.headers['Location'], location)
            self.assertNotIn('Content-Disposition', response.headers)
        self.assertEqual((await self.client.get('/api/admin')).status, 401)
        self.assertEqual((await self.client.post('/api/federation/block', json=self.action())).status, 401)
        self.assertFalse(self.a.blocked)

    async def test_all_pages_share_navigation_order_and_one_current_item(self):
        expected=['/','/network','/connect','/developers','/feedback','/account','/admin']
        for path in ('/','/network','/connect','/developers','/login','/register','/account',
                     '/feedback','/admin','/admin/settings','/admin/diagnostics','/admin/feedback'):
            response=await self.client.get(path)
            self.assertEqual(response.status,200,path)
            header=re.search(r'<header\b.*?</header>',await response.text(),re.S).group()
            self.assertEqual(re.findall(r'href="([^"]+)"',header),expected,path)
            self.assertEqual(header.count('aria-current="page"'),1,path)

    async def test_diagnostic_records_require_admin_and_polling_does_not_use_login_limit(self):
        self.assertEqual((await self.client.get('/api/admin/diagnostics')).status,401)
        await self.login()
        for _ in range(12):
            response=await self.client.get('/api/admin/diagnostics')
            self.assertEqual(response.status,200)
            self.assertEqual((await response.json())['sessions'],[])
        # A normal user's cookie is not a substitute for the operator cookie.
        await self.client.post('/api/logout',json={},headers={'X-CSRF-Token':self.portal.sessions[next(iter(self.portal.sessions))]['csrf']})
        self.assertEqual((await self.client.get('/api/admin/diagnostics')).status,401)

    async def test_registration_steps_have_separate_limits_and_unknown_proxy_headers_are_ignored(self):
        self.portal.state['registration_open'] = True
        for _ in range(8):
            self.assertEqual((await self.client.post('/api/account/enroll', json={})).status, 200)
        response = await self.client.post('/api/account/enroll', json={}, headers={'X-Real-IP': '198.51.100.99'})
        self.assertEqual(response.status, 429)
        # Reaching enrollment's cap must not consume the administrator login cap.
        await self.login()

    async def test_configured_proxy_separates_users_and_malformed_headers_cannot_reset_limits(self):
        self.portal.state.update(registration_open=True, trusted_proxy_cidrs=['127.0.0.1/32'])
        for _ in range(8):
            response = await self.client.post('/api/account/enroll', json={}, headers={'CF-Connecting-IP': '198.51.100.1'})
            self.assertEqual(response.status, 200)
        self.assertEqual((await self.client.post('/api/account/enroll', json={}, headers={'CF-Connecting-IP': '198.51.100.1'})).status, 429)
        self.assertEqual((await self.client.post('/api/account/enroll', json={}, headers={'CF-Connecting-IP': '198.51.100.2'})).status, 200)
        for _ in range(8):
            self.assertEqual((await self.client.post('/api/account/enroll', json={}, headers={'CF-Connecting-IP': 'invalid'})).status, 200)
        self.assertEqual((await self.client.post('/api/account/enroll', json={}, headers={'CF-Connecting-IP': 'also-invalid'})).status, 429)

    async def test_email_delivery_error_is_actionable_and_not_reported_as_sent(self):
        from unittest.mock import patch
        import smtplib
        self.portal.state['registration_open'] = True
        mail = self.portal.account_api.mail
        mail.configure({'username': 'sender@example.com', 'password': 'private-smtp-secret'})
        enrollment = await (await self.client.post('/api/account/enroll', json={})).json()
        data = dict(nonce=enrollment['nonce'], email='user@example.com', username='Alice')
        with patch.object(mail, '_send', side_effect=smtplib.SMTPAuthenticationError(535, b'private-smtp-secret')):
            response = await self.client.post('/api/account/email-code', json=data)
        self.assertEqual(response.status, 503)
        self.assertIn('发信账号验证失败', await response.text())
        self.assertNotIn('private-smtp-secret', await response.text())
        self.assertFalse(mail.pending)
        with patch.object(mail, '_send'):
            response = await self.client.post('/api/account/email-code', json=data)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['retry_after'], 60)
        response = await self.client.post('/api/account/email-code', json=data)
        self.assertEqual(response.status, 400)
        self.assertIn('验证码已发送', await response.text())

    async def test_bridge_guide_uses_portable_program_and_marks_native_only_features(self):
        response = await self.client.get('/developers')
        self.assertEqual(response.status, 200)
        page = await response.text()
        self.assertIn('actions/workflows/bridge.yml', page)
        self.assertIn('buggcraft-bridge.exe', page)
        self.assertIn('ProcessBuilder(bridgePath)', page)
        self.assertIn('ProcessStartInfo(bridgePath)', page)
        self.assertIn('当前桥接加入接口需要邀请码', page)
        self.assertIn('不属于当前 JSON-RPC 方法', page)
        self.assertNotIn('ProcessBuilder(pythonPath', page)
        self.assertNotIn('subprocess.Popen(["python"', page)
        self.assertNotIn('Content-Disposition', response.headers)

    async def test_bandwidth_is_admin_only_cached_and_has_no_interface_details(self):
        from unittest.mock import Mock
        from tunnel.bandwidth import Bandwidth
        self.portal.bandwidth.task.cancel()
        await asyncio.gather(self.portal.bandwidth.task, return_exceptions=True)
        reader = Mock(side_effect=[{'private-interface':(100, 200)},
                                  {'private-interface':(2_000_100, 6_000_200)}])
        meter = Bandwidth(reader, Mock(side_effect=[10, 12, 12, 12]))
        self.portal.bandwidth = meter
        await meter.sample()
        await meter.sample()
        public = await (await self.client.get('/api/status')).json()
        self.assertNotIn('bandwidth', public)
        self.assertNotIn('capacity', public)
        self.assertEqual((await self.client.get('/api/admin')).status, 401)
        await self.login()
        for _ in range(2):
            response = await self.client.get('/api/admin')
            self.assertEqual(response.status, 200)
            sample = (await response.json())['bandwidth']
            self.assertEqual(sample['status'], 'ready')
            self.assertEqual(sample['rx_bytes_per_second'], 1_000_000)
            self.assertEqual(sample['tx_bytes_per_second'], 3_000_000)
            self.assertNotIn('private-interface', json.dumps(sample))
        self.assertEqual(reader.call_count, 2)

    async def test_csrf_confirmation_reason_password_and_target_are_required(self):
        headers = await self.login()
        before = self.path.read_bytes()
        self.assertEqual((await self.client.post('/api/federation/block', json=self.action())).status, 403)
        for changes, status in [({'confirmation':self.c.id},400),({'reason':'short'},400),
                                ({'password':'incorrect-password'},401),({'id':self.a.id},400),
                                ({'id':'f' * 32},400),({'blocked':1},400)]:
            response = await self.client.post('/api/federation/block', json=self.action(**changes), headers=headers)
            self.assertEqual(response.status, status, changes)
        self.assertFalse(self.a.blocked)
        self.assertEqual(self.path.read_bytes(), before)

    async def test_mail_settings_require_admin_csrf_and_never_return_authorization_code(self):
        from unittest.mock import patch
        settings = dict(host='smtp.example.com', port=465, username='sender@example.com',
                        password='test-smtp-secret', from_name='Node', required=True)
        self.assertEqual((await self.client.get('/api/admin/mail')).status, 401)
        self.assertEqual((await self.client.post('/api/admin/mail', json=settings)).status, 401)
        headers = await self.login()
        self.assertEqual((await self.client.post('/api/admin/mail', json=settings)).status, 403)
        response = await self.client.post('/api/admin/mail', json=settings, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertNotIn('password', await response.json())
        self.assertNotIn(settings['password'], await (await self.client.get('/api/admin/mail')).text())
        self.assertNotIn(settings['password'], await (await self.client.get('/api/status')).text())
        self.assertEqual(json.loads(self.path.with_name('smtp.json').read_text())['password'], settings['password'])
        messages = []
        with patch.object(self.portal.account_api.mail, '_send', side_effect=messages.append):
            self.assertEqual((await self.client.post('/api/admin/mail/test',
                json={'email':'recipient@example.com'})).status, 403)
            response = await self.client.post('/api/admin/mail/test',
                json={'email':'recipient@example.com'}, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['To'], 'recipient@example.com')

    async def test_admin_routes_only_show_the_selected_section(self):
        from html.parser import HTMLParser
        class Sections(HTMLParser):
            def __init__(self):
                super().__init__()
                self.visible = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if attrs.get('class') == 'console-card' and 'hidden' not in attrs:
                    self.visible.append(attrs.get('id'))
        for section in ('nodes','network','settings','users','history','mail'):
            parser = Sections()
            response = await self.client.get('/admin/' + section)
            self.assertEqual(response.status, 200)
            parser.feed(await response.text())
            self.assertEqual(parser.visible, [{'network':'network-settings'}.get(section,section)])

    async def test_unconfigured_required_mail_blocks_registration_without_consuming_challenge(self):
        self.portal.state['registration_open'] = True
        response = await self.client.post('/api/account/enroll', json={})
        self.assertEqual(response.status, 200)
        nonce = (await response.json())['nonce']
        # A registration attempt cannot bypass default email verification, and
        # the enrolment challenge survives so the user can request a code later.
        response = await self.client.post('/api/account/register', json={'nonce':nonce})
        self.assertEqual(response.status, 400)
        self.assertIn(nonce, self.portal.account_api.store.challenges)
        self.assertFalse(self.portal.account_api.store.records)
        self.assertFalse(self.portal.account_api.mail.enabled)

    async def test_isolation_is_local_and_audited_without_secrets(self):
        headers = await self.login()
        async with asyncssh.connect('127.0.0.1', self.relays[0].port, username='federation:' + self.b.id,
            known_hosts=([asyncssh.import_public_key(self.a.profile['server_public_key'])], [], []), client_keys=[self.b.key], agent_path=None,
            config=None, preferred_auth=['publickey']) as member:
            response = await self.client.post('/api/federation/block', json=self.action(), headers=headers)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())['scope'], 'local')
            await asyncio.wait_for(member.wait_closed(), 3)
        self.assertNotIn(self.b.id, self.a.live())
        self.assertIn(self.b.id, self.c.live())
        self.assertFalse(self.c.blocked)
        self.c.ingest(self.a.snapshot()['records'])
        self.assertIn(self.b.id, self.c.live())
        state = await (await self.client.get('/api/admin')).json()
        target = next(p for p in state['peers'] if p['id'] == self.b.id)
        self.assertEqual(target['name'], 'B')
        self.assertEqual(target['status'], 'blocked')
        self.assertFalse(next(p for p in state['peers'] if p['local'])['manageable'])
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['blocked'], [self.b.id])
        self.assertEqual(saved['node_decisions'][0]['action'], 'isolate')
        self.assertNotIn(self.password, json.dumps(saved['node_decisions']))
        self.assertNotIn('Reviewed suspicious traffic', await (await self.client.get('/api/status')).text())
        self.assertNotIn('node_decisions', json.dumps(self.a.snapshot()))

    async def test_restore_requires_password_and_duplicate_action_conflicts(self):
        headers = await self.login()
        self.assertEqual((await self.client.post('/api/federation/block', json=self.action(), headers=headers)).status, 200)
        self.assertEqual((await self.client.post('/api/federation/block', json=self.action(), headers=headers)).status, 409)
        payload = self.action(blocked=False)
        payload.pop('password')
        self.assertEqual((await self.client.post('/api/federation/block', json=payload, headers=headers)).status, 401)
        payload['password'] = self.password
        self.assertEqual((await self.client.post('/api/federation/block', json=payload, headers=headers)).status, 200)
        self.a.ingest([self.b.records[self.b.id]])
        self.assertIn(self.b.id, self.a.live())
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual([d['action'] for d in saved['node_decisions']], ['isolate','restore'])
        self.assertFalse(saved['blocked'])

    async def test_signed_member_cannot_issue_network_expulsion(self):
        async with asyncssh.connect('127.0.0.1', self.relays[0].port, username='federation:' + self.b.id,
            known_hosts=([asyncssh.import_public_key(self.a.profile['server_public_key'])], [], []), client_keys=[self.b.key], agent_path=None,
            config=None, preferred_auth=['publickey'], encoding=None) as member:
            async with member.create_process(subsystem='buggcraft-rooms-v1', encoding=None) as process:
                process.stdin.write(json.dumps(dict(op='block', id=self.c.id, blocked=True)).encode() + b'\n')
                response = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
                self.assertFalse(response['ok'])
        self.assertFalse(self.a.blocked)
        self.assertIn(self.c.id, self.a.live())

    async def test_published_example_works_and_rejects_wrong_host_key(self):
        path = Path(__file__).resolve().parents[1] / 'src/tunnel/web/launcher-example.py'
        spec = importlib.util.spec_from_file_location('launcher_example', path)
        example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(example)
        code = 'buggnode1-' + base64.urlsafe_b64encode(json.dumps(self.a.profile).encode()).decode().rstrip('=')
        profile = example.decode_profile(code)
        result = await asyncio.wait_for(example.inspect(profile), 12)
        self.assertEqual(result['node'], 'A')
        self.assertEqual(result['page']['rooms'], [])
        with self.assertRaises(ValueError):
            await example.inspect(profile, 'missing_user')
        wrong = dict(profile, server_public_key=self.b.profile['server_public_key'])
        with self.assertRaises(asyncssh.HostKeyNotVerifiable):
            await example.inspect(wrong)


if __name__ == '__main__':
    unittest.main(verbosity=2)
