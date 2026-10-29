"""Real browser registration/email selection, encrypted Chinese vault and launcher codes.

Uses an isolated HTTP/SSH fixture; only the SMTP transport is replaced by an
in-memory mailbox. No production mail or player data is touched.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'web'), str(ROOT.parent / '.test-tools')]
from playwright.async_api import async_playwright, expect
from tests.test_email_accounts import EmailAccountTests


async def main():
    fixture = EmailAccountTests()
    await fixture.asyncSetUp()
    errors = []
    try:
        first, _ = await fixture.register(0, name='same_user')
        second, _ = await fixture.register(1, name='same_user')
        # The browser registers the third identity while every peer is offline.
        for fed in fixture.feds:
            await fed.sync_once()
        async with async_playwright() as p:
            executable = Path(p.chromium.executable_path)
            if not executable.exists():
                executable = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
            browser = await p.chromium.launch(headless=True, executable_path=str(executable))
            context = await browser.new_context(accept_downloads=True, viewport=dict(width=1280, height=1100))
            page = await context.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            base = str(fixture.clients[0].make_url('/')).rstrip('/')
            await page.goto(base + '/register')
            await page.locator('#register-name').fill('same_user')
            await page.locator('#register-email').fill('owner@qq.com')
            password = '中文长密码-用于浏览器联调-123456'
            await page.locator('#register-password').fill(password)
            await page.locator('#register-confirm').fill(password)
            fixture.portals[0].account_api.mail.recent.clear()
            await page.locator('#send-email-code').click()
            await expect(page.locator('#email-code-feedback')).to_contain_text('验证码已发往')
            message = fixture.messages[-1].get_body(preferencelist=('plain',)).get_content()
            code = re.search(r'(?<!\d)\d{6}(?!\d)', message).group()
            await page.locator('#register-email-code').fill(code)
            async with page.expect_download():
                await page.locator('#account-register button[type=submit]').click()
            await expect(page.locator('#account-panel')).to_be_visible(timeout=20000)
            third = await page.locator('#account-current').text_content()
            assert third not in (first, second)
            # Chinese profile round-trip through real PBKDF2/AES-GCM and signatures.
            await page.evaluate("accountView('profile')")
            await page.locator('#account-nickname').fill('中文昵称：重庆朋友😀')
            await page.locator('#account-notes').fill('中文备忘：上海、宿迁、重庆')
            await page.locator('#account-save').click()
            await expect(page.locator('#account-feedback')).to_contain_text('加密保存')
            await page.evaluate("accountView('security')")
            await page.locator('#account-logout').click()
            await page.locator('#account-id').fill('OWNER@QQ.COM')
            fixture.portals[0].account_api.mail.recent.clear()
            await page.locator('#send-login-email').click()
            await expect(page.locator('#login-email-feedback')).to_contain_text('验证码已发往')
            message = fixture.messages[-1].get_body(preferencelist=('plain',)).get_content()
            code = re.search(r'(?<!\d)\d{6}(?!\d)', message).group()
            await page.locator('#login-email-code').fill(code)
            await page.locator('#verify-login-email').click()
            await expect(page.locator('#email-account-picker')).to_be_visible()
            assert await page.locator('#email-account-choice option').count() == 4
            await page.locator('#email-account-choice').select_option(third)
            await page.locator('#account-password').fill(password + 'wrong')
            await page.locator('#account-login button[type=submit]').click()
            await expect(page.locator('#account-feedback')).to_contain_text('密码错误')
            await page.locator('#account-password').fill(password)
            await page.locator('#account-login button[type=submit]').click()
            await expect(page.locator('#account-panel')).to_be_visible(timeout=20000)
            assert await page.locator('#account-current').text_content() == third
            assert await page.locator('#account-nickname').input_value() == '中文昵称：重庆朋友😀'
            # Unlock by exact ID after a session-only reload, never ambiguous name.
            await page.reload()
            await expect(page.locator('#account-panel')).to_be_visible()
            await page.locator('#account-unlock').click()
            assert await page.locator('#account-id').input_value() == third
            await page.locator('#account-password').fill(password)
            await page.locator('#account-login button[type=submit]').click()
            await expect(page.locator('#account-panel')).to_be_visible()
            await page.evaluate("accountView('access')")
            await page.locator('#account-connect').click()
            await expect(page.locator('#personal-result')).to_be_visible()
            code1 = await page.locator('#player-code').input_value()
            assert code1.startswith('buggnode1-')
            import base64
            profile = json.loads(base64.urlsafe_b64decode(code1.split('-', 1)[1] + '==='))
            account = fixture.portals[0].account_api.store.authenticate(profile['token'])
            assert account == third
            await page.locator('#access-code-format').select_option('2')
            await page.locator('#account-connect').click()
            await expect(page.locator('#player-code')).to_have_value(re.compile(r'buggnode2-.+~.+'))
            await page.evaluate("accountView('friends')")
            await page.locator('#friend-username').fill(first)
            await page.locator('#friend-search button').click()
            await expect(page.locator('#friend-results')).to_contain_text(first)
            assert not errors, errors
            # Selection is replicated and survives a node process reload.
            for fed in fixture.feds:
                await fed.sync_once()
            selected = fixture.portals[1].account_api.store.email_accounts.candidates(
                fixture.portals[1].account_api.store.email_accounts.mailbox('owner@qq.com'))
            assert selected[0]['account'] == third and selected[0]['preferred']
            await context.close()
            await browser.close()
            print(json.dumps(dict(registration=True, duplicate_candidates=3, wrong_password_rejected=True,
                chinese_vault=True, exact_id_unlock=True, launcher_codes=[1,2], friend_lookup_id=True,
                choice_replicated=True, browser_errors=errors)))
    finally:
        await fixture.asyncTearDown()
        fixture.doCleanups()


if __name__ == '__main__':
    asyncio.run(main())
