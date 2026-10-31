/* Apply the saved palette before styles load. Only the theme preference is stored. */
(() => {
  const key = 'buggcraft.theme';
  const root = document.documentElement;
  let theme = 'dark';
  try { if (localStorage.getItem(key) === 'light') theme = 'light'; } catch (_) {}
  root.dataset.theme = theme;

  function refresh() {
    const button = document.getElementById('theme-toggle');
    if (!button) return;
    const light = root.dataset.theme === 'light';
    button.textContent = light ? '深色模式' : '浅色模式';
    button.setAttribute('aria-label', light ? '切换到深色主题' : '切换到浅色主题');
    button.setAttribute('aria-pressed', String(light));
    button.title = button.getAttribute('aria-label');
  }
  function mount() {
    const nav = document.querySelector('.topbar > nav, .nav-links, .theme-nav');
    if (!nav || document.getElementById('theme-toggle')) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.id = 'theme-toggle';
    button.className = 'theme-toggle';
    button.addEventListener('click', () => {
      root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
      try { localStorage.setItem(key, root.dataset.theme); } catch (_) {}
      refresh();
    });
    nav.append(button);
    refresh();
  }
  window.addEventListener('storage', event => {
    if (event.key === key) {
      root.dataset.theme = event.newValue === 'light' ? 'light' : 'dark';
      refresh();
    }
  });
  window.addEventListener('buggcraft:page-ready',mount);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
/* BUGGCRAFT PAGE SHELL */
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
  document.head.append(style);
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
