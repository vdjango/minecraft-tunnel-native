'use strict';
const views={overview:['用户中心','管理你的联机身份、好友与个人资料。'],access:['连接启动器','使用节点 HTTPS 地址登录，不再需要接入码。'],players:['在线玩家','查看当前节点的游戏连接状态。'],friends:['我的好友','同一网络，不同节点，也能找到彼此。'],profile:['个人资料','昵称与备忘加密保存。'],security:['安全与备份','管理身份恢复文件和加密副本。']};
function setupNodeAddress(){
  const section=document.querySelector('#account-panel [data-view="access"]');
  if(!section||section.dataset.addressReady)return;
  const legacy=section.querySelector('.code-layout > div');
  if(!legacy)return;
  const identity=legacy.querySelector('#account-code-identity');
  if(identity)identity.remove();
  section.dataset.addressReady='true';
  const heading=document.createElement('h2');heading.textContent='连接启动器';
  const intro=document.createElement('p');intro.textContent='复制当前节点接入地址（域名或 IP），在启动器的「联机」页添加节点。新版本直接用地址即可接入，无需接入码。';
  const row=document.createElement('div');row.className='node-address-row';
  const address=document.createElement('code');address.id='node-address';address.textContent=location.origin;
  const copy=document.createElement('button');copy.id='copy-node-address';copy.type='button';copy.className='primary';copy.textContent='复制节点地址';
  const feedback=document.createElement('p');feedback.id='node-address-feedback';feedback.className='feedback';feedback.setAttribute('role','status');
  const note=document.createElement('p');note.className='node-address-note';note.textContent='接入码将在后续版本取消。旧版启动器暂时仍可使用下方兼容接入码。';
  const details=document.createElement('details');details.className='legacy-access';
  const summary=document.createElement('summary');summary.textContent='旧版启动器兼容：生成接入码';
  const legacyContent=document.createElement('div');legacyContent.className='legacy-access-content';
  const warning=document.createElement('p');warning.textContent='仅供尚不支持节点地址登录的旧版启动器临时使用。生成并复制后，在启动器「联机」→「添加节点」中粘贴；接入码最长有效 7 天，仅限本人使用。新版启动器直接填写上方节点接入地址即可。';
  const oldHeading=legacy.querySelector('h2');if(oldHeading)oldHeading.remove();
  const oldIntro=legacy.querySelector('p:not(.feedback)');if(oldIntro)oldIntro.remove();
  const oldHelp=section.querySelector('.code-help');if(oldHelp)oldHelp.remove();
  const typeLabel=legacy.querySelector('label');if(typeLabel&&typeLabel.textContent.includes('接入码'))typeLabel.firstChild.textContent='兼容格式';
  const create=legacy.querySelector('#account-connect');if(create){create.textContent='生成旧版接入码';create.className='secondary';}
  const output=legacy.querySelector('#personal-result');
  if(output){const label=output.querySelector('label');if(label)label.textContent='旧版启动器接入码';const button=output.querySelector('#copy-code');if(button){button.textContent='复制旧版接入码';button.className='secondary';}}
  const overview=document.querySelector('#account-panel [data-account-view="access"] span');
  if(overview)overview.textContent='复制节点地址并在启动器中登录';
  section.replaceChildren(heading);
  if(identity)section.append(identity);
  section.append(intro,row,feedback,note,details);
  row.append(address,copy);
  details.append(summary,legacyContent);
  legacyContent.append(warning,...legacy.childNodes);
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(location.origin);feedback.textContent='节点地址已复制。';}catch{feedback.textContent='复制失败，请手动复制上方地址。';}};
  details.addEventListener('toggle',()=>{if(details.open)details.scrollIntoView({block:'nearest'});});
}
setupNodeAddress();
function accountView(view='overview') {
  if(!views[view]) view='overview';
  const [title,description]=views[view];
  $('page-title').textContent=title;$('page-description').textContent=description;
  document.title=title+' · Buggcraft';
  const launcher=accountLauncherRequest;
  history.replaceState(null,'',(view==='overview'?'/account':'/account/'+view)+(launcher?'?launcher='+encodeURIComponent(launcher):''));
  for(const section of document.querySelectorAll('[data-view]')) section.hidden=!section.dataset.view.split(' ').includes(view);
  for(const link of document.querySelectorAll('[data-account-view]')) {
    link.setAttribute('aria-current',link.dataset.accountView===view?'page':'false');
    link.onclick=event=>{event.preventDefault();accountView(link.dataset.accountView);};
  }
  $('account-feedback').textContent='';
  if(view==='players') refreshOnlinePlayers().catch(e=>feedback('online-player-count',e));
}
async function refreshOnlinePlayers(){
  const result=await api('/api/account/players');
  const roles={host:'房主',client:'访客'},paths={p2p:'P2P直连',relay:'节点中转',mixed:'混合',auto:'协商中',unknown:'待确认',unavailable:'不可用'};
  const players=result.players||[];$('online-player-count').textContent=players.length+' 个全网在线'+(result.partial?'（部分节点暂不可达）':'');
  $('online-players').replaceChildren(...players.map(player=>{
    const row=document.createElement('tr');
    for(const value of [player.username,roles[player.role]||player.role,player.room,paths[player.path]||player.path,
      Number.isFinite(player.peer_rtt_ms)?player.peer_rtt_ms.toFixed(1)+' ms':'—']){
      const cell=document.createElement('td');cell.textContent=value;row.append(cell);
    }
    return row;
  }));
}
setInterval(()=>{
  if(location.pathname==='/account/players'&&!document.hidden&&!$('account-panel').hidden)
    refreshOnlinePlayers().catch(()=>{});
},5000);
async function refresh() {
  const s=await api('/api/status');
  $('show-register').hidden=!s.registration_open;
  emailRequired=!!s.email_verification;
  $('email-registration-fields').hidden=!emailRequired;
  $('register-email').required=emailRequired;$('register-email-code').required=emailRequired;
  if(!s.registration_open && location.pathname==='/register') {accountEntry();$('account-feedback').textContent='此节点暂未开放注册，已有账号仍可登录。';}
}
async function shortFriendCode(account) {
  const bytes=new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode('buggcraft-friend-v1:'+account)));
  const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';let code='',bits=0,value=0;
  for(const byte of bytes){value=(value<<8)|byte;bits+=8;while(bits>=5&&code.length<12){bits-=5;code+=alphabet[(value>>>bits)&31];}if(code.length===12)break;}
  return code.match(/.{4}/g).join('-');
}
function friendRow(item,label,op,ref) {
  const row=document.createElement('div');row.className='friend-row';
  const name=document.createElement('span');name.textContent=item.username+(item.online===true?' · 在线':item.online===false?' · 离线':'');
  const id=document.createElement('small');id.title=item.account;id.style.overflowWrap='anywhere';name.append(document.createElement('br'),id);row.append(name);
  shortFriendCode(item.account).then(code=>{id.textContent=code;}).catch(()=>{id.textContent=item.account;});
  if(label) {const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=label;
    button.onclick=async()=>{button.disabled=true;try{if(!await requireIdentity('操作好友关系需要确认账号身份'))return;await friendAction(op,item.account,ref);$('friend-feedback').textContent='操作已保存，其他节点同步后可见。';await refreshFriends();}catch(e){feedback('friend-feedback',e);}finally{button.disabled=false;}};row.append(button);}
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
  if(!state.friends.length) $('friend-list').textContent='还没有好友，用好友码或用户名查找你的朋友吧。';
  $('friend-requests').replaceChildren(...state.requests.map(item=>friendRow(item,item.incoming?'同意申请':'撤回申请',item.incoming?'accept':'remove',item.incoming?item.id:undefined)));
  if(!state.requests.length) $('friend-requests').textContent='暂无好友申请。';
}
$('friend-search').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;$('friend-feedback').textContent='正在查找…';$('friend-results').replaceChildren();
  try{const result=await api('/api/account/lookup',{username:$('friend-username').value.trim()});
    $('friend-results').replaceChildren(...result.users.filter(item=>item.account!==$('account-current').textContent).map(item=>friendRow(item,'添加好友','request')));
    $('friend-feedback').textContent=$('friend-results').childElementCount?'找到用户，发送申请后需对方同意。':'未找到其他用户。';
  }catch(e){feedback('friend-feedback',e);}finally{button.disabled=false;}
};
$('copy-friend-code').onclick=async()=>{
  try{await navigator.clipboard.writeText($('my-friend-code').textContent);$('friend-feedback').textContent='好友码已复制，发给朋友即可添加。';}
  catch(e){feedback('friend-feedback',e);}
};
(async()=>{
  try{await refresh();if(!crypto.subtle) throw new Error('请使用支持 WebCrypto 的浏览器，通过 HTTPS 访问。');
    try{await showAccount();}catch{accountEntry(location.pathname==='/register'?'register':'login');}
  }catch(e){feedback('account-feedback',e);}
})();
