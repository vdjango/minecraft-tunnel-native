"""Run the shipped Vue bundles, including their server HTML handoff, in Chromium."""
import json
import mimetypes
import os
from pathlib import Path
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web/tunnel/web'
ARTIFACTS = ROOT / 'build/web-browser-artifacts'
ISSUES = [dict(id=f'issue-{index}', title=f'Feedback {index}: mod installation needs checking',
               reporter='Test player', game_version='1.21.1', status='open', updated=1790000000)
          for index in range(30)]


class PageServer(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        admin = path.startswith('/admin/')
        if path == '/':
            html = (WEB / 'index.html').read_text(encoding='utf-8')
            html = html.replace('{{SITE_HEADER}}', (WEB / 'site-header.html').read_text(encoding='utf-8'))
            self.respond(200, 'text/html; charset=utf-8', html.encode())
        elif path in ('/feedback', '/admin/feedback', '/anonymous/feedback', '/admin/diagnostics'):
            filename = 'diagnostics.html' if path.endswith('diagnostics') else 'feedback.html'
            html = (WEB / filename).read_text(encoding='utf-8')
            html = html.replace('{{SITE_HEADER}}', (WEB / 'site-header.html').read_text(encoding='utf-8'))
            # Diagnostics is deliberately unauthenticated; administrator feedback is authenticated.
            has_navigation = path == '/admin/feedback'
            html = html.replace('{{LAYOUT_CLASS}}', 'management-layout' if has_navigation else '')
            html = html.replace('{{ADMIN_NAV}}', (WEB / 'admin-nav.html').read_text(encoding='utf-8') if has_navigation else '')
            if has_navigation:
                html = html.replace('<div id="feedback-content" hidden>', '<div id="feedback-content">')
            elif filename == 'diagnostics.html':
                html = html.replace('<form id="diagnostic-login" class="user-card" hidden>',
                                    '<form id="diagnostic-login" class="user-card">')
            self.respond(200, 'text/html; charset=utf-8', html.encode())
        elif path == '/api/status':
            payload = dict(name='Test node', id='node-test', status='online', uptime_seconds=3600,
                           total_uptime_seconds=2*86400+3600, max_rooms=20, rooms=0, players=0,
                           p2p_players=0, relay_players=0, unconfirmed_players=0,
                           game_counts_reported=0, game_counts_unknown=0, game_players=0,
                           fingerprint='test-fingerprint')
            self.respond(200, 'application/json', json.dumps(payload).encode())
        elif path in ('/api/account/me', '/api/admin') and '/anonymous/' not in self.headers.get('Referer', ''):
            self.respond(200, 'application/json', b'{"csrf":"test-browser-csrf"}')
        elif path == '/api/feedback':
            anonymous = '/anonymous/' in self.headers.get('Referer', '')
            if anonymous:
                self.respond(401, 'application/json', b'{"error":"Please sign in"}')
            else:
                payload = dict(viewer={'admin':'/admin/' in self.headers.get('Referer', '')},
                               issues=ISSUES, total=30, last_sync=0, cleanup_pending=0)
                self.respond(200, 'application/json', json.dumps(payload).encode())
        elif path.startswith('/api/'):
            self.respond(401, 'application/json', b'{"error":"Please sign in"}')
        else:
            asset = WEB / path.lstrip('/')
            if asset.parent == WEB and asset.is_file() and asset.suffix in ('.js', '.css', '.svg', '.png'):
                content_type = 'text/javascript' if asset.suffix == '.js' else mimetypes.guess_type(asset.name)[0]
                self.respond(200, content_type or 'application/octet-stream', asset.read_bytes())
            else:
                self.respond(404, 'text/plain', b'Not found')

    def respond(self, status, content_type, content):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        pass


@unittest.skipUnless(sync_playwright, 'Install Playwright to run browser acceptance tests')
class SupportBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), PageServer)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        options = {'headless':True}
        if os.environ.get('BUGGCRAFT_TEST_BROWSER'):
            options['executable_path'] = os.environ['BUGGCRAFT_TEST_BROWSER']
        cls.browser = cls.playwright.chromium.launch(**options)
        cls.origin = f'http://127.0.0.1:{cls.server.server_port}'
        ARTIFACTS.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def visit(self, path, width):
        page = self.browser.new_page(viewport={'width':width, 'height':840})
        page.set_default_timeout(10000)
        self.addCleanup(page.close)
        errors = []
        page.on('pageerror', lambda error:errors.append(str(error)))
        response = page.goto(self.origin + path, wait_until='networkidle')
        self.assertEqual(response.status, 200)
        page.wait_for_selector('[data-vue-page]')
        self.assertEqual(errors, [])
        return page, errors

    def assert_layout(self, page, *, admin=False):
        self.assertEqual(page.locator('.console-nav').count(), int(admin))
        geometry = page.evaluate("""() => ({titleY:document.querySelector('.page-heading').getBoundingClientRect().y,
            headerY:document.querySelector('header').getBoundingClientRect().y,
            wide:document.documentElement.scrollWidth>innerWidth})""")
        self.assertFalse(geometry['wide'])
        self.assertEqual(geometry['headerY'], 0)
        self.assertLess(geometry['titleY'], 400 if admin else 260)

    def test_public_feedback_long_list_stays_compact_after_vue_mount(self):
        for width in (390, 559, 1440):
            with self.subTest(width=width):
                page, errors = self.visit('/feedback', width)
                page.wait_for_selector('#issue-list .issue-row')
                self.assert_layout(page)
                self.assertEqual(page.locator('#issue-list .issue-row').count(), 30)
                self.assertTrue(page.locator('#issue-list').evaluate('(element)=>element.scrollHeight>element.clientHeight'))
                self.assertTrue(page.locator('#issues-next').is_visible())
                page.screenshot(path=str(ARTIFACTS / f'feedback-list-{width}.png'), full_page=True)
                self.assertEqual(errors, [])
                page.close()

    def test_feedback_user_menu_matches_account_navigation(self):
        page, errors = self.visit('/feedback', 559)
        page.wait_for_selector('#issue-list .issue-row')
        links = page.locator('.user-feedback-nav a')
        self.assertEqual(links.all_text_contents(), [
            '概览', '连接启动器', '在线玩家', '好友', '个人资料', '安全与备份', '问题反馈'])
        self.assertEqual(links.last.get_attribute('aria-current'), 'page')
        self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        page.screenshot(path=str(ARTIFACTS / 'feedback-unified-menu-559.png'), full_page=True)
        self.assertEqual(errors, [])

    def test_home_shows_current_and_cumulative_uptime(self):
        page, errors = self.visit('/', 559)
        page.wait_for_function("document.querySelector('#total-days')?.textContent?.includes('累计运行 2.0 天')")
        self.assertIn('1.0 时', page.locator('#uptime').inner_text())
        self.assertEqual(page.locator('#total-days').inner_text(), '累计运行 2.0 天')
        self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
        page.screenshot(path=str(ARTIFACTS / 'node-cumulative-days-559.png'), full_page=True)
        self.assertEqual(errors, [])

    def test_new_feedback_form_is_reachable_without_administrator_menu(self):
        page, errors = self.visit('/feedback?new=1', 559)
        page.wait_for_selector('#issue-create')
        self.assert_layout(page)
        page.locator('#issue-title').fill('Mod installation problem')
        self.assertEqual(page.locator('#issue-title').input_value(), 'Mod installation problem')
        page.locator('#show-issues').click()
        page.wait_for_selector('#issue-list-panel')
        self.assertTrue(page.locator('#issue-create').is_hidden())
        page.screenshot(path=str(ARTIFACTS / 'feedback-form-return.png'), full_page=True)
        self.assertEqual(errors, [])

    def test_short_anonymous_page_does_not_float_down_the_screen(self):
        page, errors = self.visit('/anonymous/feedback', 559)
        page.wait_for_selector('#feedback-login')
        self.assert_layout(page)
        page.screenshot(path=str(ARTIFACTS / 'feedback-anonymous-559.png'), full_page=True)
        self.assertEqual(errors, [])

    def test_administrator_feedback_keeps_navigation_and_list(self):
        page, errors = self.visit('/admin/feedback', 559)
        page.wait_for_selector('#issue-list .issue-row')
        self.assert_layout(page, admin=True)
        self.assertTrue(page.locator('.console-nav a[href="/admin/nodes"]').is_visible())
        self.assertTrue(page.locator('.user-feedback-nav').is_hidden())
        page.screenshot(path=str(ARTIFACTS / 'feedback-admin-559.png'), full_page=True)
        self.assertEqual(errors, [])

    def test_diagnostics_login_does_not_restore_administrator_menu(self):
        page, errors = self.visit('/admin/diagnostics', 559)
        page.wait_for_selector('#diagnostic-login')
        self.assert_layout(page)
        self.assertEqual(errors, [])
