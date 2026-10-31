/* Shared progressive navigation. Server HTML remains the canonical page. */
(() => {
  if (window.__buggcraftShell) return;
  window.__buggcraftShell = true;
  const root = document.documentElement;
  const style = document.createElement('style');
  style.id = 'site-shell-style';
  style.textContent = `
  #site-load-bar{position:fixed;inset:0 auto auto 0;height:3px;width:100%;z-index:2147483647;pointer-events:none;transform:scaleX(0);transform-origin:left;background:linear-gradient(90deg,#8971f5,#57c8c4);transition:transform .2s,opacity .2s}
  html[data-page-loading] #site-load-bar{transform:scaleX(.75);animation:site-progress 1.8s ease-in-out infinite}
  #site-navigation-overlay{position:fixed;inset:76px 0 0;z-index:2147483600;background:#11111bee;backdrop-filter:blur(10px);padding:clamp(24px,5vw,76px);overflow:hidden;color:#e9e6fa;pointer-events:none}
  html[data-theme=light] #site-navigation-overlay{background:#f8f7fdef;color:#28223d}
  #site-navigation-overlay[hidden]{display:none}
  .site-load-heading{max-width:1140px;margin:12px auto 28px;display:flex;align-items:center;gap:12px;font:500 14px system-ui,sans-serif}
  .site-load-dot{width:8px;height:8px;border-radius:50%;background:#a18af8;box-shadow:0 0 22px #a18af866;animation:site-pulse 1.2s ease-in-out infinite}
  .site-skeleton-layout{max-width:1140px;margin:auto;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}
  .site-skeleton{height:190px;border:1px solid #a18af822;border-radius:18px;background:linear-gradient(105deg,#a18af808 30%,#a18af825 50%,#a18af808 70%);background-size:220% 100%;animation:site-shimmer 1.6s linear infinite}
  .site-skeleton:first-child{height:104px;grid-column:1/-1}.site-skeleton:after{content:'';display:block;margin:28px;width:48%;height:10px;border-radius:5px;background:#a18af822;box-shadow:0 25px 0 #a18af811,0 50px 0 #a18af811}
  [aria-busy=true]:empty{min-height:100px;border-radius:12px;background:linear-gradient(105deg,#a18af808 30%,#a18af825 50%,#a18af808 70%);background-size:220% 100%;animation:site-shimmer 1.6s linear infinite}
  .loading-value{display:inline-block;min-width:100px;min-height:26px;border-radius:6px;color:transparent!important;background:linear-gradient(105deg,#a18af808 30%,#a18af825 50%,#a18af808 70%);background-size:220% 100%;animation:site-shimmer 1.6s linear infinite}
  @keyframes site-shimmer{to{background-position:-220% 0}}@keyframes site-progress{50%{transform:scaleX(.9)}}@keyframes site-pulse{50%{opacity:.35}}
  @media(max-width:650px){.site-skeleton-layout{grid-template-columns:1fr}.site-skeleton{height:120px}#site-navigation-overlay{inset:64px 0 0}}
  @media(prefers-reduced-motion:reduce){#site-load-bar,.site-skeleton,.site-load-dot,.loading-value,[aria-busy=true]:empty{animation:none!important;transition:none!important}}
  `;
  if(!document.querySelector('[data-shell-styles]'))document.head.append(style);
  let overlay, bar, active, sequence = 0, booted = false, navigationStarted = false, pendingCount = 0;
  const modules = new Map();
  const dependencies = new Set([...document.querySelectorAll('script[src]')].map(node=>node.src));
  const ensure = () => {
    if (!document.body) return;
    if (!bar?.isConnected) { bar = document.createElement('div'); bar.id = 'site-load-bar'; bar.setAttribute('aria-hidden','true'); document.body.append(bar); }
    if (!overlay?.isConnected) {
      overlay = document.createElement('div'); overlay.id = 'site-navigation-overlay'; overlay.hidden = true;
      overlay.setAttribute('role','status'); overlay.setAttribute('aria-live','polite');
      overlay.innerHTML = '<div class="site-load-heading"><span class="site-load-dot" aria-hidden="true"></span><span data-load-label>正在准备页面…</span></div><div class="site-skeleton-layout" aria-hidden="true"><div class="site-skeleton"></div><div class="site-skeleton"></div><div class="site-skeleton"></div><div class="site-skeleton"></div></div>';
      document.body.append(overlay);
    }
  };
  const show = (label, cover = true) => { ensure(); root.dataset.pageLoading = 'true'; if(overlay){overlay.querySelector('[data-load-label]').textContent=label;overlay.hidden=!cover;} };
  const hide = () => { delete root.dataset.pageLoading; if(overlay)overlay.hidden=true; };
  const initial = () => { ensure(); if(!booted)show('正在准备页面…'); };
  if(document.body)initial();
  else {
    const firstBody=new MutationObserver(()=>{if(document.body){firstBody.disconnect();initial();}});
    firstBody.observe(root,{childList:true});
  }
  const documentReady=()=>{initial();if(!document.querySelector('script[type=module][src*="vue-page-"]')){booted=true;hide();}};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',documentReady,{once:true}); else documentReady();
  // A failed JS bundle cannot permanently conceal usable server-rendered content.
  setTimeout(() => { if(!navigationStarted)hide(); }, 8000);
  window.addEventListener('buggcraft:page-ready',()=>{booted=true;ensure();if(!navigationStarted){if(pendingCount>0)show('正在读取数据…',false);else hide();}});
  window.addEventListener('buggcraft:page-pending',event=>{
    pendingCount=event.detail;
    if(!navigationStarted && booted){if(event.detail>0)show('正在读取数据…',false);else hide();}
  });
  window.addEventListener('pageshow',()=>{navigationStarted=false;hide();});
  const entry = doc => doc.querySelector('script[type=module][src*="vue-page-"]');
  const pageURL = value => new URL(value, location.href);
  const downloadPath = url => /\.(exe|zip|jar|mrpack|deb|tar|gz|png|svg|webp|json|txt|pdf)(?:$|\/)/i.test(url.pathname)||/^\/(?:api|v1|assets|maven|java|mods|_mirror_node|mc\/game)\//.test(url.pathname);
  const syncHead = doc => {
    document.title=doc.title;
    const selector='meta[name="description"],meta[name="robots"],meta[property^="og:"],meta[name^="twitter:"],link[rel="canonical"],script[type="application/ld+json"]';
    document.head.querySelectorAll(selector).forEach(node=>node.remove());
    doc.head.querySelectorAll(selector).forEach(node=>document.head.append(document.importNode(node,true)));
    // Page styles may overlap. Remove only styles previously installed for navigation.
    document.head.querySelectorAll('[data-route-style]').forEach(node=>node.remove());
    doc.head.querySelectorAll('style,link[rel="stylesheet"]').forEach(node=>{
      if(node.tagName==='LINK' && [...document.head.querySelectorAll('link[rel="stylesheet"]')].some(old=>old.href===node.href))return;
      const copy=document.importNode(node,true);copy.dataset.routeStyle='';document.head.append(copy);
    });
  };
  async function prepareDependencies(doc, base) {
    for(const script of doc.querySelectorAll('script[src]:not([type=module])')){
      const url=new URL(script.getAttribute('src'),base).href;
      if(dependencies.has(url)||new URL(url).origin!==location.origin||/\/theme\.js(?:\?|$)/.test(url))continue;
      await new Promise((resolve,reject)=>{const node=document.createElement('script');node.src=url;node.onload=resolve;node.onerror=reject;document.head.append(node);});
      dependencies.add(url);
    }
  }
  async function navigate(url, historyMode='push') {
    active?.abort(); const controller=new AbortController();active=controller;
    const id=++sequence; navigationStarted=true;show('正在打开页面…');
    const timeout=setTimeout(()=>controller.abort(),15000);
    try {
      const response=await fetch(url.href,{signal:controller.signal,headers:{'Accept':'text/html'}});
      if(!response.ok||!response.headers.get('Content-Type')?.includes('text/html'))throw Error('Page unavailable');
      const doc=new DOMParser().parseFromString(await response.text(),'text/html');
      const script=entry(doc), current=entry(document);
      const destination=pageURL(response.url||url.href);
      if(!script||!current||destination.origin!==location.origin)throw Error('Full navigation required');
      const moduleURL=new URL(script.getAttribute('src'),destination.href).href;
      if(new URL(moduleURL).origin!==location.origin)throw Error('Invalid page entry');
      await prepareDependencies(doc,destination.href);
      if(id!==sequence)return;
      window.__buggcraftRouteLoading=true;
      window.dispatchEvent(new Event('buggcraft:before-navigate'));
      if(historyMode==='push')history.pushState({buggcraft:true},'',destination.href);
      else if(historyMode==='replace')history.replaceState({buggcraft:true},'',destination.href);
      syncHead(doc);
      document.body.className=doc.body.className;
      for(const key of Object.keys(document.body.dataset))delete document.body.dataset[key];
      Object.assign(document.body.dataset,doc.body.dataset);
      const nodes=[...doc.body.childNodes].filter(node=>node.nodeName!=='SCRIPT').map(node=>document.importNode(node,true));
      document.body.replaceChildren(...nodes);ensure();show('正在准备页面…');
      // Keep the current entry identifiable on the next navigation.
      current.remove();const marker=document.createElement('script');marker.type='module';marker.dataset.routeEntry='';marker.setAttribute('src',script.getAttribute('src'));
      let page=modules.get(moduleURL);
      if(!page){page=await import(/* @vite-ignore */ moduleURL);modules.set(moduleURL,page);}
      if(id!==sequence)return;
      marker.setAttribute('src',moduleURL);document.head.append(marker);
      window.__buggcraftRouteLoading=false;
      page.mount?.();
      navigationStarted=false;booted=true;hide();
      if(url.hash)document.getElementById(decodeURIComponent(url.hash.slice(1)))?.scrollIntoView();else window.scrollTo(0,0);
      const heading=document.querySelector('main h1');if(heading){heading.setAttribute('tabindex','-1');heading.focus({preventScroll:true});}
      window.dispatchEvent(new Event('buggcraft:navigated'));
    } catch(error) {
      if(id!==sequence)return;
      // A full navigation is the reliable fallback for authorization and old bundles.
      location.assign(url.href);
    } finally {clearTimeout(timeout);}
  }
  document.addEventListener('click',event=>{
    if(event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    const link=event.target instanceof Element?event.target.closest('a[href]'):null;
    if(!link||link.hasAttribute('download')||link.target&&link.target!=='_self'||link.hasAttribute('data-native-navigation'))return;
    const url=pageURL(link.href);
    if(!/^https?:$/.test(url.protocol)||downloadPath(url))return;
    if(url.pathname===location.pathname&&url.search===location.search&&url.hash)return;
    if(url.origin!==location.origin){show('正在前往 '+url.hostname+'…');return;}
    if(!entry(document)){show('正在打开页面…');return;}
    event.preventDefault();void navigate(url);
  });
  window.addEventListener('popstate',()=>{if(entry(document))void navigate(pageURL(location.href),'none');});
})();

/* Loading belongs to the data region, including refreshes of populated lists. */
(() => {
  if(window.__buggcraftListLoading)return;
  window.__buggcraftListLoading=true;
  const styles=`
  .list-region-frame{position:relative}.list-region-pending{min-height:140px}
  .list-refreshing{opacity:.55;transition:opacity .15s}
  .list-load-status{position:absolute;right:12px;top:10px;z-index:6;display:flex;align-items:center;gap:9px;padding:7px 12px;border:1px solid var(--line,#665c8d);border-radius:8px;background:var(--panel,#211b30);color:var(--text,#f5f1ff);box-shadow:0 4px 18px #0002;font:12px system-ui,sans-serif;max-width:calc(100% - 24px)}
  .list-load-status:before{content:'';width:7px;height:7px;border-radius:50%;background:#9c85ed;animation:site-pulse 1s ease-in-out infinite}
  .list-load-status[data-error]:before{background:#dc7186;animation:none}.list-load-status button{border:0;background:transparent;color:inherit;font:inherit;text-decoration:underline;cursor:pointer;white-space:nowrap}
  .list-skeleton-card{padding:20px;border:1px solid var(--line,#665c8d);border-radius:10px;margin:10px 0;min-height:88px}
  .list-skeleton-line{display:block;height:12px;min-width:32px;width:74%;border-radius:5px;margin:9px 0;background:linear-gradient(105deg,#a18af816 30%,#a18af840 50%,#a18af816 70%);background-size:220% 100%;animation:site-shimmer 1.6s linear infinite}
  .list-skeleton-line:nth-child(even){width:45%}.list-skeleton-row td{height:62px;padding:14px}
  .release-box[aria-busy=true]:has([aria-disabled=true]) h3,.release-box[aria-busy=true]:has([aria-disabled=true]) p{color:transparent;background:linear-gradient(105deg,#a18af811 30%,#a18af840 50%,#a18af811 70%);background-size:220% 100%;animation:site-shimmer 1.6s linear infinite;border-radius:5px}
  @media(prefers-reduced-motion:reduce){.list-skeleton-line,.list-load-status:before{animation:none}.list-refreshing{transition:none}}
  `;
  if(!document.querySelector('[data-shell-styles]')){const style=document.createElement('style');style.dataset.listLoadingStyles='';style.textContent=styles;document.head.append(style);}
  const groups=[
    [/\/v1\/admin\/mirror(?:$|\?)/,'#mirror-metrics,#mirror-source-statistics,#quality-sources','#mirror-refresh'],
    [/\/v1\/admin\/tasks(?:$|\?)/,'#tasks','#refresh-tasks'],
    [/\/v1\/admin\/usage|\/v1\/admin\/calls/,'#usage-calls,#usage-summary','#refresh-usage'],
    [/\/v1\/admin\/clients/,'#clients','#refresh-clients'],
    [/\/v1\/admin\/overview/,'#feedback,#metrics','#refresh-dashboard'],
    [/\/v1\/admin\/feedback/,'#feedback-list,#feedback-items,#feedback','#refresh-feedback'],
    [/\/v1\/admin\/nodes/,'#nodes,#node-list,#federation-nodes','#refresh-nodes'],
    [/\/v1\/admin\/traffic/,'#traffic-summary,#traffic-pages,#traffic-days','#refresh-traffic'],
    [/\/api\/status(?:$|\?)/,'#network-rows','#refresh-network'],
    [/\/api\/admin$/,'#admin-accounts,#admin-players,#node-rows,#node-history','#refresh-admin'],
    [/\/api\/admin\/diagnostics/,'#event-list','#refresh-diagnostics'],
    [/\/api\/admin\/nodes/,'#node-rows,#node-history','#refresh-nodes'],
    [/\/api\/admin\/accounts/,'#admin-accounts','#refresh-users'],
    [/\/api\/admin\/players/,'#admin-players','#refresh-players'],
    [/\/api\/feedback(?:$|\?)/,'#issue-list','#issues-refresh'],
    [/\/api\/account\/players/,'#online-players','#refresh-players'],
    [/\/api\/account\/social/,'#friend-list,#friend-requests','#refresh-friends'],
    [/\/api\/launcher\/devices/,'#launcher-device-list','#refresh-devices'],
    [/\/api\/admin\/launcher\/releases/,'#release-records,#release-list','#refresh-releases'],
    [/\/api\/admin\/launcher\/cloud/,'#cloud-assets','#retry-cloud'],
    [/\/api\/admin\/launcher\/files/,'#cloud-file-list','#cloud-file-refresh'],
    [/\/api\/launcher\/releases|\/release-history\.json/,'.release-box,#release-history','#refresh-downloads'],
  ];
  const states=new Map();
  function begin(element,retry){
    let state=states.get(element);
    if(state){state.count++;return state;}
    const frame=element.tagName==='TBODY'?element.closest('table').parentElement:element;
    frame.classList.add('list-region-frame');
    frame.querySelectorAll(':scope > .list-load-status[data-error]').forEach(n=>n.remove());
    const status=document.createElement('span');status.className='list-load-status';status.setAttribute('role','status');status.textContent='正在刷新…';
    state={element,frame,status,count:1,retry,original:null,skeleton:[],failed:false};states.set(element,state);
    element.setAttribute('aria-busy','true');
    const initial=!element.children.length||/^(正在|读取|加载|连接).*(…|\.\.\.)$/.test(element.textContent.trim());
    if(initial){
      status.textContent='正在加载…';state.original=[...element.childNodes];element.replaceChildren();element.classList.add('list-region-pending');
      const columns=element.closest('table')?.querySelectorAll('thead th').length||4;
      for(let i=0;i<4;i++){
        const row=document.createElement(element.tagName==='TBODY'?'tr':'div');row.className=element.tagName==='TBODY'?'list-skeleton-row':'list-skeleton-card';row.setAttribute('aria-hidden','true');
        if(element.tagName==='TBODY')for(let j=0;j<columns;j++){const cell=document.createElement('td'),line=document.createElement('span');line.className='list-skeleton-line';cell.append(line);row.append(cell);}
        else row.innerHTML='<span class="list-skeleton-line"></span><span class="list-skeleton-line"></span>';
        element.append(row);state.skeleton.push(row);
      }
    }else element.classList.add('list-refreshing');
    frame.append(status);return state;
  }
  function finish(state,failed=false){
    state.failed ||= failed;
    if(--state.count>0)return;
    requestAnimationFrame(()=>{
      if(states.get(state.element)!==state||state.count>0)return;
      states.delete(state.element);
      const {element,frame,status}=state;
      const retained=state.skeleton.some(node=>node.parentNode===element);
      state.skeleton.forEach(node=>node.remove());
      if(retained&&state.original)element.append(...state.original);
      element.removeAttribute('aria-busy');element.classList.remove('list-refreshing','list-region-pending');
      if(!state.failed||!element.isConnected){status.remove();return;}
      status.dataset.error='';status.textContent='加载未完成';const button=document.createElement('button');button.type='button';button.textContent='重试';
      button.onclick=()=>{status.remove();const refresh=document.querySelector(state.retry);if(refresh?.tagName==='SELECT')refresh.dispatchEvent(new Event('change',{bubbles:true}));else if(refresh)refresh.click();else location.reload();};status.append(button);
    });
  }
  const original=window.fetch;
  window.fetch=async function(input,init){
    const path=new URL(input instanceof Request?input.url:String(input),location.href).pathname;
    const method=(init?.method||(input instanceof Request?input.method:'GET')).toUpperCase();
    let group=groups.find(([pattern])=>pattern.test(path));
    if(group&&method!=='GET'){
      let action;try{action=JSON.parse(init?.body||'{}').action;}catch{}
      if(action==='source_files')group=[group[0],'#mirror-source-files','#mirror-source-filter'];
      else if(action==='quality_history')group=[group[0],'#quality-history','#quality-refresh'];
      else group=null;
    }
    const active=group?[...document.querySelectorAll(group[1])].filter(el=>el.getClientRects().length&& !['H1','H2','H3','SELECT','INPUT','TEXTAREA'].includes(el.tagName)).map(el=>begin(el,group[2])):[];
    let done=false;const complete=failed=>{if(done)return;done=true;clearTimeout(deadline);active.forEach(state=>finish(state,failed));};
    const deadline=setTimeout(()=>complete(true),30000);
    try{
      const response=await original.call(this,input,init);
      if(!active.length){complete(false);return response;}
      return new Proxy(response,{get(target,key){const value=Reflect.get(target,key,target);if(typeof value!=='function')return value;if(['json','text','blob','arrayBuffer','formData','bytes'].includes(String(key)))return async(...args)=>{try{const result=await value.apply(target,args);complete(!target.ok);return result;}catch(error){complete(true);throw error;}};return value.bind(target);}});
    }catch(error){complete(error?.name!=='AbortError');throw error;}
  };
  window.addEventListener('buggcraft:before-navigate',()=>{for(const state of states.values()){state.count=1;finish(state);}states.clear();});
})();
