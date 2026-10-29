"""Real HTTPS/browser registration; intercept SMTP only, not account endpoints."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import re
import smtplib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from tunnel.provisioning import initialize
from tunnel.mail import VerificationMail
from tests.test_onboarding_browser import free_ports, async_playwright

spec = importlib.util.spec_from_file_location('registration_node', ROOT / 'src/main.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


@unittest.skipUnless(async_playwright, 'Install requirements-test.txt for browser tests')
class RegistrationBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        relay, web = free_ports()
        initialize(self.directory, '127.0.0.1', relay, web_port=web, name='Registration test')
        state = self.directory / 'node-state.json'
        value = json.loads(state.read_text()); value['registration_open'] = True; value['public_join'] = True
        state.write_text(json.dumps(value))
        mail = VerificationMail(self.directory / 'smtp.json', 'Registration test')
        mail.configure({'username': 'sender@example.com', 'password': 'test-smtp-secret'})
        self.messages = []
        self.smtp = patch.object(VerificationMail, '_send', lambda instance, message: self.messages.append(message))
        self.smtp.start(); self.addCleanup(self.smtp.stop)
        self.node = asyncio.create_task(entry.run(self.directory / 'server.conf'))
        self.addAsyncCleanup(self.stop_node)
        self.url = f'https://127.0.0.1:{web}'
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        executable = os.environ.get('BUGGCRAFT_TEST_BROWSER')
        if not executable and not Path(self.playwright.chromium.executable_path).exists():
            edge = Path(os.environ.get('ProgramFiles(x86)', '')) / 'Microsoft/Edge/Application/msedge.exe'
            if edge.exists(): executable = str(edge)
            else: self.skipTest('Install Chromium or set BUGGCRAFT_TEST_BROWSER')
        self.browser = await self.playwright.chromium.launch(headless=True, executable_path=executable)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(ignore_https_errors=True, accept_downloads=True,
                                                       viewport={'width': 390, 'height': 844})
        self.addAsyncCleanup(self.context.close)
        self.page = await self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        async with asyncio.timeout(10):
            while True:
                if self.node.done(): await self.node
                try:
                    response = await self.context.request.get(self.url + '/api/status')
                    if response.ok: break
                except Exception: pass
                await asyncio.sleep(.05)
        await self.page.goto(self.url)
        await self.page.locator('#open-account').click()
        await self.page.locator('#show-register').wait_for(state='visible')
        await self.page.locator('#show-register').click()

    async def stop_node(self):
        self.node.cancel()
        try: await self.node
        except asyncio.CancelledError: pass

    async def fill_identity(self):
        await self.page.locator('#register-name').fill('browser_user')
        await self.page.locator('#register-email').fill('user@example.com')

    async def test_slow_smtp_feedback_failure_retry_countdown_and_real_registration(self):
        from playwright.async_api import expect
        await self.fill_identity()
        release = threading.Event()
        def failing(instance, message):
            release.wait(5)
            raise smtplib.SMTPAuthenticationError(535, b'test-smtp-secret')
        try:
            with patch.object(VerificationMail, '_send', failing):
                await self.page.locator('#send-email-code').click()
                await expect(self.page.locator('#send-email-code')).to_have_text('正在发送…')
                await expect(self.page.locator('#send-email-code')).to_be_disabled()
                await expect(self.page.locator('#email-code-feedback')).to_contain_text('正在连接邮件服务')
                self.assertTrue(await self.page.locator('#register-email').get_attribute('readonly') is not None)
                release.set()
                await expect(self.page.locator('#email-code-feedback')).to_contain_text('发信账号验证失败')
                await expect(self.page.locator('#send-email-code')).to_be_enabled()
        finally: release.set()
        self.assertFalse(self.messages)
        await self.page.locator('#send-email-code').click()
        await expect(self.page.locator('#email-code-feedback')).to_contain_text('验证码已发往 user@example.com')
        await expect(self.page.locator('#send-email-code')).to_have_text(re.compile(r'\d+ 秒后重发'))
        self.assertEqual(len(self.messages), 1)
        code = re.search(r'\b\d{6}\b', self.messages[0].get_body(preferencelist=('plain',)).get_content()).group()
        await self.page.locator('#register-email-code').fill(code)
        password = 'browser test private password'
        await self.page.locator('#register-password').fill(password)
        await self.page.locator('#register-confirm').fill(password)
        async with self.page.expect_download() as download:
            await self.page.locator('#account-register button[type=submit]').click()
        await (await download.value).save_as(str(self.directory / 'recovery.json'))
        await expect(self.page.locator('#account-panel')).to_be_visible()
        await expect(self.page.locator('#account-feedback')).to_contain_text('注册成功')
        await self.page.locator('.account-nav [data-account-view=access]').click()
        await expect(self.page.locator('#node-address')).to_have_text(self.url)
        await expect(self.page.locator('#account-code-identity')).to_contain_text('browser_user')
        self.assertFalse(await self.page.locator('.legacy-access').evaluate('(element) => element.open'))
        await self.page.locator('.legacy-access summary').click()
        records = json.loads((self.directory / 'accounts-replica.json').read_text())
        self.assertEqual([r['certificate']['username'] for r in records], ['browser_user'])
        self.assertNotIn(password, json.dumps(records))
        await self.page.locator('#account-connect').click()
        await expect(self.page.locator('#player-code')).to_have_value(re.compile(r'^buggnode1-'))
        await self.page.locator('.account-nav [data-account-view=overview]').click()
        await self.page.locator('#account-logout').click()
        await self.page.locator('#account-id').fill('browser_user')
        await self.page.locator('#account-password').fill(password)
        await self.page.locator('#account-login button[type=submit]').click()
        await expect(self.page.locator('#account-panel')).to_be_visible()
        self.assertFalse(self.errors, self.errors)

    async def test_invalid_input_does_not_send_and_editing_identity_invalidates_ticket(self):
        from playwright.async_api import expect
        await self.page.locator('#send-email-code').click()
        self.assertEqual(self.messages, [])
        await self.fill_identity()
        await self.page.locator('#send-email-code').click()
        await expect(self.page.locator('#email-code-feedback')).to_contain_text('验证码已发往')
        await self.page.locator('#register-email').fill('changed@example.com')
        await expect(self.page.locator('#email-code-feedback')).to_contain_text('请重新发送验证码')
        await self.page.locator('#register-email-code').fill('123456')
        await self.page.locator('#register-password').fill('browser test private password')
        await self.page.locator('#register-confirm').fill('browser test private password')
        await self.page.locator('#account-register button[type=submit]').click()
        await expect(self.page.locator('#account-feedback')).to_contain_text('请先发送并填写邮箱验证码')
        self.assertFalse((self.directory / 'accounts-replica.json').exists())
        self.assertFalse(self.errors, self.errors)

    async def test_network_failure_is_visible_and_send_button_recovers(self):
        from playwright.async_api import expect
        await self.fill_identity()
        await self.page.route('**/api/account/email-code', lambda route: route.abort())
        await self.page.locator('#send-email-code').click()
        await expect(self.page.locator('#email-code-feedback')).to_contain_text('无法连接节点')
        await expect(self.page.locator('#send-email-code')).to_be_enabled()
        self.assertFalse(self.messages)
        await self.page.unroute('**/api/account/email-code')
        await self.page.locator('#send-email-code').click()
        await expect(self.page.locator('#email-code-feedback')).to_contain_text('验证码已发往')
        self.assertEqual(len(self.messages), 1)
        self.assertFalse(self.errors, self.errors)

    async def register_page(self,page,username):
        from playwright.async_api import expect
        await page.goto(self.url+'/register')
        await page.locator('#account-register').wait_for(state='visible')
        await page.locator('#register-name').fill(username)
        await page.locator('#register-email').fill(username+'@example.com')
        await page.locator('#send-email-code').click()
        await expect(page.locator('#email-code-feedback')).to_contain_text('验证码已发往')
        message=next(m for m in reversed(self.messages) if m['To']==username+'@example.com')
        code=re.search(r'\b\d{6}\b',message.get_body(preferencelist=('plain',)).get_content()).group()
        await page.locator('#register-email-code').fill(code)
        await page.locator('#register-password').fill('a distinct browser test password')
        await page.locator('#register-confirm').fill('a distinct browser test password')
        await page.locator('#account-register button[type=submit]').click()
        await expect(page.locator('#account-panel')).to_be_visible()

    async def test_friends_ui_search_request_consent_and_remove(self):
        from playwright.async_api import expect
        await self.register_page(self.page,'alice')
        other=await self.browser.new_context(ignore_https_errors=True,accept_downloads=True)
        self.addAsyncCleanup(other.close)
        bob=await other.new_page();bob.on('pageerror',lambda e:self.errors.append(str(e)))
        await self.register_page(bob,'bob')
        await self.page.locator('.account-nav [data-account-view=friends]').click()
        await self.page.locator('#friend-username').fill('bob')
        await self.page.locator('#friend-search button').click()
        await expect(self.page.locator('#friend-results')).to_contain_text('bob')
        await self.page.locator('#friend-results button').click()
        await expect(self.page.locator('#friend-requests')).to_contain_text('撤回申请')
        await expect(self.page.locator('#friend-list')).to_contain_text('还没有好友')
        await bob.locator('.account-nav [data-account-view=friends]').click()
        await expect(bob.locator('#friend-requests')).to_contain_text('alice',timeout=10000)
        await bob.locator('#friend-requests button').click()
        await expect(bob.locator('#friend-list')).to_contain_text('alice')
        await expect(self.page.locator('#friend-list')).to_contain_text('bob',timeout=10000)
        await self.page.locator('#friend-list button').click()
        await expect(self.page.locator('#friend-list')).to_contain_text('还没有好友')
        await expect(bob.locator('#friend-list')).to_contain_text('还没有好友',timeout=10000)
        self.assertFalse(self.errors,self.errors)

    async def test_anonymous_entry_and_reload_never_issue_a_mislabelled_personal_code(self):
        from playwright.async_api import expect
        await self.page.goto(self.url+'/connect')
        await expect(self.page.locator('.node-address-row code')).to_have_text(self.url)
        self.assertFalse(await self.page.locator('.legacy-access').evaluate('(element) => element.open'))
        await self.page.locator('.legacy-access summary').click()
        await expect(self.page.locator('#connect-form')).to_contain_text('旧版访客接入码')
        self.assertEqual(await self.page.locator('#account-dialog').count(),0)
        await self.page.locator('#connect-form button[type=submit]').click()
        await expect(self.page.locator('#player-code')).to_have_value(re.compile('^buggnode1-'))
        await self.register_page(self.page,'alice')
        await self.page.locator('.account-nav [data-account-view=access]').click()
        await self.page.locator('.legacy-access summary').click()
        await self.page.locator('#account-connect').click()
        await expect(self.page.locator('#personal-result')).to_be_visible()
        profile=json.loads(__import__('base64').urlsafe_b64decode((await self.page.locator('#player-code').input_value())[10:]+'==='))
        self.assertTrue(profile['token'].startswith('acct1.'));self.assertIn('~',profile['token'])
        await self.page.reload()
        await expect(self.page.locator('#node-address')).to_have_text(self.url)
        self.assertEqual(await self.page.locator('#player-code').input_value(),'')
        await self.page.locator('.legacy-access summary').click()
        await self.page.locator('#account-connect').click()
        await expect(self.page.locator('#account-unlock-dialog')).to_be_visible()
        await self.page.locator('#account-unlock-cancel').click()
        self.assertEqual(await self.page.locator('#player-code').input_value(),'')
        await self.page.locator('#account-connect').click()
        await self.page.locator('#account-unlock-password').fill('a distinct browser test password')
        await self.page.locator('#account-unlock-form button[type=submit]').click()
        await expect(self.page.locator('#player-code')).to_have_value(re.compile('^buggnode1-'))
        self.assertFalse(self.errors,self.errors)

    async def test_launcher_browser_login_approval_refresh_and_revoke(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from playwright.async_api import expect
        from tests.test_account_replication import public, sign

        await self.register_page(self.page, 'launcher_user')
        launcher = await self.playwright.request.new_context(ignore_https_errors=True)
        self.addAsyncCleanup(launcher.dispose)
        key = ec.generate_private_key(ec.SECP256R1())
        started = await launcher.post(self.url + '/api/launcher/start', data={
            'key': public(key), 'name': 'Browser integration launcher', 'remember': True})
        self.assertTrue(started.ok, await started.text())
        request = await started.json()
        self.assertEqual(request['verification_uri'], self.url + '/account/access?launcher=' + request['id'])
        poll = {'id': request['id'], 'signature': sign(key, {
            'p': 'buggcraft-launcher-poll-v1', 'id': request['id'], 'nonce': request['nonce']})}
        pending = await launcher.post(self.url + '/api/launcher/poll', data=poll)
        self.assertEqual((await pending.json())['status'], 'pending')

        await self.page.locator('#account-logout').click()
        await self.page.goto(request['verification_uri'])
        await expect(self.page.locator('#account-login')).to_be_visible()
        await self.page.reload()
        await expect(self.page.locator('#account-login')).to_be_visible()
        await self.page.locator('#account-id').fill('launcher_user')
        await self.page.locator('#account-password').fill('a distinct browser test password')
        await self.page.locator('#account-login button[type=submit]').click()
        await expect(self.page.locator('#launcher-approval')).to_be_visible()
        await expect(self.page.locator('#launcher-device-name')).to_have_text('Browser integration launcher')
        await expect(self.page).to_have_url(request['verification_uri'])
        await self.page.reload()
        await expect(self.page.locator('#launcher-device-name')).to_have_text('Browser integration launcher')
        await self.page.locator('#launcher-approve').click()
        await expect(self.page.locator('#account-unlock-dialog')).to_be_visible()
        await self.page.locator('#account-unlock-password').fill('a distinct browser test password')
        await self.page.locator('#account-unlock-form button[type=submit]').click()
        await expect(self.page.locator('#launcher-device-feedback')).to_contain_text('已授权，请返回启动器')
        approved = await launcher.post(self.url + '/api/launcher/poll', data=poll)
        self.assertTrue(approved.ok, await approved.text())
        profile = await approved.json()
        self.assertEqual(profile['status'], 'approved')
        self.assertTrue(profile['profile']['token'].startswith('acct1.'))
        self.assertEqual(profile['name'], 'Registration test')

        async def refresh():
            challenge = await launcher.post(self.url + '/api/launcher/challenge', data={'id': request['id']})
            self.assertTrue(challenge.ok, await challenge.text())
            message = (await challenge.json())['message']
            return await launcher.post(self.url + '/api/launcher/refresh', data={
                'nonce': message['nonce'], 'signature': sign(key, message)})

        renewed = await refresh()
        self.assertTrue(renewed.ok, await renewed.text())
        await self.page.goto(self.url + '/account/security')
        await expect(self.page.locator('#launcher-device-list')).to_contain_text('Browser integration launcher')
        await self.page.locator('#launcher-device-list button').click()
        await expect(self.page.locator('#launcher-device-list')).to_contain_text('已失效')
        revoked = await refresh()
        self.assertEqual(revoked.status, 401, await revoked.text())
        self.assertFalse(self.errors, self.errors)
