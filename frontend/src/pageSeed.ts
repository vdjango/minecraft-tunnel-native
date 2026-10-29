/** Preserve server-rendered first paint and route/account state during Vue handoff. */
export function capturePageSeed(){
  const byId=(id:string)=>document.getElementById(id);
  const text=(selector:string)=>document.querySelector(selector)?.textContent||'';
  const html=(selector:string)=>document.querySelector(selector)?.innerHTML||'';
  const elements=new Map<string,{hidden:boolean;value?:string;className:string}>();
  for(const element of document.querySelectorAll<HTMLElement>('[id]')){
    elements.set(element.id,{hidden:element.hidden,className:element.className,
      ...('value' in element?{value:String((element as HTMLInputElement).value)}:{})});
  }
  const auth=byId('admin-access');
  const state={
    authMode:auth?.dataset.mode||'login',authTitle:text('#admin-access h2'),
    setupToken:(byId('setup-token') as HTMLInputElement|null)?.value||'',
    webPort:(document.querySelector('[name=web_port]') as HTMLInputElement|null)?.value||'18443',
    layoutClass:document.querySelector('.management-layout')?'management-layout':'',
    detail:byId('project-page')?.dataset.detail||'{}',title:text('#project-page h1'),
    summary:text('#project-summary'),status:text('#project-status'),
    tags:html('#project-page .tag-list'),options:html('#project-language'),
    body:html('#project-body'),original:html('#project-page details .prose'),
    info:html('#project-page .project-sidebar dl'),releases:Array.from(document.querySelectorAll('#project-page .detail-content>details')).slice(1).map(element=>element.outerHTML).join(''),
    sourceUrl:document.querySelector<HTMLAnchorElement>('#project-page .detail-actions>a')?.href||'',
  };
  return {state,restore(){
    for(const [id,previous] of elements){const current=byId(id);if(!current)continue;
      current.hidden=previous.hidden;
      if(previous.value!==undefined&&'value' in current)(current as HTMLInputElement).value=previous.value;
    }
    for(const a of document.querySelectorAll<HTMLAnchorElement>('nav a')){
      const path=location.pathname==='/admin'?'/admin/nodes':location.pathname;
      if(a.getAttribute('href')===path)a.setAttribute('aria-current','page');
    }
  }};
}
