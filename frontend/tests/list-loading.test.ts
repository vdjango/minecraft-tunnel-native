import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
const script=readFileSync(resolve(__dirname,'../list-loading.js'),'utf8');
beforeEach(()=>{
  vi.useFakeTimers();
  delete (window as any).__buggcraftListLoading;
  vi.spyOn(HTMLElement.prototype,'getClientRects').mockReturnValue([{}] as any);
  document.body.innerHTML='<button id="refresh-network">刷新</button><div><table><thead><tr><th>名称</th><th>状态</th></tr></thead><tbody id="network-rows"></tbody></table></div>';
});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers();document.body.replaceChildren();});
function install(json:()=>Promise<unknown>,ok=true){vi.stubGlobal('fetch',vi.fn(async()=>({ok,json})));new Function(script)();}
describe('list loading and refresh',()=>{
  it('shows actual table skeleton rows until the response body arrives',async()=>{
    let resolveBody!:(v:unknown)=>void;
    install(()=>new Promise(r=>resolveBody=r));
    const response=await fetch('/api/status');
    expect(document.querySelectorAll('.list-skeleton-row')).toHaveLength(4);
    expect(document.querySelectorAll('.list-skeleton-row:first-child td')).toHaveLength(2);
    const body=response.json();resolveBody({nodes:[]});await body;
    await vi.advanceTimersByTimeAsync(32);
    expect(document.querySelector('.list-skeleton-row')).toBeNull();
    expect(document.querySelector('[aria-busy=true]')).toBeNull();
  });
  it('retains populated rows while refreshing and keeps the region busy for overlapping requests',async()=>{
    document.querySelector('#network-rows')!.innerHTML='<tr><td>原有节点</td></tr>';
    const resolvers:Array<(value:unknown)=>void>=[];install(()=>new Promise(r=>resolvers.push(r)));
    const a=(await fetch('/api/status')).json(),b=(await fetch('/api/status')).json();
    expect(document.querySelector('#network-rows')!.textContent).toBe('原有节点');
    expect(document.querySelector('.list-load-status')!.textContent).toContain('刷新');
    resolvers[0]({});await a;await vi.advanceTimersByTimeAsync(32);
    expect(document.querySelector('#network-rows')!.getAttribute('aria-busy')).toBe('true');
    resolvers[1]({});await b;await vi.advanceTimersByTimeAsync(32);
    expect(document.querySelector('.list-load-status')).toBeNull();
    expect(document.querySelector('#network-rows')!.textContent).toBe('原有节点');
  });
  it('retains old data and offers a retry through the existing refresh control on failure',async()=>{
    document.querySelector('#network-rows')!.innerHTML='<tr><td>原有节点</td></tr>';
    const retry=vi.fn();document.querySelector('#refresh-network')!.addEventListener('click',retry);
    install(async()=>({error:'unavailable'}),false);
    await (await fetch('/api/status')).json();await vi.advanceTimersByTimeAsync(32);
    expect(document.querySelector('#network-rows')!.textContent).toBe('原有节点');
    (document.querySelector('.list-load-status button') as HTMLButtonElement).click();
    expect(retry).toHaveBeenCalledOnce();
  });
});
