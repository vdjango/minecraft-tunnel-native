import { createApp, defineComponent, onMounted, onBeforeUnmount, h, reactive, type Component } from 'vue';
import { createViews } from './views';
import { capturePageSeed } from './pageSeed';

export type PageScope = ReturnType<typeof createScope>;
export function createScope() {
  let active = true;
  const timers = new Set<number>();
  const controllers = new Set<AbortController>();
  const readyCallbacks = new Set<() => void>();
  const listeners = new AbortController();
  const exposed = new Map<string, unknown>();
  const cleanup = new Set<() => void>();
  const state = reactive({ pending: 0, error: '' });
  const scope = {
    state,
    setTimeout(handler: TimerHandler, delay?: number, ...args: unknown[]): number {
      const id = window.setTimeout(() => { timers.delete(id); if(active && typeof handler === 'function') handler(...args); }, delay);
      timers.add(id); return id;
    },
    clearTimeout(id?: number) { window.clearTimeout(id); if(id !== undefined)timers.delete(id); },
    setInterval(handler: TimerHandler, delay?: number, ...args: unknown[]): number {
      const id = window.setInterval(() => { if(active && typeof handler === 'function') handler(...args); }, delay);
      timers.add(id); return id;
    },
    clearInterval(id?: number) { window.clearInterval(id); if(id !== undefined)timers.delete(id); },
    async fetch(input: RequestInfo | URL, init: RequestInit = {}) {
      if(!active) throw new DOMException('Page closed', 'AbortError');
      const controller = new AbortController(); controllers.add(controller); state.pending++;
      const supplied=init.signal||(input instanceof Request?input.signal:undefined);
      const signal=controller.signal;
      const forwardAbort=()=>controller.abort(supplied?.reason);
      const deadline=window.setTimeout(()=>controller.abort(new DOMException('Request timed out','TimeoutError')),25000);
      let finished=false;
      const finish=()=>{if(!finished){finished=true;window.clearTimeout(deadline);supplied?.removeEventListener('abort',forwardAbort);controllers.delete(controller);state.pending--;}};
      signal.addEventListener('abort',finish,{once:true});
      if(supplied?.aborted)forwardAbort();else supplied?.addEventListener('abort',forwardAbort,{once:true});
      try {
        const response=await window.fetch(input, {...init, signal});
        return new Proxy(response,{get(target,key){
          const value=Reflect.get(target,key,target);
          if(typeof value!=='function')return value;
          if(['json','text','blob','arrayBuffer','formData','bytes'].includes(String(key)))return async(...args:unknown[])=>{try{return await value.apply(target,args);}finally{finish();}};
          return value.bind(target);
        }});
      }catch(error){finish();throw error;}
    },
    ready(callback: () => void) {
      const run = () => { readyCallbacks.delete(run); if(active)callback(); };
      if(document.readyState === 'loading') { readyCallbacks.add(run); document.addEventListener('DOMContentLoaded',run,{once:true}); }
      else queueMicrotask(run);
    },
    expose(name: string, value: unknown) {
      const target = window as unknown as Record<string,unknown>;
      exposed.set(name,target[name]); target[name]=value;
    },
    listen(target:EventTarget,event:string,callback:EventListenerOrEventListenerObject,options?:boolean|AddEventListenerOptions){
      target.addEventListener(event,callback,typeof options==='boolean'?{capture:options,signal:listeners.signal}:{...options,signal:listeners.signal});
    },
    onDispose(callback:()=>void){cleanup.add(callback);},
    dispose() {
      if(!active)return;
      active=false;
      listeners.abort();
      for(const id of timers) { window.clearTimeout(id); window.clearInterval(id); } timers.clear();
      for(const controller of controllers)controller.abort(); controllers.clear();
      for(const callback of readyCallbacks)document.removeEventListener('DOMContentLoaded',callback); readyCallbacks.clear();
      for(const callback of cleanup)callback();cleanup.clear();
      const target=window as unknown as Record<string,unknown>;
      for(const [name,previous] of exposed) {if(previous===undefined)delete target[name];else target[name]=previous;} exposed.clear();
    },
  };
  return scope;
}

/** Mount the complete visible page; server HTML remains available before JS loads. */
export function mountPage(initialize: (scope:PageScope,views:ReturnType<typeof createViews>)=>void, Page?:Component) {
  if(document.querySelector('[data-vue-page]'))return;
  const seed=capturePageSeed();
  const views=createViews();
  const host=document.createElement('div');
  host.className='vue-page-root'; host.dataset.vuePage='';
  if(Page){host.style.display='contents';document.body.replaceChildren(host);}
  else {host.hidden=true;document.body.append(host);}
  const application=createApp(defineComponent({
    setup() {
      const scope=createScope();
      onMounted(()=>{
        seed.restore();
        try{initialize(scope,views);}catch(error){
          scope.state.error=String(error);console.error(error);
          const feedback=document.createElement('p');feedback.setAttribute('role','alert');
          feedback.textContent='Page initialization failed. Refresh and try again.';
          (document.querySelector('main')||document.body).prepend(feedback);
          document.dispatchEvent(new CustomEvent('page-initialization-error',{detail:String(error)}));
        }
      });
      onBeforeUnmount(()=>{scope.dispose();views.dispose();});
      return ()=>Page?h(Page,{seed:seed.state,views}):h('span',{'data-pending':scope.state.pending});
    },
  }));
  application.mount(host);
  const close=(event:PageTransitionEvent)=>{if(!event.persisted){application.unmount();host.remove();window.removeEventListener('pagehide',close);}};
  window.addEventListener('pagehide',close);
}
