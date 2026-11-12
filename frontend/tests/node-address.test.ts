import {afterEach, describe, expect, it, vi} from 'vitest';
import {createApp} from 'vue';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import Account from '../src/pages/account.vue';

const controller = readFileSync(resolve(process.cwd(), '../web/tunnel/web/node-address.js'), 'utf8');
let app: ReturnType<typeof createApp>;
afterEach(() => {app?.unmount();document.body.replaceChildren();vi.restoreAllMocks();});
function mount(page: typeof Account) {
  const host = document.createElement('div');document.body.append(host);
  app = createApp(page, {seed:{}});app.mount(host);
  new Function('$', controller)((id:string) => document.getElementById(id));
  return document.getElementById('copy-node-address') as HTMLButtonElement;
}
describe.each([Account])('custom service address', page => {
  it('copies the current public origin and exposes no old code controls', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText}});
    const button = mount(page);
    expect(document.getElementById('node-address')!.textContent).toBe(location.origin);
    expect(document.querySelector('#player-code, #account-connect, #access-code-format, .legacy-access')).toBeNull();
    await button.onclick!(new MouseEvent('click'));
    expect(writeText).toHaveBeenCalledExactlyOnceWith(location.origin);
    expect(document.getElementById('node-address-feedback')!.textContent).toContain('已复制');
  });
  it('selects only the address if clipboard permission is denied', async () => {
    Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:vi.fn().mockRejectedValue(new Error('denied'))}});
    const button = mount(page);
    await button.onclick!(new MouseEvent('click'));
    expect(getSelection()!.toString()).toBe(location.origin);
    expect(document.getElementById('node-address-feedback')!.textContent).toContain('已选中');
  });
});
