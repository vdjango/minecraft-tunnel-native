import {afterEach, describe, expect, it, vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {createApp, h} from 'vue';
import Account from '../src/pages/account.vue';
import Connect from '../src/pages/connect.vue';

const source = readFileSync(resolve(process.cwd(), '../web/tunnel/web/access-code.js'), 'utf8');
let unmount: (() => void) | undefined;
afterEach(() => { unmount?.(); document.body.replaceChildren(); vi.restoreAllMocks(); });

function mount(page: typeof Account) {
  const host = document.createElement('div'); document.body.append(host);
  const app = createApp({render: () => h(page, {seed: {}})}); app.mount(host);
  unmount = () => app.unmount();
  new Function('$', source)((id: string) => document.getElementById(id));
  const field = document.getElementById('player-code') as HTMLTextAreaElement;
  const button = document.getElementById('copy-code') as HTMLButtonElement;
  const status = document.getElementById('connect-feedback')!;
  document.getElementById(page === Account ? 'personal-result' : 'connect-result')!.hidden = false;
  return {field, button, status};
}

describe.each([['personal', Account], ['guest', Connect]] as const)('%s launcher code', (_name, page) => {
  it('copies only the code, including its signing suffix, independently of surrounding text', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText}});
    const {field, button, status} = mount(page);
    field.value = '  buggnode2-aHR0cHM6Ly9leGFtcGxlLmNvbQ.reference~privateKey  \n';
    status.textContent = '生成成功：这是不应该被复制的说明';
    await button.onclick!(new MouseEvent('click'));
    expect(writeText).toHaveBeenCalledExactlyOnceWith('buggnode2-aHR0cHM6Ly9leGFtcGxlLmNvbQ.reference~privateKey');
    expect(status.textContent).toBe('接入码已复制。');
    expect(field.closest('.code-output')!.contains(status)).toBe(false);
    expect(button.compareDocumentPosition(field) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('copies only the selected code when the clipboard API is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: undefined});
    const {field, button, status} = mount(page);
    field.value = 'buggnode1-testCode';
    const copy = vi.fn(() => {expect(field.value.slice(field.selectionStart, field.selectionEnd)).toBe('buggnode1-testCode'); return true;});
    Object.defineProperty(document, 'execCommand', {configurable: true, value: copy});
    await button.onclick!(new MouseEvent('click'));
    expect(copy).toHaveBeenCalledExactlyOnceWith('copy');
    expect(status.textContent).toBe('接入码已复制。');
  });

  it('leaves only the code selected for manual copy if browser permissions deny both methods', async () => {
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: vi.fn().mockRejectedValue(new Error('denied'))}});
    Object.defineProperty(document, 'execCommand', {configurable: true, value: vi.fn(() => false)});
    const {field, button, status} = mount(page); field.value = 'buggnode1-testCode';
    await button.onclick!(new MouseEvent('click'));
    expect(field.selectionStart).toBe(0); expect(field.selectionEnd).toBe(field.value.length);
    expect(status.textContent).toContain('请按 Ctrl+C');
  });
});
