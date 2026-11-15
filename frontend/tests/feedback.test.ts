// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useFeedback } from '../src/feedback';

afterEach(() => vi.restoreAllMocks());
describe('feedback administrator session', () => {
  it('keeps failed login input, clears successful input and requests metadata without the ZIP', async () => {
    const replies = [new Response('{}', {status:401}), new Response(JSON.stringify({items:[{id:'one'}],next_cursor:'two'})),
      new Response(JSON.stringify({id:'one',message:'problem',has_archive:true})), new Response(JSON.stringify({items:[{id:'two'}],next_cursor:''}))];
    const fetch = vi.spyOn(window,'fetch').mockImplementation(async () => replies.shift()!);
    const model = useFeedback(); model.keyInput.value = 'x'.repeat(48);
    await model.login(); expect(model.connected.value).toBe(false); expect(model.keyInput.value).toHaveLength(48);
    await model.login(); expect(model.connected.value).toBe(true); expect(model.keyInput.value).toBe('');
    await model.open(model.items.value[0]);
    expect(JSON.parse(String(fetch.mock.calls[2][1]?.body))).toEqual({id:'one',include_archive:false});
    await model.page(true); expect(model.history.value).toHaveLength(2); expect(model.items.value[0].id).toBe('two');
    expect(sessionStorage.length).toBe(0); expect(localStorage.length).toBe(0);
    model.dispose(); expect(model.items.value).toEqual([]); expect(model.detail.value).toBe(null);
  });
  it('discards a late response when the page is disposed', async () => {
    let finish!: (response: Response) => void;
    vi.spyOn(window,'fetch').mockImplementation(() => new Promise(resolve => {finish=resolve;}));
    const model=useFeedback(); model.keyInput.value='x'.repeat(48);
    const pending=model.login(); model.dispose();
    finish(new Response(JSON.stringify({items:[{id:'late'}]}))); await pending;
    expect(model.connected.value).toBe(false); expect(model.items.value).toEqual([]);
  });
});
