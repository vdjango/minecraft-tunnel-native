"""Exercise the published website bundle and responsive player-facing flows."""
import functools
from contextlib import ExitStack
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import threading
import unittest

from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]


class QuietSite(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


class WebsiteBrowserTests(unittest.TestCase):
    def test_responsive_home_navigation_community_and_faq(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(QuietSite,directory=str(ROOT/'site')))
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        artifacts=ROOT/'validation-artifacts/website-web'
        artifacts.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as playwright, ExitStack() as resources:
            executable=os.environ.get('BUGGCRAFT_TEST_BROWSER')
            if not executable and not Path(playwright.chromium.executable_path).exists():
                executable=str(Path(os.environ['ProgramFiles(x86)'])/'Microsoft/Edge/Application/msedge.exe')
            browser=playwright.chromium.launch(headless=True,executable_path=executable)
            resources.callback(browser.close)
            page=browser.new_page()
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.server_port}/',wait_until='networkidle')
            expect(page.locator('#hero-title')).to_have_text('打开游戏。也打开朋友的世界。')
            for width in (390,768,1280,1920):
                page.set_viewport_size(dict(width=width,height=900))
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width)
                self.assertEqual(page.locator('body').evaluate('(el)=>getComputedStyle(el).backgroundColor'),'rgb(253, 252, 249)')
                page.screenshot(path=str(artifacts/f'home-{width}.png'),full_page=True)
            self.assertTrue(page.locator('.hero-preview img').evaluate('(el)=>el.complete&&el.naturalWidth>0'))
            self.assertEqual(page.locator('a[href*="node.bugg.icu/network"],a[href*="node.bugg.icu/feedback"]').count(),0)
            page.get_by_text('普通玩家需要部署节点吗？',exact=True).click()
            expect(page.locator('.faq-list details[open]')).to_contain_text('通常不需要')
            page.evaluate("Object.defineProperty(navigator,'clipboard',{value:{writeText:async(value)=>{window.copied=value}},configurable:true})")
            page.get_by_role('button',name='复制微信号',exact=True).click()
            expect(page.locator('.copy-status')).to_contain_text('已复制微信号：junsi223')
            self.assertEqual(page.evaluate('window.copied'),'junsi223')
            self.assertEqual(errors,[])


if __name__=='__main__':
    unittest.main()
