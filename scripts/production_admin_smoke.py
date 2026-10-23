"""Read-only checks of authenticated production pages; sessions stay in memory."""
import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright, expect
from production_remote import connect
from production_release import remote_python, save


BOOTSTRAP = r'''
import configparser,http.cookiejar,json,pathlib,re,ssl,urllib.request
data=pathlib.Path('/var/lib/buggcraft-node')
config=configparser.ConfigParser(interpolation=None);config.read(data/'server.conf')
state=json.loads((data/'node-state.json').read_text())
base='https://127.0.0.1:'+config['web'].get('port','18443')
jar=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
 urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
password=re.search(r'^Admin password: (.+)$',(data/'admin-credentials.txt').read_text(),re.M).group(1)
request=urllib.request.Request(base+'/api/login',data=json.dumps(dict(
 username=state.get('admin_user','admin'),password=password)).encode(),
 headers={'Content-Type':'application/json'})
auth=json.loads(opener.open(request,timeout=5).read())
print(json.dumps(dict(csrf=auth['csrf'],token=next(c.value for c in jar if c.name=='node_session'))))
'''


async def main():
    checks=[]
    errors=[]
    async with async_playwright() as playwright:
        executable=Path(playwright.chromium.executable_path)
        if not executable.exists():
            executable=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'
        browser=await playwright.chromium.launch(headless=True,executable_path=str(executable))
        try:
            for node,base in (('node1','https://node.bugg.icu'),('node2','https://43.241.50.19:18444')):
                with connect(node) as client:
                    auth=json.loads(remote_python(client,BOOTSTRAP))
                context=await browser.new_context(ignore_https_errors=True)
                try:
                    await context.add_cookies([dict(name='node_session',value=auth['token'],url=base,
                        httpOnly=True,secure=True,sameSite='Strict')])
                    page=await context.new_page()
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    for section in ('nodes','settings','users','mail','feedback','diagnostics'):
                        response=await page.goto(base+'/admin/'+section,wait_until='networkidle')
                        assert response.status==200
                        await expect(page.locator('.console-nav')).to_be_visible()
                        if section in ('nodes','settings','users','mail'):
                            await expect(page.locator('#admin-panel')).to_be_visible()
                            await expect(page.locator('#admin-bandwidth')).to_be_visible()
                            await expect(page.locator('#bandwidth-rx')).not_to_have_text('—')
                        if section=='diagnostics':
                            await expect(page.locator('#session-detail')).to_be_visible()
                            await expect(page.locator('#event-list')).to_contain_text('连接')
                        for width in (390,1440):
                            await page.set_viewport_size(dict(width=width,height=1000))
                            assert not await page.evaluate('document.documentElement.scrollWidth>innerWidth'),(node,section,width)
                        checks.append(dict(node=node,section=section,authenticated=True,desktop_and_mobile_fit=True))
                        print(node+' /admin/'+section+' passed',flush=True)
                finally:
                    response=await context.request.post(base+'/api/logout',data={},headers={'X-CSRF-Token':auth['csrf']})
                    assert response.status==200
                    await context.close()
                    auth.clear()
            assert not errors,errors
            save('admin-browser.json',dict(passed=True,checks=checks,page_errors=errors,sessions_logged_out=True))
            print(json.dumps(dict(passed=True,pages=len(checks),page_errors=errors,sessions_logged_out=True)))
        finally:
            await browser.close()


if __name__=='__main__':
    asyncio.run(main())
