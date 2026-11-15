import { computed, ref } from 'vue';
import { unzipSync, strFromU8 } from 'fflate';
import { createScope } from './runtime';

export interface FeedbackItem {
  id: string; message: string; expected: string; contact: string; created_at: string;
  has_archive: boolean; notified_at: string; mail_error: string;
  account_id?: string; archive?: string; sha256?: string;
}

/** Read-only administrator session. Secrets and attachments live in page memory only. */
export function useFeedback() {
  const scope = createScope();
  const keyInput = ref(''), connected = ref(false), busy = ref(false), error = ref('');
  const items = ref<FeedbackItem[]>([]), detail = ref<FeedbackItem | null>(null);
  const cursor = ref(''), history = ref<string[]>(['']), filter = ref('');
  const files = ref<Record<string, Uint8Array>>({}), selectedFile = ref(''), previewReady = ref(false);
  let token = '', generation = 0;
  const visibleItems = computed(() => items.value.filter(item =>
    `${item.id} ${item.message} ${item.expected} ${item.contact}`.toLocaleLowerCase().includes(filter.value.toLocaleLowerCase())));
  const preview = computed(() => {
    const bytes = files.value[selectedFile.value];
    if (!bytes) return '';
    const text = strFromU8(bytes);
    if (selectedFile.value.endsWith('.json')) {
      try { return JSON.stringify(JSON.parse(text), null, 2); } catch { /* Show original text. */ }
    }
    return text;
  });
  function clear() {
    generation++; scope.dispose(); token = ''; keyInput.value = ''; connected.value = false;
    items.value = []; detail.value = null; files.value = {}; selectedFile.value = ''; busy.value = false;
  }
  async function request(operation: 'list' | 'get', body: object, credential: string) {
    const response = await scope.fetch('/v1/admin/feedback/' + operation, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + credential },
      credentials: 'omit', cache: 'no-store', body: JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) {
      if (response.status === 401) throw new Error('管理密钥不正确或已失效，请重新登录。');
      if (response.status === 409) throw new Error('列表已更新，请点击刷新重新加载。');
      if (response.status === 429) throw new Error('操作较频繁，请稍后重试。');
      throw new Error('暂时无法读取反馈，请稍后重试。');
    }
    return value;
  }
  async function load(next = '', candidate = token, login = false) {
    if (busy.value) return;
    const current = ++generation; busy.value = true; error.value = '';
    try {
      const value = await request('list', next ? { cursor: next } : {}, candidate);
      if (current !== generation) return;
      items.value = value.items; cursor.value = value.next_cursor || '';
      detail.value = null; files.value = {}; previewReady.value = false;
      if (login) { token = candidate; connected.value = true; keyInput.value = ''; }
    } catch (failure) {
      if (current === generation) error.value = failure instanceof Error ? failure.message : '连接失败，请重试。';
    } finally { if (current === generation) busy.value = false; }
  }
  async function login() {
    const candidate = keyInput.value.trim();
    if (candidate.length < 32 || candidate.length > 4096) { error.value = '请输入完整的反馈管理密钥。'; return; }
    history.value = ['']; await load('', candidate, true);
  }
  async function refresh() { history.value = ['']; filter.value = ''; await load(); }
  async function page(forward: boolean) {
    const next = forward ? cursor.value : history.value[history.value.length - 2];
    if (next === undefined || (forward && !next)) return;
    await load(next);
    if (!error.value) history.value = forward ? [...history.value, next] : history.value.slice(0, -1);
  }
  async function open(item: FeedbackItem) {
    if (busy.value) return;
    const current = ++generation; busy.value = true; error.value = ''; detail.value = null;
    files.value = {}; previewReady.value = false; selectedFile.value = '';
    try {
      const value = await request('get', { id: item.id, include_archive: false }, token);
      if (current === generation) detail.value = value;
    } catch (failure) {
      if (current === generation) error.value = failure instanceof Error ? failure.message : '无法读取详情。';
    } finally { if (current === generation) busy.value = false; }
  }
  async function archive(download = false) {
    if (busy.value || !detail.value?.has_archive) return;
    const current = ++generation, id = detail.value.id;
    busy.value = true; error.value = '';
    try {
      const value = await request('get', { id, include_archive: true }, token);
      if (current !== generation) return;
      if (typeof value.archive !== 'string' || value.archive.length > 12 * 1024 * 1024) throw new Error('诊断包超过读取上限。');
      const bytes = Uint8Array.from(atob(value.archive), c => c.charCodeAt(0));
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
      if (current !== generation) return;
      if (digest !== value.sha256) throw new Error('诊断包校验失败，请重新读取。');
      if (download) {
        const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }));
        const link = document.createElement('a'); link.href = url; link.download = 'Bugg-feedback-' + id.replace(/[^A-Za-z0-9_-]/g, '') + '.zip';
        link.click(); URL.revokeObjectURL(url);
      } else {
        let total = 0, count = 0;
        files.value = unzipSync(bytes, { filter: entry => {
          if (++count > 128 || entry.originalSize > 1024 * 1024 || !/\.(json|jsonl|log|txt|xml)$/i.test(entry.name)) return false;
          total += entry.originalSize; return total <= 8 * 1024 * 1024;
        } });
        selectedFile.value = Object.keys(files.value).find(name => name.endsWith('latest.log')) || Object.keys(files.value)[0] || '';
        previewReady.value = true;
      }
    } catch (failure) {
      if (current === generation) error.value = failure instanceof Error ? failure.message : '无法读取诊断包。';
    } finally { if (current === generation) busy.value = false; }
  }
  function notification(item: FeedbackItem) {
    return item.notified_at ? '邮件已通知' : item.mail_error ? '邮件发送失败，将重试' : '等待邮件通知';
  }
  return { keyInput, connected, busy, error, items, visibleItems, detail, cursor, history, filter, files,
    selectedFile, previewReady, preview, login, refresh, page, open, archive, notification, dispose: clear };
}
