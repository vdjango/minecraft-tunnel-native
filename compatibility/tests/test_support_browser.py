"""Browser workflows against a real TLS node and persisted log attachments."""
import json
import io
import os
from pathlib import Path
import re
import unittest
import zipfile
from tests import test_registration_browser as fixtures


@unittest.skipUnless(fixtures.async_playwright,'Install requirements-test.txt for browser tests')
class SupportBrowserTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.RegistrationBrowserTests.asyncSetUp
    stop_node=fixtures.RegistrationBrowserTests.stop_node
    register_page=fixtures.RegistrationBrowserTests.register_page

    async def admin_page(self):
        context=await self.browser.new_context(ignore_https_errors=True,accept_downloads=True)
        self.addAsyncCleanup(context.close)
        page=await context.new_page();page.on('pageerror',lambda e:self.errors.append(str(e)))
        await page.goto(self.url+'/admin/users')
        credentials=(self.directory/'admin-credentials.txt').read_text(encoding='utf-8')
        password=re.search(r'^Admin password: (.+)$',credentials,re.M).group(1)
        await page.locator('#admin-password').fill(password)
        await page.locator('#login-form button[type=submit]').click()
        await page.locator('#admin-panel').wait_for(state='visible')
        return context,page

    async def test_issue_submission_upload_admin_receipt_resolution_and_public_audit(self):
        from playwright.async_api import expect
        await self.register_page(self.page,'reporter')
        await self.page.goto(self.url+'/feedback')
        await self.page.locator('#new-issue').click()
        await self.page.locator('#issue-title').fill('死亡重生时短暂卡住')
        await self.page.locator('#issue-description').fill('在同一个房间，重生玩家卡住约两秒，其他玩家正常。')
        await self.page.locator('#issue-game').fill('1.21.1')
        await self.page.locator('#issue-loader').fill('原版')
        await self.page.locator('#issue-system').fill('Windows 10')
        await self.page.locator('#issue-create button').click()
        await expect(self.page.locator('#detail-title')).to_have_text('死亡重生时短暂卡住')
        issue=self.page.url.rsplit('/',1)[-1]
        await self.page.locator('#attachment-file').set_input_files({'name':'game.log','mimeType':'text/plain','buffer':b'private-player-diagnostics'})
        await self.page.locator('#attachment-form button').click()
        await expect(self.page.locator('#upload-feedback')).to_contain_text('上传完成')
        await expect(self.page.locator('#detail-attachments')).to_contain_text('game.log')
        _,admin=await self.admin_page()
        await expect(admin.locator('#admin-accounts')).to_contain_text('reporter')
        await expect(admin.locator('#admin-accounts')).to_contain_text('已同步')
        await admin.locator('.console-nav a[href="/admin/feedback"]').click()
        await admin.locator('.issue-row').click()
        await expect(admin.locator('#detail-title')).to_have_text('死亡重生时短暂卡住')
        async with admin.expect_download() as download:
            await admin.locator('#detail-attachments a').click()
        path=await (await download.value).path()
        self.assertEqual(path.read_bytes(),b'private-player-diagnostics')
        await admin.locator('#status-message').fill('已受理，正在复现重生流程。')
        await admin.locator('#status-form button').click()
        await expect(admin.locator('#detail-status')).to_have_text('已受理')
        await admin.locator('#status-value').select_option('resolved')
        await admin.locator('#status-message').fill('已修复并通过重生回归验证。')
        await admin.locator('#status-form button').click()
        await expect(admin.locator('#detail-status')).to_have_text('已完成')
        self.assertFalse(list((self.directory/'feedback'/issue).glob('*.bin')))
        await self.page.reload()
        await expect(self.page.locator('#issue-history')).to_contain_text('已修复并通过重生回归验证')
        await expect(self.page.locator('#detail-attachments')).to_contain_text('已删除')
        await expect(self.page.locator('#attachment-form')).to_be_hidden()
        self.assertFalse(self.errors,self.errors)

    async def test_admin_diagnostics_and_page_navigation_do_not_overflow_mobile(self):
        from playwright.async_api import expect
        await self.register_page(self.page,'player')
        _,admin=await self.admin_page()
        await admin.set_viewport_size({'width':390,'height':844})
        await admin.locator('.console-nav a[href="/admin/diagnostics"]').click()
        await expect(admin.locator('#diagnostic-panel')).to_be_visible()
        await expect(admin.locator('#journal-health')).to_contain_text('等待玩家连接')
        self.assertFalse(await admin.evaluate('document.documentElement.scrollWidth>innerWidth'))
        await self.page.goto(self.url+'/account')
        for view,title in [('access','连接启动器'),('friends','我的好友'),('profile','个人资料'),('security','安全与备份')]:
            await self.page.locator('.account-nav [data-account-view='+view+']').click()
            await expect(self.page.locator('#page-title')).to_have_text(title)
            self.assertFalse(await self.page.evaluate('document.documentElement.scrollWidth>innerWidth'),view)
        self.assertFalse(self.errors,self.errors)

    async def test_user_center_report_entry_creates_issue_with_initial_zip(self):
        from playwright.async_api import expect
        await self.register_page(self.page,'report_zip')
        await self.page.locator('#account-feedback-entry').click()
        await expect(self.page.locator('#new-issue')).to_be_visible()
        await self.page.locator('#my-issues').click()
        await expect(self.page.locator('#my-issues')).to_have_attribute('aria-selected','true')
        await self.page.locator('#new-issue').click()
        await self.page.locator('#issue-title').fill('连续联机后的短暂卡顿')
        await self.page.locator('#issue-description').fill('三名玩家同时进入新的区块后，出现了两秒卡顿。')
        await self.page.locator('#issue-game').fill('1.21.8')
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr('game.log','test player diagnostic log')
        await self.page.locator('#issue-file').set_input_files(dict(name='diagnostics.zip',mimeType='application/zip',buffer=buffer.getvalue()))
        await self.page.locator('#issue-create button[type=submit]').click()
        await expect(self.page.locator('#detail-title')).to_have_text('连续联机后的短暂卡顿')
        await expect(self.page.locator('#detail-attachments')).to_contain_text('diagnostics.zip')
        await expect(self.page.locator('#feedback-message')).to_contain_text('问题和日志已提交')
        self.assertFalse(self.errors,self.errors)

    async def test_diagnostics_displays_real_game_session_filters_and_exports_it(self):
        import asyncio
        import secrets
        import asyncssh
        from tunnel.relay import room_id
        from playwright.async_api import expect

        profile=json.loads((self.directory/'relay-profile.json').read_text())
        secret=secrets.token_urlsafe(32)
        async def echo(reader,writer):
            try:
                while data:=await reader.read(65536):
                    writer.write(data);await writer.drain()
            finally:
                writer.close();await writer.wait_closed()
        server=await asyncio.start_server(echo,'127.0.0.1',0)
        self.addAsyncCleanup(server.wait_closed);self.addCleanup(server.close)
        async def connect(role):
            connection=await asyncssh.connect(profile['server_host'],profile['server_port'],
                username=role+':'+room_id(secret),password=json.dumps(dict(token=profile['token'],secret=secret)),
                known_hosts=([asyncssh.import_public_key(profile['server_public_key'])],[],[]),
                client_keys=[],agent_path=None,config=None)
            self.addAsyncCleanup(connection.wait_closed);self.addCleanup(connection.close)
            return connection
        host=await connect('host')
        reverse=await host.forward_remote_port('127.0.0.1',0,'127.0.0.1',server.sockets[0].getsockname()[1])
        self.addCleanup(reverse.close)
        client=await connect('client')
        reader,writer=await client.open_connection(room_id(secret),25565)
        writer.write(b'diagnostic transport payload');await writer.drain()
        self.assertEqual(await reader.readexactly(28),b'diagnostic transport payload')
        writer.close();await writer.wait_closed()
        client.close();await client.wait_closed()
        _,admin=await self.admin_page()
        await admin.goto(self.url+'/admin/diagnostics')
        await expect(admin.locator('#session-detail')).to_be_visible()
        await expect(admin.locator('#event-list')).to_contain_text('转发流')
        await expect(admin.locator('#findings')).to_contain_text('断开原因')
        self.assertEqual(await admin.locator('#replay-position').input_value(),
                         await admin.locator('#replay-position').get_attribute('max'))
        await admin.locator('#replay-position').press('Home')
        await expect(admin.locator('#replay-position')).to_have_value('0')
        await admin.evaluate('loadSessions()')
        await expect(admin.locator('#replay-position')).to_have_value('0')
        await admin.locator('#player-filter').fill('no_matching_player')
        await expect(admin.locator('#session-detail')).to_be_hidden()
        await admin.locator('#player-filter').fill('匿名玩家')
        await expect(admin.locator('#session-detail')).to_be_visible()
        async with admin.expect_download() as download:
            await admin.locator('#export-diagnostics').click()
        path=await (await download.value).path()
        exported=json.loads(path.read_text(encoding='utf-8'))
        self.assertTrue(any(e['event']=='stream_closed' for e in exported['events']))
        self.assertTrue(any(e['event']=='disconnected' for e in exported['events']))
        self.assertNotIn(profile['token'],json.dumps(exported))
        self.assertNotIn('diagnostic transport payload',json.dumps(exported))
        self.assertFalse(self.errors,self.errors)

    async def test_login_returns_to_node_address_or_issue_submission(self):
        from playwright.async_api import expect
        await self.register_page(self.page,'return_user')
        await self.page.locator('#account-logout').click()
        for target,selector in [('/account/access','#node-address'),('/feedback?new=1','#issue-create')]:
            await self.page.goto(self.url+'/login?next='+target)
            await self.page.locator('#account-id').fill('return_user')
            await self.page.locator('#account-password').fill('a distinct browser test password')
            await self.page.locator('#account-login button[type=submit]').click()
            await expect(self.page.locator(selector)).to_be_visible()
            await expect(self.page).to_have_url(self.url+target)
            await self.page.goto(self.url+'/account')
            await self.page.locator('#account-logout').click()
        self.assertFalse(self.errors,self.errors)

    async def test_all_admin_sections_and_network_fit_mobile_and_desktop(self):
        from playwright.async_api import expect
        await self.register_page(self.page,'layout_user')
        _,admin=await self.admin_page()
        directory=os.environ.get('BUGGCRAFT_WEB_SCREENSHOTS')
        if directory:Path(directory).mkdir(parents=True,exist_ok=True)
        for width in (390,1440):
            await admin.set_viewport_size(dict(width=width,height=1000))
            await self.page.set_viewport_size(dict(width=width,height=1000))
            for section in ('nodes','settings','users','mail','feedback','diagnostics'):
                await admin.goto(self.url+'/admin/'+section)
                await expect(admin.locator('.console-nav')).to_be_visible()
                if section in ('nodes','settings','users','mail'):
                    await expect(admin.locator('#admin-bandwidth')).to_be_visible()
                self.assertFalse(await admin.evaluate('document.documentElement.scrollWidth>innerWidth'),(section,width))
                if directory:await admin.screenshot(path=str(Path(directory)/f'admin-{section}-{width}.png'),full_page=True)
            for section in ('access','friends','security'):
                await self.page.goto(self.url+'/account/'+section)
                await expect(self.page.locator('#account-panel')).to_be_visible()
                self.assertFalse(await self.page.evaluate('document.documentElement.scrollWidth>innerWidth'),(section,width))
                if directory:await self.page.screenshot(path=str(Path(directory)/f'account-{section}-{width}.png'),full_page=True)
            await self.page.goto(self.url+'/network')
            await expect(self.page.locator('#network-rows')).to_contain_text('Registration test')
            self.assertFalse(await self.page.evaluate('document.documentElement.scrollWidth>innerWidth'),('network',width))
        self.assertFalse(self.errors,self.errors)

    async def test_theme_switch_persists_across_pages_reload_and_tabs(self):
        from playwright.async_api import expect
        await expect(self.page.locator('html')).to_have_attribute('data-theme','dark')
        dark=await self.page.evaluate('getComputedStyle(document.body).backgroundColor')
        self.assertNotEqual(dark,'rgb(245, 247, 250)')
        await self.page.get_by_role('button',name='切换到浅色主题').click()
        for path in ('/','/login','/account','/connect','/admin/users','/admin/diagnostics','/feedback','/developers'):
            await self.page.goto(self.url+path)
            await expect(self.page.locator('html')).to_have_attribute('data-theme','light')
            await expect(self.page.get_by_role('button',name='切换到深色主题')).to_be_visible()
            self.assertNotEqual(await self.page.evaluate('getComputedStyle(document.body).backgroundColor'),dark,path)
            overflow=await self.page.evaluate('''() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,
              offenders:[...document.querySelectorAll('body *')].filter(el=>el.getBoundingClientRect().right>innerWidth+1)
                .slice(0,6).map(el=>[el.tagName,el.className,Math.round(el.getBoundingClientRect().right)])})''')
            self.assertLessEqual(overflow['scroll'],overflow['width'],(path,overflow))
        other=await self.context.new_page()
        self.addAsyncCleanup(other.close)
        await other.goto(self.url+'/login')
        light_code=await self.page.locator('.docs-code').first.evaluate('el=>getComputedStyle(el).backgroundColor')
        await self.page.get_by_role('button',name='切换到深色主题').click()
        await expect(other.locator('html')).to_have_attribute('data-theme','dark')
        code=self.page.locator('.docs-code').first
        self.assertNotEqual(await code.evaluate('el=>getComputedStyle(el).backgroundColor'),light_code)
        await other.reload()
        await expect(other.locator('html')).to_have_attribute('data-theme','dark')
        self.assertEqual(await other.evaluate('getComputedStyle(document.body).backgroundColor'),dark)
        self.assertEqual(await other.evaluate('localStorage.getItem("buggcraft.theme")'),'dark')
        self.assertFalse(self.errors,self.errors)

    async def test_theme_still_switches_when_browser_storage_is_disabled(self):
        from playwright.async_api import expect
        await self.context.add_init_script('Object.defineProperty(window,"localStorage",{get(){throw new Error("storage disabled")}})')
        await self.page.goto(self.url+'/login')
        await expect(self.page.locator('html')).to_have_attribute('data-theme','dark')
        await self.page.get_by_role('button',name='切换到浅色主题').click()
        await expect(self.page.locator('html')).to_have_attribute('data-theme','light')
        await self.page.reload()
        await expect(self.page.locator('html')).to_have_attribute('data-theme','dark')
        self.assertFalse(self.errors,self.errors)
