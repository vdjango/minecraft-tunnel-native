// Device approval stays on this node. Only the device public key reaches the browser.
const launcherRequest = accountLauncherRequest;
const launcherApproval = $('launcher-approval');
const accountPanel = $('account-panel');
if (launcherRequest && launcherApproval) {
  launcherApproval.hidden = false;
  document.querySelectorAll('[data-view]').forEach(section => {section.hidden = true;});
  if (accountPanel && !accountPanel.hidden) accountPanel.hidden = true;
  api('/api/launcher/info?id=' + encodeURIComponent(launcherRequest)).then(info => {
    $('launcher-device-name').textContent = info.name;
    $('launcher-device-policy').textContent = info.remember
      ? '保持登录：30 天未使用失效，最长 180 天。请核对启动器中显示的设备指纹。'
      : '仅授权 8 小时。请核对启动器中显示的设备指纹。';
    $('launcher-device-fingerprint').textContent = info.fingerprint;
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
    $('launcher-device-feedback').textContent = '已授权，请返回启动器。启动器会自动完成连接，无需复制接入码。';
    button.textContent = '已授权';
    await loadLauncherDevices();
  } catch (error) {$('launcher-device-feedback').textContent = error.message;}
  finally {if (button.textContent !== '已授权') button.disabled = false;}
};
async function loadLauncherDevices() {
  if (!$('launcher-device-list') || $('account-panel').hidden) return;
  try {
    const {devices} = await api('/api/launcher/devices');
    $('launcher-device-list').replaceChildren(...devices.map(device => {
      const row = document.createElement('p'), name = document.createElement('span');
      name.textContent = device.name + (device.active ? ' · 已登录' : ' · 已失效');row.append(name);
      if (device.active) {
        const button = document.createElement('button');button.type = 'button';button.className = 'secondary';button.textContent = '撤销登录';
        button.onclick = async () => {button.disabled = true;try {await api('/api/launcher/revoke', {id: device.id});await loadLauncherDevices();}
          catch (error) {feedback('account-feedback', error);button.disabled = false;}};
        row.append(' ', button);
      }
      return row;
    }));
    if (!devices.length) $('launcher-device-list').textContent = '暂无启动器登录设备。';
  } catch (error) {if (error.status !== 401) feedback('account-feedback', error);}
}
loadLauncherDevices();
setInterval(loadLauncherDevices, 15000);
if (launcherRequest) {
  new MutationObserver(() => {
    if (accountPanel && !accountPanel.hidden) {accountPanel.hidden = true;launcherApproval.hidden = false;}
  }).observe(accountPanel, {attributes:true, attributeFilter:['hidden']});
}
