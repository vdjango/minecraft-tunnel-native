"""Optional real-browser tests; HTTP/security tests do not require Playwright."""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from tunnel.onboarding import SetupServer

spec = importlib.util.spec_from_file_location('browser_node_entry', ROOT / 'src/main.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)

try:
    from playwright.async_api import async_playwright, expect
except ImportError:
    async_playwright = None


def free_ports():
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0))
        second.bind(('127.0.0.1', 0))
        return first.getsockname()[1], second.getsockname()[1]


@unittest.skipUnless(async_playwright, 'Install requirements-test.txt for browser tests')
class OnboardingBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_desktop_and_mobile_wizard_then_admin_login_and_inline_protocol(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch('builtins.print'):
            playwright = await async_playwright().start()
            self.addAsyncCleanup(playwright.stop)
            executable = os.environ.get('BUGGCRAFT_TEST_BROWSER')
            if not executable and not Path(playwright.chromium.executable_path).exists():
                edge = Path(os.environ.get('ProgramFiles(x86)', '')) / 'Microsoft/Edge/Application/msedge.exe'
                if edge.exists():
                    executable = str(edge)
                else:
                    self.skipTest('Run python -m playwright install chromium or set BUGGCRAFT_TEST_BROWSER')
            browser = await playwright.chromium.launch(headless=True, executable_path=executable)
            self.addAsyncCleanup(browser.close)
            context = await browser.new_context(ignore_https_errors=True, viewport={'width':1280, 'height':1000})
            self.addAsyncCleanup(context.close)
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            directory = Path(folder) / 'data'
            relay_port, web_port = free_ports()
            server = SetupServer(directory, web_port=web_port)
            args = argparse.Namespace(config=None, directory=str(directory), web_port=web_port, setup_host='127.0.0.1')
            with mock.patch('tunnel.onboarding.SetupServer', return_value=server):
                task = asyncio.create_task(entry.serve(args))
                try:
                    url = f'https://127.0.0.1:{web_port}'
                    async with asyncio.timeout(15):
                        while True:
                            if task.done():
                                await task
                            try:
                                reader, writer = await asyncio.open_connection('127.0.0.1', web_port)
                                writer.close()
                                await writer.wait_closed()
                                break
                            except OSError:
                                await asyncio.sleep(.05)
                    await page.goto(url + '/?token=' + server.token)
                    self.assertTrue(await page.get_by_role('heading', name='给节点一个名字').is_visible())
                    artifacts = Path(os.environ.get('BUGGCRAFT_TEST_ARTIFACTS', folder))
                    artifacts.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(artifacts / 'onboarding-desktop.png'), full_page=True)
                    await page.set_viewport_size({'width':390, 'height':844})
                    self.assertLessEqual(await page.evaluate('document.documentElement.scrollWidth'), 390)
                    await page.screenshot(path=str(artifacts / 'onboarding-mobile.png'), full_page=True)
                    await page.locator('[name="name"]').fill('Browser node')
                    await page.locator('[name="port"]').fill(str(relay_port))
                    await page.locator('[name="web_port"]').fill(str(web_port))
                    await page.get_by_role('button', name='继续', exact=True).click()
                    self.assertTrue(await page.get_by_role('heading', name='设置管理员').is_visible())
                    await page.get_by_role('button', name='上一步').click()
                    self.assertEqual(await page.locator('[name="name"]').input_value(), 'Browser node')
                    await page.get_by_role('button', name='继续', exact=True).click()
                    await page.locator('[name="username"]').fill('browser_admin')
                    await page.locator('[name="password"]').fill('browser secret password')
                    await page.locator('[name="password_confirm"]').fill('browser secret password')
                    await page.get_by_role('button', name='继续', exact=True).click()
                    await page.locator('[name="bandwidth_mb_s"]').fill('1')
                    self.assertIn('4 位中转访客', await page.locator('#budget').inner_text())
                    await page.locator('[name="acknowledge"]').check()
                    await page.get_by_role('button', name='保存并启动节点').click()
                    await page.locator('#success').wait_for(state='visible')
                    self.assertNotIn('token=', page.url)
                    # The same process releases the setup TLS listener before starting Portal.
                    async with asyncio.timeout(15):
                        while True:
                            if task.done():
                                await task
                            try:
                                response = await context.request.get(url + '/api/status')
                                if response.ok:
                                    self.assertEqual((await response.json())['name'], 'Browser node')
                                    break
                            except Exception:
                                pass
                            await asyncio.sleep(.05)
                    await page.get_by_role('link', name='进入管理后台').click()
                    await page.locator('#username').fill('browser_admin')
                    await page.locator('#admin-password').fill('browser secret password')
                    await page.locator('#login-form button[type="submit"]').click()
                    await page.locator('#admin-panel').wait_for(state='visible')
                    # Exercise the rendered bandwidth controls with deterministic
                    # RX/TX samples, while all other status fields come from the node.
                    bandwidth_status = 'ready'
                    async def status_sample(route):
                        response = await route.fetch()
                        body = await response.json()
                        body['bandwidth'] = dict(status=bandwidth_status,
                            rx_bytes_per_second=1_250_000, tx_bytes_per_second=2_500_000)
                        await route.fulfill(response=response, json=body)
                    await page.route('**/api/status', status_sample)
                    await page.goto(url + '/')
                    await expect(page.locator('#bandwidth-rx')).to_have_text('1.250')
                    self.assertEqual(await page.locator('#bandwidth-tx').inner_text(), '2.500')
                    await page.get_by_role('button', name='Mbps', exact=True).click()
                    self.assertEqual(await page.locator('#bandwidth-rx').inner_text(), '10.000')
                    self.assertEqual(await page.locator('#bandwidth-tx').inner_text(), '20.000')
                    self.assertEqual(await page.get_by_role('button', name='Mbps', exact=True).get_attribute('aria-pressed'), 'true')
                    await page.screenshot(path=str(artifacts / 'bandwidth-mobile.png'), full_page=True)
                    self.assertLessEqual(await page.evaluate('document.documentElement.scrollWidth'), 390)
                    await page.reload()
                    await expect(page.locator('#bandwidth-rx')).to_have_text('10.000')
                    await page.set_viewport_size({'width':1280, 'height':1000})
                    await page.screenshot(path=str(artifacts / 'bandwidth-desktop.png'), full_page=True)
                    bandwidth_status = 'unavailable'
                    await page.reload()
                    await page.locator('#bandwidth-status').filter(has_text='服务器流量暂不可读').wait_for()
                    self.assertEqual(await page.locator('#bandwidth-rx').inner_text(), '—')
                    bandwidth_status = 'ready'
                    await page.reload()
                    await expect(page.locator('#bandwidth-rx')).to_have_text('10.000')
                    # Losing access to browser storage must not break unit changes.
                    await page.evaluate("Object.defineProperty(window, 'localStorage', {get() {throw new Error('storage blocked')}})")
                    await page.get_by_role('button', name='MB/s', exact=True).click()
                    self.assertEqual(await page.locator('#bandwidth-rx').inner_text(), '1.250')
                    await page.route('**/api/status', lambda route: route.abort())
                    await page.evaluate('refresh()')
                    self.assertEqual(await page.locator('#bandwidth-status').inner_text(), '连接中断，等待刷新')
                    self.assertEqual(await page.locator('#bandwidth-tx').inner_text(), '—')
                    await page.goto(url + '/developers')
                    self.assertIn('节点管理的权限与流程', await page.locator('#protocol').inner_text())
                    self.assertEqual(await page.locator('a[href="/protocol.md"]').count(), 0)
                    self.assertFalse(errors, errors)
                    state = json.loads((directory / 'node-state.json').read_text(encoding='utf-8'))
                    self.assertEqual(state['admin_user'], 'browser_admin')
                finally:
                    # Release browser keep-alive sockets before closing the HTTPS service.
                    await context.close()
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass


if __name__ == '__main__':
    unittest.main()
