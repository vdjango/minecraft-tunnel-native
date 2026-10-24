'use strict';
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
$('copy-code').onclick=async()=>{try{await navigator.clipboard.writeText($('player-code').value);$('connect-feedback').textContent='已复制。在启动器中添加节点即可联机。';}catch{$('player-code').select();$('connect-feedback').textContent='请复制已选中的启动器接入码。';}};
