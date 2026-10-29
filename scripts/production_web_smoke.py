"""Exercise the public production pages in an isolated desktop/mobile browser."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urljoin

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
            external_release_requests=[]
            page.on('request',lambda request:external_release_requests.append(request.url)
                    if 'api.github.com/repos/vdjango/buggcraft/releases' in request.url else None)
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
                theme_toggle=True
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
            platform_checks=[]
            test_platforms=(
                ('windows','Windows','Win32','Windows','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'),
                ('mac','macOS','MacIntel','macOS','Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'),
                ('linux','Linux','Linux x86_64','Linux','Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'))
            for platform,title,navigator_platform,platform_hint,user_agent in test_platforms:
                platform_context=await browser.new_context(viewport=dict(width=1280,height=900),user_agent=user_agent)
                await platform_context.add_init_script(
                    "Object.defineProperty(navigator,'platform',{get:()=>"+json.dumps(navigator_platform)+"});"
                    "Object.defineProperty(navigator,'userAgentData',{get:()=>({platform:"+json.dumps(platform_hint)+"})});")
                platform_page=await platform_context.new_page()
                await platform_page.goto('https://bugg.icu/',wait_until='networkidle')
                cta=platform_page.locator('.hero-actions .download-button')
                assert platform in await cta.get_attribute('data-detected-platform'),platform
                assert title in await cta.locator('[data-platform-label]').inner_text(),platform
                assert title in await cta.locator('[data-platform-detail]').inner_text(),platform
                assert 'platform=auto' in await cta.get_attribute('href'),platform
                await cta.click()
                await platform_page.wait_for_load_state('networkidle')
                assert platform_page.url.endswith('?platform=auto'),platform_page.url
                assert await platform_page.locator('#platform-title').inner_text()==title,platform
                assert await platform_page.locator('#detected-pill').inner_text()=='与你的系统相符',platform
                platform_checks.append(dict(platform=platform,cta_matches=True,download_page_matches=True))
                await platform_context.close()

            await page.goto('https://download.bugg.icu/?platform=windows',wait_until='networkidle')
            assert await page.locator('#platform-title').inner_text()=='Windows'
            assert await page.locator('#selected-download').get_attribute('aria-disabled') is None
            selected_download=await page.locator('#selected-download').evaluate('(item)=>item.href')
            assert selected_download.startswith('https://download.bugg.icu/release-cache/'),selected_download
            for platform,title in (('mac','macOS'),('linux','Linux')):
                await page.locator(f'[data-platform="{platform}"]').click()
                assert await page.locator('#platform-title').inner_text()==title
            await page.goto('https://download.bugg.icu/?platform=windows',wait_until='networkidle')
            cache_response=await context.request.get('https://download.bugg.icu/releases.json')
            assert cache_response.status==200,cache_response.status
            release_cache=await cache_response.json()
            assert release_cache.get('updated_at') and release_cache.get('releases')
            public_releases=release_cache['releases']
            actual_asset_urls={urljoin('https://download.bugg.icu/',asset['browser_download_url'])
                               for release in public_releases for asset in release.get('assets',[])}
            assert selected_download in actual_asset_urls,selected_download
            installer=await context.request.head(selected_download)
            assert installer.status==200,(selected_download,installer.status)
            listed_downloads=await page.locator('#release-history .history-download').evaluate_all(
                '(items)=>items.map(item=>item.href).filter(url=>url.includes("/release-cache/"))')
            assert all(url in actual_asset_urls for url in listed_downloads),listed_downloads
            assert not external_release_requests,external_release_requests
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
                if relative in ('platform.js','theme.js','style.css'):
                    url+='?v=20261025-platform-auto'
                response=await context.request.get(url)
                assert response.status==200,(url,response.status)
                assert hashlib.sha256(await response.body()).digest()==hashlib.sha256(path.read_bytes()).digest(),url
                assets.append(relative)
            assert not errors,errors
            result=dict(passed=True,pages=pages,assets=assets,page_errors=errors,
                launcher_release=public_releases[0].get('tag_name') if public_releases else None,
                windows_download_url=selected_download,release_cache_updated_at=release_cache['updated_at'],
                history_download_links=len(listed_downloads),
                platform_checks=platform_checks)
            save('public-browser.json',result)
            print(json.dumps(dict(passed=True,pages=len(pages),assets=len(assets),page_errors=errors,
                                 launcher_release=public_releases[0].get('tag_name') if public_releases else None,
                                 history_download_links=len(listed_downloads),platform_checks=platform_checks,
                                 release_cache_updated_at=release_cache['updated_at'])))
        finally:
            await browser.close()


if __name__=='__main__':
    asyncio.run(main())
