'use strict';
const views={overview:['用户中心','管理你的联机身份、好友与个人资料。'],access:['连接启动器','生成你的启动器接入码。'],friends:['我的好友','同一网络，不同节点，也能找到彼此。'],profile:['个人资料','昵称与备忘加密保存。'],security:['安全与备份','管理身份恢复文件和加密副本。']};
function accountView(view='overview') {
  if(!views[view]) view='overview';
  const [title,description]=views[view];
  $('page-title').textContent=title;$('page-description').textContent=description;
  document.title=title+' · Buggcraft';
  history.replaceState(null,'',view==='overview'?'/account':'/account/'+view);
  for(const section of document.querySelectorAll('[data-view]')) section.hidden=!section.dataset.view.split(' ').includes(view);
  for(const link of document.querySelectorAll('[data-account-view]')) {
    link.setAttribute('aria-current',link.dataset.accountView===view?'page':'false');
    link.onclick=event=>{event.preventDefault();accountView(link.dataset.accountView);};
  }
  $('account-feedback').textContent='';
}
async function refresh() {
  const s=await api('/api/status');
  $('show-register').hidden=!s.registration_open;
  emailRequired=!!s.email_verification;
  $('email-registration-fields').hidden=!emailRequired;
  $('register-email').required=emailRequired;$('register-email-code').required=emailRequired;
  if(!s.registration_open && location.pathname==='/register') {accountEntry();$('account-feedback').textContent='此节点暂未开放注册，已有账号仍可登录。';}
}
function friendRow(item,label,op,ref) {
  const row=document.createElement('div');row.className='friend-row';
  const name=document.createElement('span');name.textContent=item.username+(item.online===true?' · 在线':item.online===false?' · 离线':'');row.append(name);
  if(label) {const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=label;button.disabled=!launcherKey;
    button.onclick=async()=>{button.disabled=true;try{await friendAction(op,item.account,ref);$('friend-feedback').textContent='操作已保存，其他节点同步后可见。';await refreshFriends();}catch(e){feedback('friend-feedback',e);}finally{button.disabled=!launcherKey;}};row.append(button);}
  return row;
}
async function friendAction(op,target,ref) {
  if(!launcherKey||!loginGrant) throw new Error('请验证密码后操作好友关系。');
  const action={op,target,at:Date.now()/1000,id:Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join(''),...(ref?{ref}:{})};
  const key=await crypto.subtle.importKey('jwk',launcherKey,{name:'ECDSA',namedCurve:'P-256'},false,['sign']);
  return api('/api/account/social',{event:{grant:loginGrant,proof:loginProof,action,signature:await signBytes(key,canonicalBytes(action))}});
}
async function refreshFriends() {
  const state=await api('/api/account/social');
  $('friends-summary').textContent=state.friends.length+' 位好友';
  $('friend-list').replaceChildren(...state.friends.map(item=>friendRow(item,'移除','remove')));
  if(!state.friends.length) $('friend-list').textContent='还没有好友，用用户名查找你的朋友吧。';
  $('friend-requests').replaceChildren(...state.requests.map(item=>friendRow(item,item.incoming?'同意申请':'撤回申请',item.incoming?'accept':'remove',item.incoming?item.id:undefined)));
  if(!state.requests.length) $('friend-requests').textContent='暂无好友申请。';
}
$('friend-search').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;$('friend-feedback').textContent='正在查找…';$('friend-results').replaceChildren();
  try{const result=await api('/api/account/lookup',{username:$('friend-username').value.trim()});
    $('friend-results').replaceChildren(...result.users.filter(item=>item.account!==$('account-current').textContent).map(item=>friendRow(item,'添加好友','request')));
    $('friend-feedback').textContent=$('friend-results').childElementCount?(launcherKey?'找到用户，发送申请后需对方同意。':'请先验证密码，再发送好友申请。'):'未找到其他用户。';
  }catch(e){feedback('friend-feedback',e);}finally{button.disabled=false;}
};
(async()=>{
  try{await refresh();if(!crypto.subtle) throw new Error('请使用支持 WebCrypto 的浏览器，通过 HTTPS 访问。');
    try{await showAccount();}catch{accountEntry(location.pathname==='/register'?'register':'login');}
  }catch(e){feedback('account-feedback',e);}
})();
