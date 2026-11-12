import {afterEach,expect,it,vi} from 'vitest';
import {createApp} from 'vue';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import Admin from '../src/pages/admin.vue';

let app:ReturnType<typeof createApp>;
afterEach(()=>{app?.unmount();document.body.replaceChildren();vi.restoreAllMocks();});
it('reveals proxy controls only when enabled and submits the explicit Cloudflare choice',async()=>{
  const root=document.createElement('div');document.body.append(root);
  app=createApp(Admin,{seed:{}});app.mount(root);
  const fetch=vi.fn(()=>new Promise(()=>{}));
  const controller=readFileSync(resolve(process.cwd(),'../web/tunnel/web/admin.js'),'utf8');
  new Function('setInterval','fetch','renderAdminBandwidth',controller)(vi.fn(),fetch,vi.fn());
  const enabled=root.querySelector('#setting-proxy-enabled') as HTMLInputElement;
  const cloudflare=root.querySelector('#setting-cloudflare-enabled') as HTMLInputElement;
  const proxies=root.querySelector('#trusted-proxy-settings') as HTMLElement;
  const details=root.querySelector('#cloudflare-proxy-settings') as HTMLElement;
  expect(proxies.hidden).toBe(true);expect(details.hidden).toBe(true);
  enabled.click();expect(proxies.hidden).toBe(false);expect(details.hidden).toBe(true);
  cloudflare.click();expect(details.hidden).toBe(false);
  enabled.click();expect(proxies.hidden).toBe(true);expect(details.hidden).toBe(true);
  expect(cloudflare.disabled).toBe(true);
  const button=root.querySelector('#settings-form button[type="submit"]') as HTMLButtonElement;
  root.querySelector('#settings-form')!.dispatchEvent(new SubmitEvent('submit',{submitter:button,cancelable:true}));
  expect(fetch).toHaveBeenCalledOnce();
  const body=JSON.parse((fetch.mock.calls[0] as any)[1].body);
  expect(body.trusted_proxy_enabled).toBe(false);expect(body.cloudflare_proxy_enabled).toBe(true);
});
