'use strict';
// Passwords and decrypted identity keys remain in page memory, never localStorage.
let identityKey = null, vault = null, accountRecord = null, importedRecord = null, unlockedPassword = '', launcherKey = null, mailEnrollment = null, emailRequired = false;
let loginGrant = null, loginProof = null;
let returnView = null;
const requestedAccountNext=new URLSearchParams(location.search).get('next')||'';
const accountNext=/^\/account\/(access|friends|profile|security)$/.test(requestedAccountNext)||['/feedback','/feedback?new=1'].includes(requestedAccountNext)?requestedAccountNext:'';
const utf8 = new TextEncoder(), textDecoder = new TextDecoder();
function bytes64(bytes) { return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,''); }
function from64(text) { return Uint8Array.from(atob(text.replace(/-/g,'+').replace(/_/g,'/')), c=>c.charCodeAt(0)); }
function ordered(value) { if(Array.isArray(value)) return value.map(ordered); if(value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k=>[k,ordered(value[k])])); return value; }
function canonicalBytes(value) { return utf8.encode(JSON.stringify(ordered(value)).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'))); }
async function routingState(state, copies, key) {
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',canonicalBytes(state))), b=>b.toString(16).padStart(2,'0')).join('');
  const routing={account:state.account,revision:state.revision,copies,digest};
  return {routing,routing_signature:await signBytes(key,canonicalBytes(routing))};
}
function accountEntry(view='login') {
  if(location.pathname.startsWith('/account/'))returnView=location.pathname.split('/').pop();
  $('account-panel').hidden=true;$('account-login').hidden=view!=='login';$('account-register').hidden=view!=='register';$('account-feedback').textContent='';
  history.replaceState(null,'',(view==='register'?'/register':'/login')+(accountNext?'?next='+encodeURIComponent(accountNext):''));
  document.title=(view==='register'?'注册账号':'账号登录')+' · Buggcraft';
  $('page-title').textContent=view==='register'?'注册账号':'账号登录';
  $('recovery-import').hidden=view==='register';
}
$('show-register').onclick=()=>accountEntry('register');
$('back-to-login').onclick=()=>accountEntry();
let mailBusy=false, mailRetryAt=0, mailTimer=null;
function mailFeedback(message,state='pending') {
  const el=$('email-code-feedback');el.textContent=message;el.dataset.state=state;
}
function renderMailButton() {
  const remaining=Math.max(0,Math.ceil((mailRetryAt-Date.now())/1000));
  const button=$('send-email-code');button.disabled=mailBusy || remaining>0;
  button.textContent=mailBusy ? '正在发送…' : remaining>0 ? remaining+' 秒后重发' : '发送验证码';
  button.setAttribute('aria-busy',String(mailBusy));
  if(!mailBusy && remaining===0 && mailTimer) {clearInterval(mailTimer);mailTimer=null;}
}
for(const id of ['register-name','register-email']) $(id).addEventListener('input',()=>{
  if(mailEnrollment) {
    mailEnrollment=null;$('register-email-code').value='';
    mailFeedback('用户名或邮箱已更改，请重新发送验证码。','error');
  }
});
$('send-email-code').onclick=async()=>{
  if(mailBusy || Date.now()<mailRetryAt) return;
  const emailField=$('register-email'),nameField=$('register-name');
  const email=emailField.value.trim(),username=nameField.value.trim();
  nameField.value=username;emailField.value=email;
  if(!nameField.reportValidity() || !emailField.reportValidity()) return;
  mailBusy=true;mailEnrollment=null;renderMailButton();
  emailField.readOnly=true;nameField.readOnly=true;
  mailFeedback('正在连接邮件服务，请稍候…');$('account-feedback').textContent='';
  try {
    const enrollment=await api('/api/account/enroll',{});
    const result=await api('/api/account/email-code',{nonce:enrollment.nonce,email,username},40000);
    mailEnrollment={...enrollment,email,username,ticket:result.ticket};
    mailRetryAt=Date.now()+result.retry_after*1000;
    mailFeedback('验证码已发往 '+email+'，10 分钟内有效。未收到请检查垃圾邮件。','success');
    if(mailTimer) clearInterval(mailTimer);
    mailTimer=setInterval(renderMailButton,250);
    if(!$('account-register').hidden) $('register-email-code').focus();
  }catch(e){mailFeedback(e.message || String(e),'error');}
  finally{mailBusy=false;emailField.readOnly=false;nameField.readOnly=false;renderMailButton();}
};
async function signBytes(key,bytes) { return bytes64(await crypto.subtle.sign({name:'ECDSA',hash:'SHA-256'},key,bytes)); }
async function boxKey(password,salt) {
  const raw=await crypto.subtle.importKey('raw',utf8.encode(password),'PBKDF2',false,['deriveKey']);
  return crypto.subtle.deriveKey({name:'PBKDF2',hash:'SHA-256',salt,iterations:600000},raw,{name:'AES-GCM',length:256},false,['encrypt','decrypt']);
}
async function sealVault(password,account,revision,value) {
  const salt=crypto.getRandomValues(new Uint8Array(16)),iv=crypto.getRandomValues(new Uint8Array(12));
  const payload=utf8.encode(JSON.stringify(value));
  if(payload.length>4000) throw new Error('个人设置过长，请缩短昵称或备忘。');
  const ciphertext=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:utf8.encode(account+'|'+revision)},await boxKey(password,salt),payload);
  return {salt:bytes64(salt),iv:bytes64(iv),ciphertext:bytes64(ciphertext),iterations:600000};
}
async function unlock(record,password) {
  const state=record.state,box=state.box;
  if(box.iterations!==600000 || from64(box.salt).length!==16 || from64(box.iv).length!==12 || from64(box.ciphertext).length>4096) throw new Error('账号恢复数据无效。');
  let value;
  try {
    const raw=await crypto.subtle.decrypt({name:'AES-GCM',iv:from64(box.iv),additionalData:utf8.encode(state.account+'|'+state.revision)},await boxKey(password,from64(box.salt)),from64(box.ciphertext));
    value=JSON.parse(textDecoder.decode(raw));
  } catch { throw new Error('密码错误或加密数据已损坏。'); }
  const key=await crypto.subtle.importKey('jwk',value.key,{name:'ECDSA',namedCurve:'P-256'},true,['sign']);
  const publicPart={kty:value.key.kty,crv:value.key.crv,x:value.key.x,y:value.key.y};
  if(JSON.stringify(ordered(publicPart))!==JSON.stringify(ordered(record.certificate.public_key))) throw new Error('身份密钥与账号不一致。');
  const pub=await crypto.subtle.importKey('jwk',publicPart,{name:'ECDSA',namedCurve:'P-256'},false,['verify']);
  if(!await crypto.subtle.verify({name:'ECDSA',hash:'SHA-256'},pub,from64(record.signature),canonicalBytes(state))) throw new Error('账号设置签名无效。');
  return {key,value};
}
function exportRecovery(record) {
  const blob=new Blob([JSON.stringify({format:'buggcraft-account-v1',record},null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='buggcraft-account-'+record.state.account.slice(0,12)+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function renderBackupStatus(b) {
  $('account-backup-status').textContent=b.copies==='legacy' ? '旧版账号：请选择备份策略后加密保存，确认同步范围。' : b.copies===0 ? '仅注册节点保存身份，请保管恢复文件。' : '最新密文在线副本 '+b.available+' / '+b.target+' 份（含注册节点）；'+(b.available<b.target ? '等待可用节点补足。' : '备份已就绪。');
}
async function showAccount() {
  const next=accountNext;
  const me=await api('/api/account/me');accountCSRF=me.csrf;
  $('account-current').textContent=me.account;$('account-id').value=me.account;
  $('welcome-name').textContent=me.username;
  $('account-code-identity').textContent=me.username+' · 个人身份 · 支持好友';
  $('account-unlock').hidden=!!identityKey;
  $('recovery-import').hidden=true;
  $('account-home').textContent='注册节点身份 '+me.home.slice(0,12)+' · 加密设置版本 '+me.revision;
  const b=me.backup;
  $('account-copies').value=typeof b.copies==='number' ? b.copies : '0';
  renderBackupStatus(b);
  $('account-panel').hidden=false;$('account-login').hidden=true;$('account-register').hidden=true;
  $('account-nickname').value=vault?.nickname || me.username;$('account-notes').value=vault?.notes || '';
  $('account-save').disabled=!identityKey;$('account-export').disabled=!accountRecord;
  if(!identityKey) $('account-feedback').textContent='已登录。生成个人启动器接入码或修改资料前，请验证密码。';
  accountView(returnView || (next.startsWith('/account/')?next.split('/').pop():location.pathname.startsWith('/account/')?location.pathname.split('/').pop():'overview'));
  returnView=null;
  if(next==='/feedback'||next==='/feedback?new=1'){location.assign(next);return;}
  try{await refreshFriends();}catch(e){feedback('friend-feedback',e);}
}
$('open-account').onclick=async event=>{
  event.preventDefault();
  if(!crypto.subtle) { feedback('account-feedback','浏览器需要 HTTPS 和 WebCrypto 支持。'); return; }
  try { await showAccount(); } catch { accountEntry();await refresh(); }
};
$('account-register').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  const originalLabel=button.textContent;button.textContent='正在创建账号…';button.setAttribute('aria-busy','true');
  $('account-feedback').textContent='正在加密账号资料，请稍候…';
  try {
    if(mailBusy) throw new Error('验证码正在发送，请稍候再注册。');
    const password=$('register-password').value;
    if(password!==$('register-confirm').value) throw new Error('两次密码不一致。');
    if(emailRequired && (!mailEnrollment || mailEnrollment.email!==$('register-email').value.trim() || mailEnrollment.username!==$('register-name').value)) throw new Error('请先发送并填写邮箱验证码。');
    const enrollment=emailRequired ? mailEnrollment : await api('/api/account/enroll',{});
    const pair=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);
    const exported=await crypto.subtle.exportKey('jwk',pair.privateKey);
    const pub=await crypto.subtle.exportKey('jwk',pair.publicKey);
    const publicKey={kty:pub.kty,crv:pub.crv,x:pub.x,y:pub.y};
    const value={key:exported,nickname:$('register-name').value,notes:'',email:$('register-email').value.trim()};
    const state={account:enrollment.account,revision:1,box:await sealVault(password,enrollment.account,1,value)};
    const signature=await signBytes(pair.privateKey,canonicalBytes(state));
    const copies=Number($('register-copies').value),routing=await routingState(state,copies,pair.privateKey);
    await api('/api/account/register',{nonce:enrollment.nonce,username:$('register-name').value,email_ticket:enrollment.ticket,email_code:$('register-email-code').value,public_key:publicKey,state,signature,copies,...routing,proof:await signBytes(pair.privateKey,utf8.encode(enrollment.message))});
    mailEnrollment=null;
    const record=await api('/api/account/record/'+enrollment.account);
    exportRecovery(record);$('account-id').value=$('register-name').value;$('account-password').value=password;
    $('register-password').value='';$('register-confirm').value='';
    await loginAccount();
    $('account-feedback').textContent='注册成功。以后使用用户名登录；请妥善保管密码和已下载的恢复文件。';
  } catch(e) { feedback('account-feedback',e); } finally {button.disabled=false;button.textContent=originalLabel;button.setAttribute('aria-busy','false');}
};
async function loginAccount() {
  const password=$('account-password').value;
  const identifier=$('account-id').value.trim();
  const pair=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);
  const pub=await crypto.subtle.exportKey('jwk',pair.publicKey), delegation={kty:pub.kty,crv:pub.crv,x:pub.x,y:pub.y};
  const challenge=importedRecord && importedRecord.state.account===identifier ? await api('/api/account/recover',{record:importedRecord,delegation}) : await api('/api/account/challenge',{account:identifier,delegation});
  const grant=JSON.parse(challenge.message);
  if(grant.p!=='buggcraft-account-login-v2'||grant.o!==location.origin||grant.r!==challenge.nonce||grant.a!==challenge.record.state.account||JSON.stringify(ordered(grant.d))!==JSON.stringify(ordered(delegation))) throw new Error('登录挑战与当前网页或账号不匹配。');
  let record=challenge.record;
  if(importedRecord && importedRecord.state.account===record.state.account && importedRecord.state.revision>=record.state.revision) record=importedRecord;
  const unlocked=await unlock(record,password);
  const proof=await signBytes(unlocked.key,utf8.encode(challenge.message));
  const result=await api('/api/account/login',{nonce:challenge.nonce,signature:proof});
  loginGrant=grant;loginProof=proof;
  identityKey=unlocked.key;vault=unlocked.value;accountRecord=record;unlockedPassword=password;accountCSRF=result.csrf;launcherKey=await crypto.subtle.exportKey('jwk',pair.privateKey);
  $('account-password').value='';importedRecord=null;await showAccount();$('account-feedback').textContent='已登录。个人资料只在当前网页解密。';
}
$('account-login').onsubmit=async event=>{event.preventDefault();const button=event.submitter,label=button.textContent;button.disabled=true;button.textContent='正在登录…';button.setAttribute('aria-busy','true');$('account-feedback').textContent='正在验证账号，请稍候…';try{await loginAccount();}catch(e){feedback('account-feedback',e);}finally{button.disabled=false;button.textContent=label;button.setAttribute('aria-busy','false');}};
$('account-save').onclick=async()=>{
  const button=$('account-save');button.disabled=true;
  try {
    if(!identityKey || !accountRecord) throw new Error('请重新登录解锁身份。');
    const revision=accountRecord.state.revision+1;
    const next={...vault,nickname:$('account-nickname').value,notes:$('account-notes').value};
    const state={account:accountRecord.state.account,revision,box:await sealVault(unlockedPassword,accountRecord.state.account,revision,next)};
    const signature=await signBytes(identityKey,canonicalBytes(state));
    const routing=await routingState(state,Number($('account-copies').value),identityKey);
    await api('/api/account/backup',{state,signature,...routing});vault=next;accountRecord={...accountRecord,state,signature,...routing};
    await showAccount();$('account-feedback').textContent='已按所选策略加密保存；副本状态以实际在线确认数量为准。';
  }catch(e){feedback('account-feedback',e);}finally{button.disabled=!identityKey;}
};
$('account-connect').onclick=async()=>{
  const button=$('account-connect');button.disabled=true;
  try {
    if(!launcherKey) { $('account-unlock').click();return; }
    const result=await api('/api/profile',{format:Number($('access-code-format').value)});
    if(result.identity!=='account') throw new Error('登录已过期，请重新登录后生成个人启动器接入码。');
    const claims=JSON.parse(textDecoder.decode(from64(result.credential.split('.')[1])));
    const expected={kty:launcherKey.kty,crv:launcherKey.crv,x:launcherKey.x,y:launcherKey.y};
    if(JSON.stringify(ordered(claims.g?.d))!==JSON.stringify(ordered(expected))) throw new Error('账号已在其他标签页重新登录，请在当前页面验证密码后再生成接入码。');
    if(result.code.startsWith('buggnode1-')) {
      const profile={...result.profile,token:result.credential+'~'+launcherKey.d};
      $('player-code').value='buggnode1-'+bytes64(utf8.encode(JSON.stringify(profile)));
    } else $('player-code').value=result.code+'~'+launcherKey.d;
    $('personal-result').hidden=false;$('connect-feedback').textContent='已生成。请妥善保管，不要发给他人。';
  }catch(e){feedback('account-feedback',e);}finally{button.disabled=false;}
};
$('account-unlock').onclick=()=>{returnView=location.pathname.startsWith('/account/')?location.pathname.split('/').pop():'overview';const username=$('welcome-name').textContent;accountEntry();$('account-id').value=username;$('account-feedback').textContent='请验证密码后继续。';$('account-password').focus();};
$('copy-code').onclick=async()=>{try{await navigator.clipboard.writeText($('player-code').value);$('connect-feedback').textContent='已复制。粘贴到启动器的「添加节点」。';}catch{$('player-code').select();$('connect-feedback').textContent='请复制已选中的启动器接入码。';}};
$('account-export').onclick=()=>{if(accountRecord) exportRecovery(accountRecord);};
$('account-import').onchange=async event=>{
  try { const file=event.target.files[0];if(!file) return;if(file.size>20000) throw new Error('恢复文件过大。');
    const value=JSON.parse(await file.text());if(value.format!=='buggcraft-account-v1'||!value.record?.state?.account) throw new Error('恢复文件格式无效。');
    importedRecord=value.record;accountEntry();$('account-id').value=value.record.state.account;$('account-feedback').textContent='恢复文件已载入。输入原密码后可解锁；源节点已丢失时，登录后选择至少 1 个备份节点并保存，建立新的持久副本。';
  }catch(e){feedback('account-feedback',e);}finally{event.target.value='';}
};
$('account-logout').onclick=async()=>{
  try{await api('/api/account/logout',{});identityKey=null;vault=null;accountRecord=null;importedRecord=null;unlockedPassword='';accountCSRF='';launcherKey=null;loginGrant=null;loginProof=null;accountEntry();$('account-password').value='';$('account-notes').value='';$('account-nickname').value='';$('player-code').value='';$('personal-result').hidden=true;$('connect-feedback').textContent='';$('account-feedback').textContent='已退出。已复制的个人启动器接入码在原有效期内仍可用。';await refresh();}catch(e){feedback('account-feedback',e);}
};
// Refresh acknowledgements without overwriting settings the user is editing.
let backupRefreshing=false;
setInterval(async()=>{
  if(backupRefreshing || document.hidden || $('account-panel').hidden) return;
  backupRefreshing=true;
  try { renderBackupStatus((await api('/api/account/me')).backup);await refreshFriends(); }
  catch(e){if(e.status===401){identityKey=null;vault=null;accountRecord=null;unlockedPassword='';launcherKey=null;loginGrant=null;loginProof=null;$('player-code').value='';$('personal-result').hidden=true;accountEntry();$('account-feedback').textContent='登录已过期，请重新登录。';}else feedback('account-feedback',e);}
  finally { backupRefreshing=false; }
},5000);
