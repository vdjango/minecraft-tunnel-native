import {afterEach,describe,expect,it,vi} from 'vitest';
import {createScope,mountPage} from '../src/runtime';

afterEach(()=>{
  window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:false}));
  document.body.replaceChildren();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('page lifecycle',()=>{
  it('cancels timers, global listeners and resource subscriptions when closed',()=>{
    vi.useFakeTimers();
    const scope=createScope(), callback=vi.fn(), cleanup=vi.fn();
    scope.setTimeout(callback,20);scope.setInterval(callback,10);
    scope.listen(window,'example',callback);
    scope.onDispose(cleanup);
    scope.dispose();scope.dispose();
    window.dispatchEvent(new Event('example'));
    vi.advanceTimersByTime(100);
    expect(callback).not.toHaveBeenCalled();expect(cleanup).toHaveBeenCalledOnce();
  });
  it('runs ready callbacks even after DOMContentLoaded has already fired',async()=>{
    const scope=createScope(), callback=vi.fn();
    scope.ready(callback);
    await Promise.resolve();
    expect(callback).toHaveBeenCalledOnce();
    scope.dispose();
  });
  it('aborts a response whose headers arrived but body is still pending',async()=>{
    let signal:AbortSignal|undefined;
    vi.stubGlobal('fetch',vi.fn(async (_input,init)=>{
      signal=init.signal;
      return {json:()=>new Promise((_,reject)=>signal!.addEventListener('abort',()=>reject(signal!.reason),{once:true}))};
    }));
    const scope=createScope();
    const response=await scope.fetch('/slow');
    const body=response.json();
    const rejection=expect(body).rejects.toMatchObject({name:'AbortError'});
    expect(scope.state.pending).toBe(1);
    scope.dispose();
    await rejection;
    expect(signal!.aborted).toBe(true);expect(scope.state.pending).toBe(0);
  });
  it('times out stalled requests and forwards caller cancellation',async()=>{
    vi.useFakeTimers();
    vi.stubGlobal('fetch',vi.fn((_input,init)=>new Promise((_,reject)=>{
      if(init.signal.aborted)reject(init.signal.reason);
      else init.signal.addEventListener('abort',()=>reject(init.signal.reason),{once:true});
    })));
    const scope=createScope();
    const pending=scope.fetch('/never');
    const rejection=expect(pending).rejects.toMatchObject({name:'TimeoutError'});
    await vi.advanceTimersByTimeAsync(25000);
    await rejection;
    expect(scope.state.pending).toBe(0);
    const caller=new AbortController();
    const canceled=scope.fetch('/canceled',{signal:caller.signal});
    const canceledRejection=expect(canceled).rejects.toMatchObject({name:'AbortError'});
    caller.abort();await canceledRejection;
    scope.dispose();
  });
  it('finishes response accounting once and restores exposed compatibility functions',async()=>{
    const previous=()=>1;
    (window as any).legacyFunction=previous;
    vi.stubGlobal('fetch',vi.fn(async()=>({status:200,json:async()=>({ready:true})})));
    const scope=createScope();
    scope.expose('legacyFunction',()=>2);
    const response=await scope.fetch('/ready');
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ready:true});
    expect(scope.state.pending).toBe(0);
    scope.dispose();
    expect((window as any).legacyFunction).toBe(previous);
    delete (window as any).legacyFunction;
  });
  it('mounts once and preserves BFCache state until the document really closes',()=>{
    const initialize=vi.fn();
    mountPage(initialize);mountPage(initialize);
    expect(initialize).toHaveBeenCalledOnce();
    window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));
    expect(document.querySelector('[data-vue-page]')).not.toBeNull();
    window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:false}));
    expect(document.querySelector('[data-vue-page]')).toBeNull();
  });
});
