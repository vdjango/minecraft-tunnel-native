"""Read-only browser checks for the current website, documentation and account entry.

No release/build module is imported and no administrator forms are submitted.
Use --base for an isolated static server; production is the default.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright, expect

ROOT=Path(__file__).resolve().parents[1]

async def main(base,artifact):
    artifact.mkdir(parents=True,exist_ok=True)
    results=[];errors=[]
    async with async_playwright() as playwright:
        executable=Path(playwright.chromium.executable_path)
        if not executable.exists():
            executable=Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe'
        browser=await playwright.chromium.launch(executable_path=str(executable))
        try:
            page=await browser.new_page(viewport=dict(width=1280,height=900))
            page.on('pageerror',lambda error:errors.append(str(error)))
            urls=[('/', 'home'),('/docs/', 'docs')]
            urls.extend(('/'+p.relative_to(ROOT/'site').as_posix()+'/',p.name)
                for p in sorted((ROOT/'site/docs/help').iterdir()) if p.is_dir())
            for route,label in urls:
                response=await page.goto(base+route,wait_until='domcontentloaded',timeout=30000)
                assert response.status==200,(route,response.status)
                await expect(page.locator('h1')).to_be_visible()
                await page.locator('img').evaluate_all('(items)=>items.forEach(i=>i.loading="eager")')
                await page.wait_for_function('Array.from(document.images).every(i=>i.complete&&i.naturalWidth>0)')
                await page.locator('img').evaluate_all('(items)=>Promise.all(items.map(i=>i.decode()))')
                for width in (1280,390):
                    await page.set_viewport_size(dict(width=width,height=900))
                    if width==390 and await page.locator('.sidebar').count():
                        await page.wait_for_function("document.querySelector('.sidebar').getBoundingClientRect().right<=1")
                    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(route,width)
                    if width==1280 or label in ('home','experience','developer-multiplayer','developer-source'):
                        await page.screenshot(path=str(artifact/f'{label}-{width}.png'),full_page=True)
                assert await page.locator('a[href="https://beian.miit.gov.cn/"]').count(),route
                assert not await page.locator('a[href*="/feedback?new="]').count(),route
                results.append(dict(route=route,title=await page.title(),images_loaded=True,desktop_mobile_fit=True))
            assert not errors,errors
            (artifact/'verification.json').write_text(json.dumps(dict(base=base,pages=results,page_errors=errors),indent=2),encoding='utf-8')
            print(json.dumps(dict(pages=len(results),desktop_mobile_fit=True,page_errors=errors)))
        finally:await browser.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--base',default='https://bugg.icu')
    parser.add_argument('--artifact',type=Path,default=ROOT/'validation-artifacts/site-native-docs')
    args=parser.parse_args()
    asyncio.run(main(args.base.rstrip('/'),args.artifact))
