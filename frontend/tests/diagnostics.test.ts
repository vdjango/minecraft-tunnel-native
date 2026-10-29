import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

const html = readFileSync(resolve('../web/tunnel/web/diagnostics.html'), 'utf8');
const source = readFileSync(resolve('../web/tunnel/web/diagnostics.js'), 'utf8');

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = html.match(/<body[^>]*>([\s\S]*)<\/body>/)![1];
  vi.spyOn(document, 'hidden', 'get').mockReturnValue(false);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    scale: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(),
    lineTo: vi.fn(), stroke: vi.fn(),
  } as unknown as CanvasRenderingContext2D);
});
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.restoreAllMocks();
  document.body.replaceChildren();
});

async function view(role: string, events: object[]) {
  const session = {player: 'anonymous-example', session: 'test-session', username: 'example',
    role, started: 1, online: false, authenticated: false};
  const api = vi.fn().mockResolvedValueOnce({sessions: [session], dropped_events: 5, write_errors: 2})
    .mockResolvedValueOnce({events, critical_events: events, truncated: false});
  const controller = new Function('api', '$', 'feedback', source + '\nreturn {loadSessions};')(
    api, (id: string) => document.getElementById(id), vi.fn());
  await controller.loadSessions();
  return api;
}

describe('diagnostic evidence display', () => {
  it('shows rejected directory authentication and storage loss instead of missing-disconnect text', async () => {
    await view('directory', [{at: 1, event: 'authentication_rejected', reason: 'account-expired-claim'}]);
    expect(document.getElementById('findings')!.textContent).toContain('认证被拒绝：account-expired-claim');
    expect(document.getElementById('findings')!.textContent).not.toContain('缺少断开记录');
    expect(document.getElementById('session-meta')!.textContent).toContain('目录请求');
    expect(document.getElementById('journal-health')!.textContent).toContain('队列丢弃 5 条');
    expect(document.getElementById('journal-health')!.textContent).toContain('写盘失败 2 次');
    expect(document.getElementById('event-list')!.textContent).toContain('account-expired-claim');
  });
  it('distinguishes a control handler timeout and retains the returned transport facts', async () => {
    await view('host', [{at: 1, event: 'connected'}, {at: 2, event: 'disconnected',
      reason: 'control_rpc_timeout', transport: {rx_age_ms: 100, poll_gap_ms: 40}}]);
    expect(document.getElementById('findings')!.textContent).toContain('节点控制请求处理超时');
    expect(document.getElementById('sample-detail')!.textContent).toContain('rx_age_ms');
    expect(document.getElementById('event-list')!.textContent).toContain('连接并通过认证');
  });
});
