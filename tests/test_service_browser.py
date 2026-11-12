"""Real WebCrypto account flow and minimal control web at desktop/mobile widths."""
import os
import re
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

from playwright.async_api import async_playwright, expect
from cryptography.hazmat.primitives.asymmetric import ec

from tests.service_fixture import ServiceFixture
from tests.test_email_accounts import public, sign
from tunnel.accounts import canonical


class ServiceBrowserTests(ServiceFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await ServiceFixture.asyncSetUp(self)
        self.playwright = await async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        executable = os.environ.get('BUGGCRAFT_TEST_BROWSER')
        if not executable and not Path(self.playwright.chromium.executable_path).exists():
            executable = str(Path(os.environ['ProgramFiles(x86)'])/'Microsoft/Edge/Application/msedge.exe')
        self.browser = await self.playwright.chromium.launch(headless=True, executable_path=executable)
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(ignore_https_errors=True, viewport={'width':390,'height':844})
        self.addAsyncCleanup(self.context.close)
        self.page = await self.context.new_page()
        self.errors, self.downloads = [], []
        self.page.on('pageerror', lambda e:self.errors.append(str(e)))
        self.page.on('download', lambda d:self.downloads.append(d.suggested_filename))
        self.base = str(self.server.make_url('')).rstrip('/')
        self.authority.identity_authority.authorize = AsyncMock()
        self.authority.identity_authority.revoke = AsyncMock()
        self.artifacts = Path(__file__).resolve().parents[1]/'validation-artifacts/service-web'
        self.artifacts.mkdir(parents=True, exist_ok=True)

    async def test_registration_device_approval_reload_unlock_and_revoke(self):
        page=self.page
        await page.goto(self.base+'/register')
        await page.locator('#account-register').wait_for(state='visible')
        await page.locator('#register-name').fill('minimal_user')
        await page.locator('#register-password').fill('a-long-test-password')
        await page.locator('#register-confirm').fill('a-long-test-password')
        await page.locator('#account-register button[type=submit]').click()
        await expect(page.locator('#welcome-name')).to_have_text('minimal_user')
        self.assertEqual(self.downloads,[])

        self.assertEqual(len(self.authority.account_api.store.records),1)
        record=next(iter(self.authority.account_api.store.records.values()))
        self.assertNotIn('routing',record)
        await expect(page.locator('body')).not_to_contain_text('安全与备份')
        await expect(page.locator('body')).not_to_contain_text('备份节点')
        for width in (390,768,1280,1920):
            await page.set_viewport_size({'width':width,'height':900})
            await page.screenshot(path=str(self.artifacts/f'account-{width}.png'),full_page=True)
            self.assertTrue(await page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width)
        await page.locator('[data-account-view="access"]').click()
        await expect(page.locator('#node-address')).to_have_text(self.base)
        key=ec.generate_private_key(ec.SECP256R1())
        response=await self.context.request.post(self.base+'/api/launcher/start',data={
            'key':public(key),'name':'Browser acceptance device','remember':True})
        self.assertTrue(response.ok,await response.text())
        start=await response.json()
        await page.goto(self.base+'/launcher-login?launcher='+start['id'])
        await expect(page.locator('#launcher-approval')).to_be_visible()
        await page.locator('#launcher-approve').click()
        await expect(page.locator('#account-unlock-dialog')).to_be_visible()
        await page.locator('#account-unlock-password').fill('a-long-test-password')
        await page.locator('#account-unlock-form button[type=submit]').click()
        await expect(page.locator('#launcher-device-feedback')).to_contain_text('已授权')
        await page.screenshot(path=str(self.artifacts/'approval.png'),full_page=True)
        message=dict(p='buggcraft-launcher-poll-v1',id=start['id'],nonce=start['nonce'])
        response=await self.context.request.post(self.base+'/api/launcher/poll',data={
            'id':start['id'],'signature':sign(key,canonical(message))})
        self.assertTrue(response.ok,await response.text())
        self.assertEqual((await response.json())['status'],'approved')
        self.authority.identity_authority.authorize.assert_awaited_once()
        await page.goto(self.base+'/account/devices')
        await expect(page.locator('#launcher-device-list')).to_contain_text('Browser acceptance device')
        await page.get_by_role('button',name='撤销登录').click()
        await expect(page.locator('#launcher-device-list')).to_contain_text('已失效')
        self.authority.identity_authority.revoke.assert_awaited_once()
        await page.screenshot(path=str(self.artifacts/'devices.png'),full_page=True)
        await page.goto(self.base+'/account')
        await page.locator('#account-logout').click()
        await expect(page.locator('#account-login')).to_be_visible()
        await page.locator('#account-id').fill('minimal_user')
        await page.locator('#account-password').fill('incorrect-password')
        await page.locator('#account-login button[type=submit]').click()
        await expect(page.locator('#account-feedback')).to_contain_text('密码错误')
        await expect(page.locator('#account-id')).to_have_value('minimal_user')
        await page.screenshot(path=str(self.artifacts/'login-failure.png'),full_page=True)
        await page.locator('#account-password').fill('a-long-test-password')
        await page.locator('#account-login button[type=submit]').click()
        await expect(page.locator('#welcome-name')).to_have_text('minimal_user')
        await expect(page.locator('#account-id')).to_have_value('minimal_user')
        self.assertEqual(self.errors,[])
        self.assertEqual(self.downloads,[])

    async def test_verified_mail_registration_and_mailbox_login(self):
        mail=self.authority.account_api.mail
        mail.configure(dict(username='sender@example.com',password='test-smtp-secret',required=True))
        messages=[]
        mail._send=messages.append
        page=self.page
        await page.goto(self.base+'/register')
        await page.locator('#register-email').wait_for(state='visible')
        await page.locator('#register-name').fill('mail_user')
        await page.locator('#register-email').fill('user@example.com')
        await page.locator('#send-email-code').click()
        await expect(page.locator('#email-code-feedback')).to_contain_text('验证码已发往')
        code=re.search(r'\b\d{6}\b',messages[-1].get_body(preferencelist=('plain',)).get_content()).group()
        await page.locator('#register-email-code').fill(code)
        await page.locator('#register-password').fill('mail-test-long-password')
        await page.locator('#register-confirm').fill('mail-test-long-password')
        await page.locator('#account-register button[type=submit]').click()
        await expect(page.locator('#welcome-name')).to_have_text('mail_user')
        await page.locator('#account-logout').click()
        await expect(page.locator('#account-login')).to_be_visible()
        await page.locator('#account-id').fill('user@example.com')
        # Simulate expiry of the fixture mailbox delivery cooldown.
        mail.recent={key:at-61 for key,at in mail.recent.items()}
        await page.locator('#send-login-email').click()
        await expect(page.locator('#login-email-feedback')).to_contain_text('验证码已发往')
        code=re.search(r'\b\d{6}\b',messages[-1].get_body(preferencelist=('plain',)).get_content()).group()
        await page.locator('#login-email-code').fill(code)
        await page.locator('#verify-login-email').click()
        await expect(page.locator('#email-account-picker')).to_be_visible()
        await page.locator('#account-password').fill('mail-test-long-password')
        await page.locator('#account-login button[type=submit]').click()
        await expect(page.locator('#welcome-name')).to_have_text('mail_user')
        self.assertEqual(self.errors,[])
        self.assertEqual(self.downloads,[])
