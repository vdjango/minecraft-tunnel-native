'use strict';
const $ = id => document.getElementById(id);
let csrf = '', selectedNode = null, loading = false, accountRows=[];
function renderUsers(){
  const query=$('admin-user-filter').value.trim().toLowerCase();
  const filtered=accountRows.filter(a=>(a.username+' '+a.account+' '+a.home_name).toLowerCase().includes(query));
  $('admin-user-count').textContent=filtered.length+' / '+accountRows.length+' 个用户';
  const labels={home:'本机注册资料',replica:'持久加密副本',temporary:'临时加密资料',directory:'仅公开目录'};
  $('admin-accounts').replaceChildren(...filtered.map(a=>{
    const row=document.createElement('tr');
    const identity=document.createElement('td'),name=document.createElement('strong'),id=document.createElement('span');name.className='user-name';name.textContent=a.username;id.className='user-id';id.textContent=a.account.slice(0,8)+'…'+a.account.slice(-6);id.title=a.account;identity.append(name,id);row.append(identity);
    for(const value of [a.home_name,a.directory_synced?'已同步':'待同步',labels[a.storage],a.backup.available+' / '+a.backup.target]){const td=document.createElement('td');td.textContent=value;row.append(td);}
    const td=document.createElement('td'),button=document.createElement('button');button.className='secondary';button.textContent=a.blocked?'恢复访问':'停用本地访问';button.onclick=async()=>{button.disabled=true;try{await api('/api/account/block',{account:a.account,blocked:!a.blocked});await loadAdmin(false);}catch(e){message(e);}finally{button.disabled=false;}};td.append(button);row.append(td);return row;
  }));
}
function renderPlayers(players=[]){
  $('admin-player-count').textContent=players.length+' 个当前在线';
  const roles={host:'房主',client:'访客'},paths={p2p:'P2P直连',relay:'节点中转',mixed:'混合',auto:'协商中',unknown:'待确认',unavailable:'不可用'};
  const latency=value=>Number.isFinite(value)?value.toFixed(1)+' ms':'—';
  $('admin-players').replaceChildren(...players.map(player=>{
    const row=document.createElement('tr');
    for(const value of [player.username,roles[player.role]||player.role,player.room,paths[player.path]||player.path,
      latency(player.peer_rtt_ms),latency(player.control_rtt_ms),latency(player.game_rtt_ms)]){
      const cell=document.createElement('td');cell.textContent=value;row.append(cell);
    }
    return row;
  }));
}
$('admin-user-filter').oninput=renderUsers;
const capacityFields = [['max-rooms','max_rooms'],['room-capacity','room_capacity'],['bandwidth','bandwidth_mb_s'],['reserve','reserve_percent'],['player-budget','per_player_mb_s']];
async function api(path, data) {
  const response = await fetch(path, {method:data === undefined ? 'GET' : 'POST',
    headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
    ...(data === undefined ? {} : {body:JSON.stringify(data)})});
  const text = await response.text();
  let value;
  try { value = JSON.parse(text); } catch { value = {error:text}; }
  if (!response.ok) throw new Error(value.error || '请求失败，请稍后重试。');
  return value;
}
function message(value, id = 'admin-feedback') { $(id).textContent = value.message || String(value); }
function clearAction() {
  selectedNode = null; $('node-action-form').hidden = true; $('node-action-form').reset(); $('action-feedback').textContent = '';
}
function renderNodes(state) {
  $('console-subtitle').textContent = state.name;
  $('peer-count').textContent = state.peers.length + ' 个已知节点';
  $('admin-peers').replaceChildren(...state.peers.map(node => {
    const row = document.createElement('article'); row.className = 'admin-node';
    const info = document.createElement('div'), title = document.createElement('strong'), id = document.createElement('code'), detail = document.createElement('small');
    const states = {online:'直连正常',indirect:'间接心跳正常',suspect:'暂时不可达',offline:'离线或尚未连通',blocked:'本节点已隔离'};
    title.textContent = node.name; id.textContent = node.id;
    detail.textContent = (node.local ? '本节点 · ' : '联邦成员 · ') + (states[node.status] || node.status) +
      (node.heartbeat_age === null ? '' : ' · 心跳 ' + node.heartbeat_age + ' 秒前') +
      (node.direct_age === null || node.local ? '' : ' · 直连成功 ' + node.direct_age + ' 秒前');
    info.append(title, id, detail); row.append(info);
    if (node.manageable) {
      const button = document.createElement('button'); button.className = 'secondary';
      button.textContent = node.blocked ? '恢复连接…' : '本地隔离…';
      button.onclick = () => {
        clearAction(); selectedNode = {id:node.id, blocked:!node.blocked};
        $('action-title').textContent = (node.blocked ? '恢复与 ' : '隔离 ') + node.name;
        $('action-description').textContent = node.blocked ?
          '恢复后允许本节点重新接受该成员的签名心跳与连接。请先核实问题已解决。' :
          '本节点将拒绝此成员的联邦连接和目录，不接收其新心跳；该成员在其他节点仍可使用。持有入网密钥者可能换身份重入。';
        $('action-node-id').textContent = node.id; $('node-action-form').hidden = false;
        $('node-action-form').scrollIntoView({block:'center'}); $('action-reason').focus();
      };
      row.append(button);
    } else { const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = '当前节点'; row.append(badge); }
    return row;
  }));
  $('node-history').replaceChildren(...state.node_decisions.map(entry => {
    const row = document.createElement('div'); row.className = 'history-entry';
    const title = document.createElement('strong'), reason = document.createElement('p'), meta = document.createElement('code');
    title.textContent = (entry.action === 'isolate' ? '本地隔离 · ' : '恢复连接 · ') + entry.name;
    reason.textContent = entry.reason; meta.textContent = new Date(entry.at * 1000).toLocaleString() + ' · ' + entry.id;
    row.append(title, reason, meta); return row;
  }));
  if (!state.node_decisions.length) $('node-history').textContent = '还没有节点隔离或恢复记录。';
}
async function loadAdmin(fill = true) {
  if (loading) return;
  loading = true;
  try {
    const state = await api('/api/admin'); csrf = state.csrf;
    renderAdminBandwidth(state.bandwidth);
    $('login-form').hidden = true; $('admin-panel').hidden = false;
    renderNodes(state);
    renderPlayers(state.players);
    accountRows=state.accounts;renderUsers();
    if (fill) {
      if (!$('mail').hidden) {
        const mail = await api('/api/admin/mail');
        for (const key of ['host','port','username','from_name']) $('mail-' + key.replace('_','-')).value = mail[key];
        $('mail-required').checked = mail.required;
      }
      $('setting-name').value = state.name; $('setting-public').checked = state.public_join;
      $('setting-proxies').value=state.trusted_proxy_cidrs||'';
      $('setting-region').value=state.region||'';
      $('setting-drain').checked = state.draining; $('setting-registration').checked = state.registration_open;
      for (const [id, key] of capacityFields) $('setting-' + id).value = state.capacity[key];
    }
  } finally { loading = false; }
}
$('login-form').onsubmit = async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  const password = $('admin-password').value; $('admin-password').value = '';
  try { const result = await api('/api/login', {username:$('username').value,password}); csrf = result.csrf; await loadAdmin(); message(''); }
  catch (e) { message(e); } finally { button.disabled = false; }
};
$('settings-form').onsubmit = async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const capacity = Object.fromEntries(capacityFields.map(([id,key]) => [key,Number($('setting-' + id).value)]));
    await api('/api/settings', {name:$('setting-name').value,region:$('setting-region').value,public_join:$('setting-public').checked,
      draining:$('setting-drain').checked,registration_open:$('setting-registration').checked,capacity,
      trusted_proxy_cidrs:$('setting-proxies').value});
    message('设置已保存。'); await loadAdmin(false);
  } catch (e) { message(e); } finally { button.disabled = false; }
};
$('cancel-action').onclick = clearAction;
$('node-action-form').onsubmit = async event => {
  event.preventDefault(); if (!selectedNode) return;
  const button = event.submitter; button.disabled = true;
  const data = {...selectedNode, reason:$('action-reason').value, confirmation:$('action-confirmation').value,
    password:$('action-password').value}; $('action-password').value = '';
  try {
    await api('/api/federation/block', data); clearAction(); await loadAdmin(false);
    message('本地规则已保存，其他节点的配置不受影响。');
  } catch (e) { message(e,'action-feedback'); } finally { button.disabled = false; }
};
$('export-invite').onclick = async () => {
  try { $('federation-invite').value = JSON.stringify(await api('/api/federation/invite'), null, 2); message('邀请包含入网密钥，请私下交给可信管理员。'); }
  catch (e) { message(e); }
};
$('join-network').onclick = async event => {
  const button = event.currentTarget; button.disabled = true;
  try { await api('/api/federation/join', JSON.parse($('federation-invite').value)); $('federation-invite').value = ''; await loadAdmin(false); message('已保存连接，节点将在数秒内同步。'); }
  catch (e) { message(e); } finally { button.disabled = false; }
};
$('leave-network').onclick = () => { $('leave-form').hidden = false; };
$('cancel-leave').onclick = () => { $('leave-form').hidden = true; $('leave-form').reset(); };
$('leave-form').onsubmit = async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try { await api('/api/federation/leave', {}); $('leave-form').hidden = true; $('leave-form').reset(); clearAction(); await loadAdmin(false); message('当前节点已退出联邦。'); }
  catch (e) { message(e); } finally { button.disabled = false; }
};
$('logout').onclick = async () => {
  try {
    await api('/api/logout', {}); csrf = ''; clearAction(); $('admin-panel').hidden = true;
    $('login-form').hidden = false; $('federation-invite').value = ''; $('leave-form').hidden = true; message('');
  } catch (e) { message(e); }
};
loadAdmin().catch(() => {});
for (const link of document.querySelectorAll('.console-nav a')) {
  const current = location.pathname === '/admin' ? '/admin/nodes' : location.pathname;
  if (link.getAttribute('href') === current) link.setAttribute('aria-current', 'page');
}
if ($('mail-form')) $('mail-form').onsubmit = async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    await api('/api/admin/mail', {host:$('mail-host').value, port:Number($('mail-port').value),
      username:$('mail-username').value, password:$('mail-password').value,
      from_name:$('mail-from-name').value, required:$('mail-required').checked});
    $('mail-password').value = ''; message('邮件设置已保存。', 'mail-feedback');
  } catch (e) { message(e, 'mail-feedback'); } finally { button.disabled = false; }
};
if ($('mail-test-form')) $('mail-test-form').onsubmit = async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try { await api('/api/admin/mail/test', {email:$('mail-recipient').value}); message('测试邮件已发出，请检查收件箱。', 'mail-feedback'); }
  catch (e) { message(e, 'mail-feedback'); } finally { button.disabled = false; }
};
setInterval(() => {
  if (csrf && !document.hidden) loadAdmin(false).catch(e => message(e));
}, 5000);
