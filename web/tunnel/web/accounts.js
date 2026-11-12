'use strict';
// Passwords and decrypted identity keys remain in page memory, never localStorage.
let identityKey = null, vault = null, accountRecord = null, mailEnrollment = null, emailRequired = false;
let emailLogin = null, emailSelection = null, bindEmailTicket = null;
const emailRetry = new Map();
let returnView = null;
let authenticatedAccount = '';
const requestedLauncher=new URLSearchParams(location.search).get('launcher')||'';
const accountLauncherRequest=/^[0-9a-f]{32}$/.test(requestedLauncher)?requestedLauncher:'';
const requestedAccountNext=new URLSearchParams(location.search).get('next')||'';
const accountNext=/^\/account\/(access|devices|email)$/.test(requestedAccountNext)?requestedAccountNext:'';
const utf8 = new TextEncoder(), textDecoder = new TextDecoder();
let pendingUnlock = null, unlockBusy = false;
function requireIdentity(reason) {
  if(identityKey && accountRecord) return Promise.resolve(true);
  if(pendingUnlock) return pendingUnlock.promise;
  const dialog=$('account-unlock-dialog');
  $('account-unlock-reason').textContent=reason+'。为保护账号，请输入密码继续。';
  $('account-unlock-password').value='';$('account-unlock-feedback').textContent='';
  let resolve;const promise=new Promise(done=>{resolve=done;});
  pendingUnlock={promise,resolve,account:authenticatedAccount};
  dialog.showModal();$('account-unlock-password').focus();return promise;
}
$('account-unlock-dialog').addEventListener('cancel',event=>{if(unlockBusy)event.preventDefault();});
$('account-unlock-dialog').addEventListener('close',()=>{
  $('account-unlock-password').value='';const pending=pendingUnlock;pendingUnlock=null;pending?.resolve(false);
});
$('account-unlock-cancel').onclick=()=>{if(!unlockBusy)$('account-unlock-dialog').close();};
$('account-unlock-form').onsubmit=async event=>{
  event.preventDefault();if(unlockBusy || !pendingUnlock)return;
  unlockBusy=true;const button=event.submitter;button.disabled=true;button.textContent='正在验证…';
  $('account-unlock-cancel').disabled=true;$('account-unlock-feedback').textContent='正在解锁加密身份，请稍候…';
  try {
    await loginAccount({password:$('account-unlock-password').value,identifier:pendingUnlock.account,unlockOnly:true});
    const pending=pendingUnlock;pendingUnlock=null;pending.resolve(true);$('account-unlock-dialog').close();
  }catch(e){feedback('account-unlock-feedback',e);$('account-unlock-password').value='';$('account-unlock-password').focus();}
  finally{unlockBusy=false;button.disabled=false;button.textContent='验证并继续';$('account-unlock-cancel').disabled=false;}
};
function bytes64(bytes) { return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,''); }
function from64(text) { return Uint8Array.from(atob(text.replace(/-/g,'+').replace(/_/g,'/')), c=>c.charCodeAt(0)); }
function ordered(value) { if(Array.isArray(value)) return value.map(ordered); if(value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k=>[k,ordered(value[k])])); return value; }
function canonicalBytes(value) { return utf8.encode(JSON.stringify(ordered(value)).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'))); }
function accountEntry(view='login') {
  document.body.dataset.accountState='entry';
  clearEmailLogin();bindEmailTicket=null;
  $('email-login-fields').hidden=!$('account-id').value.includes('@');
  if(location.pathname.startsWith('/account/'))returnView=location.pathname.split('/').pop();
  $('account-panel').hidden=true;$('account-login').hidden=view!=='login';$('account-register').hidden=view!=='register';$('account-recovery').hidden=view!=='recovery';$('account-feedback').textContent='';
  const query=new URLSearchParams();
  if(accountNext)query.set('next',accountNext);
  if(accountLauncherRequest)query.set('launcher',accountLauncherRequest);
  history.replaceState(null,'',(view==='register'?'/register':view==='recovery'?'/forgot-password':'/login')+(query.size?'?'+query:''));
  document.title=(view==='register'?'注册账号':'登录')+' · Buggcraft';
  $('page-title').textContent=view==='register'?'注册账号':view==='recovery'?'找回密码':'登录';
  $('page-description').textContent=view==='register'?'创建联机账号，和朋友在同一个世界碰面。':'登录联机账号，连接你的启动器。';
  if(view==='recovery')$('page-description').textContent='验证绑定邮箱，重新设置密码。账号与好友保持不变。';
  document.dispatchEvent(new CustomEvent('launcher-account-state',{detail:{authenticated:false}}));
}
$('show-register').onclick=()=>accountEntry('register');
$('back-to-login').onclick=()=>accountEntry();
$('show-recovery').onclick=()=>accountEntry('recovery');
$('recovery-back').onclick=()=>accountEntry();
function clearEmailLogin() {
  emailLogin=null;emailSelection=null;$('login-email-code').value='';
  $('email-account-choice').replaceChildren();$('email-account-picker').hidden=true;
  $('login-email-feedback').textContent='';
}
$('account-id').addEventListener('input',()=>{
  clearEmailLogin();$('email-login-fields').hidden=!$('account-id').value.includes('@');
});
async function sendMailboxCode(purpose, field, button, feedbackId) {
  const email=$(field).value.trim();
  if(!email || (field!=='account-id' && !$(field).reportValidity())) return;
  const retryKey=purpose+':'+email.toLowerCase();
  if(Date.now()<(emailRetry.get(retryKey)||0)) throw new Error('请等待 60 秒后重发验证码。');
  button.disabled=true;button.textContent='正在发送…';$(feedbackId).textContent='正在连接邮件服务，请稍候…';
  try {
    const result=await api('/api/account/email-code',{email,purpose},40000);
    emailRetry.set(retryKey,Date.now()+result.retry_after*1000);
    $(feedbackId).textContent='验证码已发往 '+email+'，10 分钟内有效。请检查收件箱或垃圾邮件。';
    return {...result,email};
  } finally {button.disabled=false;button.textContent='发送验证码';}
}
$('send-login-email').onclick=async()=>{
  clearEmailLogin();
  const field=$('account-id');field.readOnly=true;
  try{emailLogin=await sendMailboxCode('login','account-id',$('send-login-email'),'login-email-feedback');}
  catch(e){feedback('login-email-feedback',e);}finally{field.readOnly=false;}
};
$('verify-login-email').onclick=async()=>{
  const button=$('verify-login-email');button.disabled=true;
  try{
    if(!emailLogin || emailLogin.email!==$('account-id').value.trim()) throw new Error('请先发送邮箱验证码。');
    const result=await api('/api/account/email-challenge',{ticket:emailLogin.ticket,nonce:emailLogin.nonce,code:$('login-email-code').value});
    emailSelection={...result,email:emailLogin.email};
    const select=$('email-account-choice');select.replaceChildren();
    if(result.accounts.length>1 && !result.accounts.some(a=>a.preferred)) select.add(new Option('请选择要保留的账号',''));
    result.accounts.forEach((item,index)=>select.add(new Option(item.username+(result.accounts.filter(v=>v.username===item.username).length>1?'（账号 '+(index+1)+'）':'')+(item.preferred?'（首选）':''),item.account)));
    $('email-account-picker').hidden=!result.accounts.length;
    $('login-email-feedback').textContent=result.accounts.length ? '邮箱已验证。请选择账号并输入该账号的原密码。' : '此服务未找到该邮箱的账号。已有账号可用账号 ID 登录后绑定邮箱。';
  }catch(e){emailSelection=null;feedback('login-email-feedback',e);}finally{button.disabled=false;}
};
$('send-bind-email').onclick=async()=>{
  bindEmailTicket=null;const field=$('bind-email');field.readOnly=true;
  try{bindEmailTicket=await sendMailboxCode('link','bind-email',$('send-bind-email'),'bind-email-feedback');}
  catch(e){feedback('bind-email-feedback',e);}finally{field.readOnly=false;}
};
$('bind-email').addEventListener('input',()=>{bindEmailTicket=null;$('bind-email-code').value='';});
$('bind-email-save').onclick=async()=>{
  const button=$('bind-email-save');button.disabled=true;
  try{
    if(!bindEmailTicket || bindEmailTicket.email!==$('bind-email').value.trim()) throw new Error('请先向要绑定的邮箱发送验证码。');
    const result=await api('/api/account/email-accounts',{ticket:bindEmailTicket.ticket,code:$('bind-email-code').value});
    bindEmailTicket=null;$('bind-email-code').value='';
    $('bind-email-feedback').textContent='邮箱已验证，此账号已保留用于邮箱登录。'+(result.accounts.length>1?'该邮箱还有其他账号，可在邮箱登录时切换。':'');
  }catch(e){feedback('bind-email-feedback',e);}finally{button.disabled=false;}
};
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
  if(payload.length>4000) throw new Error('账号资料过长。');
  const ciphertext=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:utf8.encode(account+'|'+revision)},await boxKey(password,salt),payload);
  return {salt:bytes64(salt),iv:bytes64(iv),ciphertext:bytes64(ciphertext),iterations:600000};
}
async function passwordCheck(password,username){
  const policy=await api('/api/account/policy');
  const normalized=password.toLowerCase(),length=Array.from(password).length;
  if(length<policy.minimum_length||length>policy.maximum_length)throw new Error('密码需要 6–256 个字符。');
  if(policy.blocked_passwords.includes(normalized)||normalized===username.toLowerCase()||/^(.)\1+$/.test(normalized))throw new Error('这个密码太常见，请换一个至少 6 位的密码。');
  const digest=await crypto.subtle.digest('SHA-256',utf8.encode(normalized));
  return {password_length:length,password_digest:Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('')};
}
async function encryptedIdentity(password,account,revision,nickname,email){
  const pair=await crypto.subtle.generateKey({name:'ECDSA',namedCurve:'P-256'},true,['sign','verify']);
  const exported=await crypto.subtle.exportKey('jwk',pair.privateKey),pub=await crypto.subtle.exportKey('jwk',pair.publicKey);
  const public_key={kty:pub.kty,crv:pub.crv,x:pub.x,y:pub.y};
  const value={key:exported,nickname,notes:'',email};
  const state={account,revision,box:await sealVault(password,account,revision,value)};
  return {public_key,state,signature:await signBytes(pair.privateKey,canonicalBytes(state)),privateKey:pair.privateKey};
}
async function unlock(record,password) {
  const state=record.state,box=state.box;
  if(box.iterations!==600000 || from64(box.salt).length!==16 || from64(box.iv).length!==12 || from64(box.ciphertext).length>4096) throw new Error('账号加密数据无效。');
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
async function showAccount() {
  const me=await api('/api/account/me');accountCSRF=me.csrf;
  authenticatedAccount=me.account;
  $('welcome-name').textContent=me.username;
  if(vault?.email && !$('bind-email').value) $('bind-email').value=vault.email;
  $('account-panel').hidden=false;$('account-login').hidden=true;$('account-register').hidden=true;$('account-recovery').hidden=true;
  accountView(accountLauncherRequest?'access':returnView || (accountNext?accountNext.split('/').pop():location.pathname.split('/').pop()));
  document.dispatchEvent(new CustomEvent('launcher-account-state',{detail:{authenticated:true,username:me.username}}));
  returnView=null;await loadLauncherDevices();
}
$('account-register').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  const originalLabel=button.textContent;button.textContent='正在创建账号…';button.setAttribute('aria-busy','true');
  $('account-feedback').textContent='正在加密账号资料，请稍候…';
  try {
    if(mailBusy) throw new Error('验证码正在发送，请稍候再注册。');
    const password=$('register-password').value;
    if(password!==$('register-confirm').value) throw new Error('两次密码不一致。');
    const passwordPolicy=await passwordCheck(password,$('register-name').value);
    if(emailRequired && (!mailEnrollment || mailEnrollment.email!==$('register-email').value.trim() || mailEnrollment.username!==$('register-name').value)) throw new Error('请先发送并填写邮箱验证码。');
    const enrollment=emailRequired ? mailEnrollment : await api('/api/account/enroll',{});
    const {privateKey,...encrypted}=await encryptedIdentity(password,enrollment.account,1,$('register-name').value,$('register-email').value.trim());
    await api('/api/account/register',{nonce:enrollment.nonce,username:$('register-name').value,email_ticket:enrollment.ticket,email_code:$('register-email-code').value,...encrypted,...passwordPolicy,proof:await signBytes(privateKey,utf8.encode(enrollment.message))});
    mailEnrollment=null;
    $('account-id').value=$('register-name').value;$('account-password').value=password;
    $('register-password').value='';$('register-confirm').value='';
    await loginAccount({identifier:enrollment.account});
    $('account-feedback').textContent='注册成功。可用用户名、已验证邮箱或完整账号 ID 登录；请妥善保管密码。';
  } catch(e) { feedback('account-feedback',e); } finally {button.disabled=false;button.textContent=originalLabel;button.setAttribute('aria-busy','false');}
};
let recoveryTicket=null,recoverySelection=null;
$('recovery-email').addEventListener('input',()=>{recoveryTicket=null;recoverySelection=null;$('recovery-reset-fields').hidden=true;});
$('recovery-send').onclick=async()=>{
  const button=$('recovery-send');button.disabled=true;recoverySelection=null;$('recovery-reset-fields').hidden=true;
  try{
    if(!$('recovery-email').reportValidity())return;
    const email=$('recovery-email').value.trim();
    recoveryTicket={...await api('/api/account/email-code',{purpose:'recovery',email},40000),email};
    $('recovery-feedback').textContent='验证码已发送，10 分钟内有效。';
  }catch(e){feedback('recovery-feedback',e);}finally{button.disabled=false;}
};
$('recovery-verify').onclick=async()=>{
  const button=$('recovery-verify');button.disabled=true;
  try{
    if(!recoveryTicket||recoveryTicket.email!==$('recovery-email').value.trim())throw new Error('请先发送验证码。');
    const ticket=recoveryTicket;recoveryTicket=null;
    recoverySelection=await api('/api/account/recovery-verify',{ticket:ticket.ticket,nonce:ticket.nonce,code:$('recovery-code').value});
    if(!recoverySelection.accounts.length)throw new Error('此邮箱没有绑定可恢复的账号。请确认使用注册或绑定时的邮箱。');
    $('recovery-choice').replaceChildren(...recoverySelection.accounts.map((account,index)=>{
      const option=document.createElement('option');option.value=account.account;option.textContent=account.username+(recoverySelection.accounts.filter(v=>v.username===account.username).length>1?' · 账号 '+(index+1):'');return option;
    }));
    $('recovery-reset-fields').hidden=false;$('recovery-feedback').textContent='邮箱已验证，请选择账号并设置新密码。';
  }catch(e){feedback('recovery-feedback',e);}finally{button.disabled=false;}
};
$('account-recovery').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  try{
    const selected=recoverySelection?.accounts.find(v=>v.account===$('recovery-choice').value);
    if(!selected)throw new Error('请先验证绑定邮箱。');
    const password=$('recovery-password').value;
    if(password!==$('recovery-confirm').value)throw new Error('两次密码不一致。');
    const policy=await passwordCheck(password,selected.username);
    const {privateKey,...encrypted}=await encryptedIdentity(password,selected.account,selected.revision+1,selected.username,$('recovery-email').value.trim());
    void privateKey;
    const grant=recoverySelection.grant;recoverySelection=null;
    await api('/api/account/recovery-reset',{grant,account:selected.account,...encrypted,...policy});
    clearIdentity();$('account-recovery').reset();accountEntry();$('account-id').value=selected.username;
    $('account-feedback').textContent='密码已重设。账号与好友保留，旧设备已退出，请使用新密码登录。';
  }catch(e){feedback('recovery-feedback',e);}finally{button.disabled=false;}
};
async function loginAccount(options={}) {
  const password=options.password ?? $('account-password').value;
  let identifier=options.identifier ?? $('account-id').value.trim(), emailGrant=null;
  if(identifier.includes('@')) {
    if(!emailSelection || emailSelection.email!==identifier || !$('email-account-choice').value) throw new Error('请先验证邮箱，再选择要保留的账号。');
    identifier=$('email-account-choice').value;emailGrant=emailSelection.grant;
  }
  const challenge=await api('/api/account/challenge',{account:identifier});
  const grant=JSON.parse(challenge.message);
  if(grant.p!=='buggcraft-account-login-v2'||grant.o!==location.origin||grant.r!==challenge.nonce||grant.a!==challenge.record.state.account) throw new Error('登录挑战与当前网页或账号不匹配。');
  if(options.unlockOnly && challenge.record.state.account!==identifier) throw new Error('账号已变更，请重新登录。');
  let record=challenge.record;
  const unlocked=await unlock(record,password);
  const proof=await signBytes(unlocked.key,utf8.encode(challenge.message));
  const result=await api('/api/account/login',{nonce:challenge.nonce,signature:proof,...(emailGrant?{email_grant:emailGrant}:{})});
  if(!options.unlockOnly){clearEmailLogin();bindEmailTicket=null;}
  identityKey=unlocked.key;vault=unlocked.value;accountRecord=record;accountCSRF=result.csrf;
  if(options.unlockOnly){return;}
  $('account-password').value='';await showAccount();$('account-feedback').textContent='已登录。请选择要管理的功能。';
}
$('account-login').onsubmit=async event=>{event.preventDefault();const button=event.submitter,label=button.textContent;button.disabled=true;button.textContent='正在登录…';button.setAttribute('aria-busy','true');$('account-feedback').textContent='正在验证账号，请稍候…';try{await loginAccount();}catch(e){feedback('account-feedback',e);}finally{button.disabled=false;button.textContent=label;button.setAttribute('aria-busy','false');}};
function clearIdentity() {
  identityKey=null;vault=null;accountRecord=null;accountCSRF='';authenticatedAccount='';
  $('bind-email').value='';$('account-password').value='';
}
$('account-logout').onclick=async()=>{
  try{await api('/api/account/logout',{});clearIdentity();accountEntry();$('account-feedback').textContent='已退出网页账号。启动器设备可在登录设备中单独撤销。';await refresh();}
  catch(e){feedback('account-feedback',e);}
};
