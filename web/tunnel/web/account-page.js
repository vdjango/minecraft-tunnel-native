// Account navigation is local; room/friend/presence rules stay in the native control service.
const views={overview:['联机账号','管理登录与启动器设备。'],access:['连接启动器','所有节点使用此控制服务登录。'],devices:['登录设备','撤销不再使用的设备。'],email:['登录邮箱','验证邮箱后可使用邮箱登录。']};
function accountView(view='overview') {
  if(!views[view])view='overview';
  const [title,description]=views[view];
  $('page-title').textContent=title;$('page-description').textContent=description;document.title=title+' · Buggcraft';
  document.body.dataset.accountState='authenticated';
  history.replaceState(null,'',(view==='overview'?'/account':'/account/'+view)+(accountLauncherRequest?'?launcher='+encodeURIComponent(accountLauncherRequest):''));
  for(const section of document.querySelectorAll('[data-view]'))section.hidden=section.dataset.view!==view;
  for(const link of document.querySelectorAll('[data-account-view]')){
    link.setAttribute('aria-current',link.dataset.accountView===view?'page':'false');
    link.onclick=event=>{event.preventDefault();accountView(link.dataset.accountView);};
  }
  $('account-feedback').textContent='';
  if(view==='devices')loadLauncherDevices();
  if(view==='overview')loadMemories();
}
function memoryDuration(seconds){
  if(seconds<60)return seconds>0?'不到 1 分钟':'还没有记录';
  if(seconds<3600)return Math.floor(seconds/60)+' 分钟';
  const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60);
  return hours+' 小时'+(minutes?' '+minutes+' 分钟':'');
}
function showCompanionMemories(data){
  const target=$('memory-reunion');target.replaceChildren();
  const known=data.companions.filter(friend=>Number(friend.last_connected)>0);
  const paragraph=(text)=>{const row=document.createElement('p');row.textContent=text;target.append(row);};
  if(!known.length){paragraph(data.friends_count?'有些相遇发生在记录开始之前。那些日子没有保存下来，但好友还在。以后一起连接时，新的回忆会从这里慢慢长出来。':'第一段共同的回忆，还在前面等你。回到启动器，认识伙伴，发出第一次联机邀请吧。');return;}
  const closest=[...known].sort((a,b)=>b.seconds-a.seconds)[0];
  if(closest.seconds>0)paragraph('和 '+closest.name+'，已经一起连接了 '+memoryDuration(closest.seconds)+'。原来，那些“再玩一会儿”，已经积攒了这么久。');
  const distant=[...known].filter(friend=>Date.now()/1000-Number(friend.last_connected)>=30*86400).sort((a,b)=>a.last_connected-b.last_connected).slice(0,3);
  for(const friend of distant){
    const days=Math.max(1,Math.floor((Date.now()/1000-Number(friend.last_connected))/86400));
    paragraph(friend.name+' · 已有 '+days+' 天没有记录到你们一起连接。上一次是 '+formatDeviceTime(friend.last_connected)+'。也许某个周末，可以再问一句：“还玩吗？”');
  }
  if(!distant.length)paragraph('最近的一次相遇：'+[...known].sort((a,b)=>b.last_connected-a.last_connected)[0].name+'。有空时，在启动器里邀请老朋友再出发吧。');
}
async function loadMemories(){
  const owner=authenticatedAccount;
  $('memory-status').textContent='正在收集我们的小足迹…';
  try{
    const data=await api('/api/account/memories');
    if(owner!==authenticatedAccount||!owner)return;
    const joined=Number(data.joined_at)*1000;
    const days=Number.isFinite(joined)?Math.max(1,Math.floor((Date.now()-joined)/86400000)+1):null;
    $('memory-days').textContent=days===null?'暂无记录':days.toLocaleString('zh-CN')+' 天';
    $('memory-greeting').textContent=days>1?'不知不觉，Bugg 已陪伴你 '+days.toLocaleString('zh-CN')+' 天。':'故事从今天开始。愿你在这里，遇到一起创造世界的人。';
    $('memory-multiplayer').textContent=memoryDuration(data.multiplayer_seconds);
    $('memory-friends').textContent=Number(data.friends_count).toLocaleString('zh-CN')+' 位';
    const calendar=$('memory-calendar');calendar.replaceChildren();
    const byDay=new Map(data.days.map(day=>[day.day,day]));
    const today=new Date(Date.now()+8*3600000);today.setUTCHours(0,0,0,0);
    for(let index=29;index>=0;index--){
      const date=new Date(today.getTime()-index*86400000),key=date.toISOString().slice(0,10),record=byDay.get(key);
      const seconds=record?.multiplayer_seconds||0,online=record?.online_seconds||0;
      const cell=document.createElement('button');cell.type='button';cell.className='memory-day';cell.setAttribute('role','listitem');
      const description=key+' · 联机连接 '+memoryDuration(seconds)+' · 启动器在线 '+memoryDuration(online);
      cell.dataset.level=String(seconds>=3600?4:seconds>=600?3:seconds>0?2:online>0?1:0);
      cell.title=description;cell.setAttribute('aria-label',description);cell.textContent=String(date.getUTCDate());
      cell.onclick=()=>{$('memory-calendar-note').textContent=description;};calendar.append(cell);
    }
    $('memory-calendar-note').textContent=data.days.length?'点选日期，看看那天留下的足迹。':'还没有活动记录。下一次连接，会留下新的足迹。';
    const companions=$('memory-companions');companions.replaceChildren();
    for(const friend of data.companions){
      const item=document.createElement('button');item.type='button';item.className='memory-friend';item.textContent=friend.name||'未命名的伙伴';
      item.dataset.weight=String(friend.seconds>=3600?3:friend.seconds>0?2:1);
      item.title=friend.last_connected?'最近同时连接 '+formatDeviceTime(friend.last_connected)+' · 累计 '+memoryDuration(friend.seconds):'已经是好友；旧版本没有保存共同连接时间。';
      item.setAttribute('aria-label',item.textContent+'，'+item.title);item.addEventListener('click',()=>{$('memory-friend-note').textContent=item.textContent+' · '+item.title;});companions.append(item);
    }
    if(!data.companions.length){const empty=document.createElement('p');empty.textContent='这里等着新的名字。回到启动器，邀请伙伴一起玩吧。';companions.append(empty);}
      showCompanionMemories(data);
      $('memory-online').textContent='已记录的启动器在线时间：'+memoryDuration(data.online_seconds)+'。多设备重叠时间只计一次。';
    $('memory-status').textContent=data.recording_since?'活动记录始于 '+formatDeviceTime(data.recording_since)+'；按已确认心跳估算。':'历史时长未记录，不会把已有账号的年龄算作游玩时间。';
  }catch(error){if(owner===authenticatedAccount)$('memory-status').textContent='小足迹暂时无法读取，稍后重新打开账号页即可重试。';}
}
async function refresh() {
  const s=await api('/api/status');$('show-register').hidden=!s.registration_open;emailRequired=!!s.email_verification;
  $('email-registration-fields').hidden=!emailRequired;$('register-email').required=emailRequired;$('register-email-code').required=emailRequired;
  if(!s.registration_open && location.pathname==='/register'){accountEntry();$('account-feedback').textContent='此服务暂未开放注册，已有账号仍可登录。';}
}
(async()=>{
  try{await refresh();if(!crypto.subtle)throw new Error('请使用支持 WebCrypto 的浏览器，通过 HTTPS 访问。');
    try{await showAccount();}catch(e){if(e.status!==401)throw e;accountEntry(location.pathname==='/register'?'register':location.pathname==='/forgot-password'?'recovery':'login');}
  }catch(e){feedback('account-feedback',e);}
})();
