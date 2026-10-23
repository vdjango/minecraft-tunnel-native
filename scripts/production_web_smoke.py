"""Exercise the public production pages in an isolated desktop/mobile browser."""
import asyncio
import hashlib
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright
from production_release import ARTIFACT, ROOT, save


async def main():
    pages=[]
    errors=[]
    async with async_playwright() as playwright:
        executable=Path(playwright.chromium.executable_path)
        if not executable.exists():
            executable=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'
        browser=await playwright.chromium.launch(headless=True,executable_path=str(executable))
        try:
            context=await browser.new_context(viewport=dict(width=1440,height=1000))
            page=await context.new_page()
            page.on('pageerror',lambda error:errors.append(str(error)))
            # These routes are read-only and do not submit any account forms.
            urls=['https://bugg.icu/','https://download.bugg.icu/']+[
                'https://node.bugg.icu'+path for path in ('/','/login','/account','/connect',
                '/feedback','/feedback?new=1','/network','/admin/users','/admin/feedback',
                '/admin/diagnostics','/developers')]
            for url in urls:
                print('Checking '+url,flush=True)
                response=await page.goto(url,wait_until='networkidle',timeout=30000)
                assert response.status==200,(url,response.status)
                assert await page.title(),url
                theme_toggle=not url.startswith('https://download.bugg.icu/')
                if theme_toggle:
                    await page.locator('#theme-toggle').wait_for()
                    before=await page.locator('html').get_attribute('data-theme')
                    await page.locator('#theme-toggle').click()
                    after=await page.locator('html').get_attribute('data-theme')
                    assert before!=after,url
                for width in (1440,390):
                    await page.set_viewport_size(dict(width=width,height=1000))
                    overflow=await page.evaluate('document.documentElement.scrollWidth>innerWidth')
                    assert not overflow,(url,width)
                pages.append(dict(url=url,status=response.status,title=await page.title(),
                                  theme_toggle=theme_toggle,desktop_and_mobile_fit=True))
            await page.goto('https://node.bugg.icu/feedback',wait_until='networkidle')
            await page.screenshot(path=str(ARTIFACT/'feedback-mobile.png'),full_page=True)
            await page.goto('https://node.bugg.icu/admin/diagnostics',wait_until='networkidle')
            await page.screenshot(path=str(ARTIFACT/'diagnostics-mobile.png'),full_page=True)
            # Compare all published landing/download assets with this release.
            assets=[]
            for path in sorted((ROOT/'site').rglob('*')):
                if not path.is_file():continue
                relative=path.relative_to(ROOT/'site').as_posix()
                url=('https://download.bugg.icu/'+relative.removeprefix('download/')
                     if relative.startswith('download/') else 'https://bugg.icu/'+relative)
                response=await context.request.get(url)
                assert response.status==200,(url,response.status)
                assert hashlib.sha256(await response.body()).digest()==hashlib.sha256(path.read_bytes()).digest(),url
                assets.append(relative)
            response=await context.request.get('https://download.bugg.icu/release.json')
            assert response.status==200
            release=await response.json()
            # Avoid downloading the whole launcher just to check publication.
            installer=await context.request.head('https://download.bugg.icu/Buggcraft-Setup.exe')
            assert installer.status==200
            assert not errors,errors
            result=dict(passed=True,pages=pages,assets=assets,page_errors=errors,
                launcher_release=release.get('version'),installer_head_status=installer.status)
            save('public-browser.json',result)
            print(json.dumps(dict(passed=True,pages=len(pages),assets=len(assets),page_errors=errors,
                                 launcher_release=release.get('version'))))
        finally:
            await browser.close()


if __name__=='__main__':
    asyncio.run(main())
