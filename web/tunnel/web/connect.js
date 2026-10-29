'use strict';
function setupNodeAddress(){
  const accountCard=document.querySelector('.connect-options>.user-card');
  const guestCard=document.getElementById('connect-dialog');
  if(!accountCard||!guestCard)return;
  const steps=accountCard.querySelectorAll('.auth-steps li');
  if(steps.length>=3){steps[1].textContent='在启动器输入节点 HTTPS 地址';steps[2].textContent='浏览器确认账号登录';}
  const description=accountCard.querySelector('p');
  if(description)description.textContent='使用当前节点接入地址（域名或 IP）连接，无需接入码。首次登录在浏览器确认账号，此后启动器会自动保持登录。';
  const addressRow=document.createElement('div');addressRow.className='node-address-row';
  const address=document.createElement('code');address.textContent=location.origin;
  const copy=document.createElement('button');copy.type='button';copy.className='primary';copy.textContent='复制节点地址';
  const feedback=document.createElement('p');feedback.className='feedback';feedback.setAttribute('role','status');
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(location.origin);feedback.textContent='节点地址已复制。';}catch{feedback.textContent='复制失败，请手动复制上方地址。';}};
  accountCard.querySelector('.auth-steps').before(addressRow,feedback);addressRow.append(address,copy);
  const form=document.getElementById('connect-form');
  if(form&&!form.parentElement.matches('details')){
    const details=document.createElement('details');details.className='legacy-access';
    const summary=document.createElement('summary');summary.textContent='旧版访客接入码（后续取消）';
    const note=document.createElement('p');note.textContent='此访客接入码仅供旧版启动器临时使用：生成并复制后，在「联机」→「添加节点」中粘贴；未来会取消。新版启动器直接填写节点接入地址（域名或 IP）即可。';
    form.before(details);details.append(summary,note,form);
    form.querySelector('h2').textContent='旧版访客接入码';
    form.querySelector('button[type="submit"]').textContent='生成旧版访客接入码';
    const result=form.querySelector('#connect-result');
    if(result){result.querySelector('label').textContent='旧版启动器接入码';result.querySelector('#copy-code').textContent='复制旧版接入码';}
  }
}
setupNodeAddress();
(async()=>{try{await api('/api/account/me');location.replace('/account/access');}catch{}
  try{const s=await api('/api/status');$('access-label').hidden=s.public_join;}catch(e){feedback('connect-feedback',e);}
})();
$('connect-form').onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;$('connect-feedback').textContent='正在生成…';
  try{const result=await api('/api/profile',{password:$('access-password').value,format:Number($('access-code-format').value)});$('access-password').value='';
    if(result.identity==='account'){location.replace('/account/access');return;}
    $('player-code').value=result.code;$('connect-result').hidden=false;$('connect-feedback').textContent='匿名启动器接入码已生成，不含个人好友身份。';
  }catch(e){feedback('connect-feedback',e);}finally{button.disabled=false;}
};
