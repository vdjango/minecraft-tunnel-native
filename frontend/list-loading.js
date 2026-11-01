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
