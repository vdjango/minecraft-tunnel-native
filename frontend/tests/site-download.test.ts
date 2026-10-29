import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
const markup=readFileSync(resolve(__dirname,'../../site/download/index.html'),'utf8');
const controller=readFileSync(resolve(__dirname,'../../site/download-page.js'),'utf8');

const binaries = ['Buggcraft.exe','Buggcraft','Buggcraft-linux-arm64'];
const packages = ['Buggcraft-windows-x64.zip','Buggcraft-linux-x86_64.zip','Buggcraft-linux-arm64.zip',
  'Buggcraft-macos-arm64.zip','Buggcraft-macos-x86_64.zip'];
function release(version='1.0.1-alpha.1', names=[...binaries,...packages,
  'SHA256SUMS-downloads.txt','SHA256SUMS-macos-arm64.txt','SHA256SUMS-macos-x86_64.txt']) {
  return {tag_name:'v'+version,prerelease:true,published_at:'2026-10-29T00:00:00Z',body:'Test release',
    assets:names.map(name=>({name,size:123,browser_download_url:`https://download.bugg.icu/api/launcher/assets/${version}/${name}`}))};
}
async function load(managed=[release()], legacy:ReturnType<typeof release>[]=[], hidden:string[]=[]){
  const fetch=vi.fn(async (url:string)=>({ok:true,json:async()=>url.includes('/api/launcher/releases')
    ?{releases:managed,hidden_versions:hidden}:{releases:legacy}}));
  new Function('fetch',controller)(fetch);
  await vi.waitFor(()=>expect(document.querySelector('#download-status')?.textContent).toContain('后台发布即时同步'));
  return fetch;
}
function click(selector:string){(document.querySelector(selector) as HTMLElement).click();}
function href(selector='#selected-download'){return (document.querySelector(selector) as HTMLAnchorElement).href;}

beforeEach(()=>{
  window.history.replaceState(null,'','/?platform=windows');
  document.body.innerHTML=new DOMParser().parseFromString(markup,'text/html').body.innerHTML;
});
afterEach(()=>{document.body.replaceChildren();vi.unstubAllGlobals();});

describe('website ZIP downloads',()=>{
  it('offers the Windows ZIP instead of the binary and links to its checksum',async()=>{
    await load();
    expect(href()).toMatch(/\/Buggcraft-windows-x64\.zip$/);
    expect(href('#checksum-link')).toMatch(/\/SHA256SUMS-downloads\.txt$/);
    expect(document.querySelector('#platform-description')?.textContent).toContain('解压 ZIP');
    expect(document.querySelector('#selected-download')?.hasAttribute('aria-disabled')).toBe(false);
  });
  it('switches real platform and architecture buttons to the correct ZIPs',async()=>{
    await load();
    click('[data-platform="linux"]');
    click('[data-arch="arm64"]');
    expect(href()).toMatch(/\/Buggcraft-linux-arm64\.zip$/);
    click('[data-arch="x86_64"]');
    expect(href()).toMatch(/\/Buggcraft-linux-x86_64\.zip$/);
    expect(href('#checksum-link')).toMatch(/\/SHA256SUMS-downloads\.txt$/);
    click('[data-platform="mac"]');
    expect(href()).toMatch(/\/Buggcraft-macos-x86_64\.zip$/);
    expect(href('#checksum-link')).toMatch(/\/SHA256SUMS-macos-x86_64\.txt$/);
    click('[data-arch="arm64"]');
    expect(href()).toMatch(/\/Buggcraft-macos-arm64\.zip$/);
    click('[data-platform="windows"]');
    expect(href()).toMatch(/\/Buggcraft-windows-x64\.zip$/);
  });
  it('shows five ZIPs once each in history and does not expose duplicate raw binaries',async()=>{
    await load();
    const links=[...document.querySelectorAll<HTMLAnchorElement>('.history-download')];
    expect(links).toHaveLength(5);
    expect(links.map(link=>link.href.split('/').at(-1)).sort()).toEqual([...packages].sort());
    for(const link of links) expect(link.href).toMatch(/\.zip$/);
  });
  it('honors old-version deep links and excludes withdrawn and draft releases',async()=>{
    window.history.replaceState(null,'','/?platform=windows&version=1.0.0-alpha.1');
    const older=release('1.0.0-alpha.1');
    await load([release(),{...release('2.0.0-alpha.1'),draft:true}], [older,release('0.9.0-alpha.1')],['0.9.0-alpha.1']);
    expect(href()).toContain('/1.0.0-alpha.1/Buggcraft-windows-x64.zip');
    expect(document.querySelectorAll('.history-entry')).toHaveLength(2);
  });
  it('keeps the download disabled when a ZIP is unavailable, without offering an EXE',async()=>{
    await load([release('1.0.1-alpha.1',binaries)]);
    expect(document.querySelector('#selected-download')?.getAttribute('aria-disabled')).toBe('true');
    expect(document.querySelectorAll('.history-download')).toHaveLength(0);
  });
});
