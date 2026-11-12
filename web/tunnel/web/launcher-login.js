// Device approval stays on the control service. Only the device public key reaches the browser.
const launcherRequest = accountLauncherRequest;
const launcherApproval = $('launcher-approval');
const accountPanel = $('account-panel');
if (launcherRequest && launcherApproval) {
  let requestReady = false;
  $('launcher-approve').disabled = true;
  launcherApproval.style.maxWidth = '640px';
  launcherApproval.style.margin = '24px auto';
  const accountLine = document.createElement('p');accountLine.id = 'launcher-account-name';
  launcherApproval.querySelector('h2').after(accountLine);
  const switchAccount = document.createElement('button');
  switchAccount.id = 'launcher-switch-account';switchAccount.type = 'button';
  switchAccount.className = 'secondary';switchAccount.textContent = '切换账号';
  launcherApproval.querySelector('.action-buttons').append(switchAccount);
  switchAccount.onclick = () => $('account-logout').click();
  function showLauncherAccount(state) {
    const authenticated = !!state.authenticated;
    launcherApproval.hidden = !authenticated;
    if(authenticated) {
      accountPanel.hidden = true;
      $('account-login').hidden = true;$('account-register').hidden = true;
      accountLine.textContent = '当前账号：' + state.username;
      $('page-title').textContent = '登录启动器';
      $('page-description').textContent = '核对当前账号和设备后确认，启动器将自动返回大厅。';
    } else {
      $('launcher-device-feedback').textContent = '';
      $('launcher-approve').textContent = '确认登录这台启动器';
      $('launcher-approve').disabled = !requestReady;
      $('page-title').textContent = '登录并连接启动器';
      $('page-description').textContent = '先登录联机账号，再确认要连接的启动器设备。';
    }
  }
  document.addEventListener('launcher-account-state',event => showLauncherAccount(event.detail));
  showLauncherAccount({authenticated:false});
  api('/api/launcher/info?id=' + encodeURIComponent(launcherRequest)).then(info => {
    $('launcher-device-name').textContent = info.name;
    $('launcher-device-policy').textContent = info.remember
      ? '保持登录：30 天未使用失效，最长 180 天。请核对启动器中显示的设备指纹。'
      : '仅授权 8 小时。请核对启动器中显示的设备指纹。';
    $('launcher-device-fingerprint').textContent = info.fingerprint;
    requestReady = true;$('launcher-approve').disabled = false;
  }).catch(error => {$('launcher-device-feedback').textContent = error.message;$('launcher-approve').disabled = true;});
}
if ($('launcher-approve')) $('launcher-approve').onclick = async () => {
  const button = $('launcher-approve'); button.disabled = true;
  try {
    if (!await requireIdentity('确认登录这台启动器需要验证账号身份')) return;
    const {grant} = await api('/api/launcher/prepare', {id: launcherRequest});
    if (grant.a !== accountRecord.state.account || grant.o !== location.origin ||
        grant.r !== launcherRequest || grant.p !== 'buggcraft-launcher-device-v1')
      throw new Error('设备授权请求不匹配，请回到启动器重新登录。');
    await api('/api/launcher/approve', {id: launcherRequest, signature: await signBytes(identityKey, canonicalBytes(grant))});
    $('launcher-device-feedback').textContent = '已授权，请返回启动器。启动器会自动完成连接并返回大厅。';
    button.textContent = '已授权';
    await loadLauncherDevices();
  } catch (error) {$('launcher-device-feedback').textContent = error.message;}
  finally {if (button.textContent !== '已授权') button.disabled = false;}
};
async function loadLauncherDevices() {
  if (!$('launcher-device-list') || $('account-panel').hidden || document.hidden) return;
  try {
    const {devices} = await api('/api/launcher/devices');
    $('launcher-device-list').replaceChildren(...devices.map(device => {
      const row = document.createElement('div'), name = document.createElement('strong'),details=document.createElement('small'),description=document.createElement('span');row.className='peer-row';
      name.textContent = device.name + (device.active ? ' · 已登录' : ' · 已失效');
      details.textContent=(device.client?.system||'未知系统')+' · Bugg 启动器 · 最近使用 '+formatDeviceTime(device.last_used);
      description.append(name,details);row.append(description);
      if (device.active) {
        const button = document.createElement('button');button.type = 'button';button.className = 'secondary';button.textContent = '撤销登录';
        button.onclick = async () => {button.disabled = true;try {await api('/api/launcher/revoke', {id: device.id});await loadLauncherDevices();}
          catch (error) {feedback('account-feedback', error);button.disabled = false;}};
        row.append(' ', button);
      }
      return row;
    }));
    if (!devices.length) $('launcher-device-list').textContent = '暂无启动器登录设备。';
    await loadBrowserSessions();
  } catch (error) {if (error.status !== 401) feedback('account-feedback', error);}
}
function formatDeviceTime(seconds){return seconds?new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(new Date(seconds*1000)):'暂无记录';}
async function loadBrowserSessions(){
  const {sessions}=await api('/api/account/sessions');
  $('browser-session-list').replaceChildren(...sessions.map(session=>{
    const row=document.createElement('div'),text=document.createElement('span'),title=document.createElement('strong'),detail=document.createElement('small');row.className='peer-row';
    title.textContent=(session.client?.system||'未知系统')+' · '+(session.client?.browser||'未知浏览器')+(session.current?' · 当前会话':'');
    detail.textContent='最近使用 '+formatDeviceTime(session.last_used);text.append(title,detail);row.append(text);
    if(!session.current){const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='退出此会话';button.onclick=async()=>{button.disabled=true;try{await api('/api/account/session-revoke',{id:session.id});await loadBrowserSessions();}catch(e){feedback('account-feedback',e);button.disabled=false;}};row.append(button);}
    return row;
  }));
}
loadLauncherDevices();
setInterval(loadLauncherDevices, 15000);
