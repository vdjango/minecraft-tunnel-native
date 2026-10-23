'use strict';
const statusNames={open:'待受理',accepted:'已受理',investigating:'处理中',resolved:'已完成'};
const adminMode=location.pathname.startsWith('/admin/');
let viewer=null,issues=[],currentIssue=null,page=0,total=0,refreshing=false,mine=false;
async function feedbackAPI(path,data){
  const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,'X-Account-CSRF':accountCSRF,'X-Feedback-Role':adminMode?'admin':'user'},...(data===undefined?{}:{body:JSON.stringify(data)})});
  const content=await response.text();let result;try{result=JSON.parse(content);}catch{result={error:content};}
  if(!response.ok)throw new Error(result.error&&!result.error.trimStart().startsWith('<')?result.error:'请求失败，请稍后重试。');return result;
}
function panel(name){for(const id of ['issue-list-panel','issue-create','issue-detail'])$(id).hidden=id!==name;$('feedback-message').textContent='';$('show-issues').setAttribute('aria-selected',String(!mine&&name==='issue-list-panel'));$('my-issues').setAttribute('aria-selected',String(mine&&name==='issue-list-panel'));}
function issueUrl(id){return '/feedback/'+id;}
function renderIssues(){
  const search=$('issue-search').value.trim().toLowerCase(),filter=$('issue-filter').value;
  const rows=issues.filter(i=>(!filter||i.status===filter)&&(!search||(i.title+' '+i.reporter+' '+i.game_version).toLowerCase().includes(search)));
  $('issue-list').replaceChildren(...rows.map(i=>{const row=document.createElement('a');row.className='issue-row';row.href=issueUrl(i.id);const label=document.createElement('span'),title=document.createElement('strong'),meta=document.createElement('small');label.className='identity-badge';label.textContent=statusNames[i.status];title.textContent=i.title;meta.textContent=i.reporter+' · '+i.game_version+' · '+new Date(i.updated*1000).toLocaleString();row.append(label,title,meta);row.onclick=event=>{event.preventDefault();openIssue(i.id).catch(e=>feedback('feedback-message',e));};return row;}));
  $('issue-empty').hidden=rows.length>0;$('issue-list').hidden=rows.length===0;
  $('issues-prev').disabled=page===0;$('issues-next').disabled=(page+1)*30>=total;$('issue-page').textContent='第 '+(page+1)+' 页 · 共 '+total+' 条';
}
async function loadList(){const result=await feedbackAPI('/api/feedback?page='+page+'&mine='+(mine?'1':'0')+'&q='+encodeURIComponent($('issue-search').value.trim())+'&status='+encodeURIComponent($('issue-filter').value));viewer=result.viewer;issues=result.issues;total=result.total;
  $('feedback-login').hidden=true;$('feedback-content').hidden=false;$('new-issue').hidden=false;$('feedback-admin-link').hidden=!viewer.admin;$('my-issues').hidden=viewer.admin;
  $('new-issue').textContent=viewer.admin?'以用户身份提交问题':'＋ 提交问题';
  for(const nav of document.querySelectorAll('.user-feedback-nav'))nav.hidden=adminMode;
  $('sync-state').textContent=(result.last_sync?'最近同步 '+new Date(result.last_sync*1000).toLocaleTimeString():'本节点记录 · 等待联邦同步')+(result.cleanup_pending?' · '+result.cleanup_pending+' 个问题的附件等待删除重试':'');renderIssues();}
async function openIssue(id){
  const result=await feedbackAPI('/api/feedback/'+id);currentIssue=result.issue;viewer=result.viewer;const i=currentIssue;
  panel('issue-detail');if(!adminMode)history.replaceState(null,'',issueUrl(id));document.title=i.title+' · 问题反馈 · Buggcraft';
  $('detail-title').textContent=i.title;$('detail-status').textContent=statusNames[i.status];$('detail-meta').textContent=i.reporter+' · 提交于 '+new Date(i.created*1000).toLocaleString();$('detail-description').textContent=i.description;
  $('detail-versions').replaceChildren(...[['游戏版本',i.game_version],['加载器',i.loader],['启动器',i.launcher_version],['系统',i.system]].flatMap(([k,v])=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=k;dd.textContent=v||'未填写';return[dt,dd];}));
  $('detail-attachments').replaceChildren(...i.attachments.map(a=>{const row=document.createElement('div');row.className='friend-row';const name=document.createElement('span');name.textContent=a.name+' · '+(a.size/1e6).toFixed(2)+' MB';row.append(name);if(a.deleted){const label=document.createElement('small');label.textContent='已删除';row.append(label);}else if(viewer.admin){const link=document.createElement('a');link.href='/api/feedback/'+id+'/attachments/'+a.id;link.className='secondary';link.textContent='下载日志';row.append(link);}else{const label=document.createElement('small');label.textContent='已上传，管理员可下载';row.append(label);}return row;}));
  if(!i.attachments.length)$('detail-attachments').textContent=i.status==='resolved'?'问题已完成，日志附件已删除。':(viewer.admin||viewer.account===i.account?'暂无附件。':'日志附件仅提交者和管理员可见。');
  $('attachment-form').hidden=viewer.admin||viewer.account!==i.account||i.status==='resolved'||i.attachments.length>=5;
  $('status-form').hidden=!viewer.admin||i.status==='resolved';
  $('issue-history').replaceChildren(...i.history.filter(e=>e.kind!=='created'&&e.kind!=='attachment').map(e=>{const row=document.createElement('article');row.className='history-entry';const title=document.createElement('strong'),body=document.createElement('p');title.textContent=e.username+' · '+(e.kind==='status'?statusNames[e.data.status]:'回复')+' · '+new Date(e.at*1000).toLocaleString();body.textContent=e.data.message;body.className='issue-prose';row.append(title,body);return row;}));
  if(!$('issue-history').childElementCount)$('issue-history').textContent='等待受理。';
}
$('show-issues').onclick=()=>{mine=false;page=0;currentIssue=null;panel('issue-list-panel');if(!adminMode)history.replaceState(null,'','/feedback');loadList().catch(e=>feedback('feedback-message',e));};
$('my-issues').onclick=()=>{mine=true;page=0;currentIssue=null;panel('issue-list-panel');loadList().catch(e=>feedback('feedback-message',e));};
$('empty-new-issue').onclick=()=>$('new-issue').click();
$('new-issue').onclick=()=>{if(viewer?.admin){location.assign('/login?next='+encodeURIComponent('/feedback?new=1'));return;}currentIssue=null;panel('issue-create');if(!adminMode)history.replaceState(null,'','/feedback?new=1');$('issue-title').focus();};let searchTimer=null;function searchIssues(){clearTimeout(searchTimer);searchTimer=setTimeout(()=>{page=0;loadList().catch(e=>feedback('feedback-message',e));},250);}$('issue-search').oninput=searchIssues;$('issue-filter').onchange=searchIssues;
for(const [id,delta] of [['issues-prev',-1],['issues-next',1]])$(id).onclick=()=>{page+=delta;loadList().catch(e=>feedback('feedback-message',e));};
function action(form,operation){$(form).onsubmit=async event=>{event.preventDefault();const b=event.submitter;b.disabled=true;try{await operation();}catch(e){feedback('feedback-message',e);}finally{b.disabled=false;}};}
function validateLog(file){
  if(!file)return;
  if(!/\.(log|txt|json|zip)$/i.test(file.name))throw new Error('请选择 ZIP 日志压缩包或 LOG、TXT、JSON 文件。');
  if(!file.size||file.size>20*1024*1024)throw new Error('文件不能为空且不能超过 20 MiB。');
}
async function uploadLog(id,file,progress){
  validateLog(file);const data=new FormData();data.append('file',file);progress.hidden=false;progress.value=0;
  try{await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/api/feedback/'+id+'/attachments');xhr.timeout=120000;xhr.setRequestHeader('X-Account-CSRF',accountCSRF);
    xhr.upload.onprogress=e=>{if(e.lengthComputable)progress.value=e.loaded/e.total*100;};
    xhr.onload=()=>xhr.status>=200&&xhr.status<300?resolve():reject(new Error('上传失败（状态码 '+xhr.status+'），问题已保存，可稍后补传。'));
    xhr.onerror=()=>reject(new Error('上传中断，请检查网络后重试。'));xhr.ontimeout=()=>reject(new Error('上传中断，请检查网络后重试。'));xhr.send(data);
  });}finally{progress.hidden=true;}
}
action('issue-create',async()=>{
  const file=$('issue-file').files[0];validateLog(file);$('create-feedback').textContent='正在创建问题…';
  const result=await feedbackAPI('/api/feedback',{title:$('issue-title').value,description:$('issue-description').value,game_version:$('issue-game').value,loader:$('issue-loader').value,launcher_version:$('issue-launcher').value,system:$('issue-system').value});
  let uploadError=null;
  try{if(file){$('create-feedback').textContent='正在上传日志，请稍候…';await uploadLog(result.id,file,$('create-progress'));}}catch(e){uploadError=e;}
  if(uploadError&&file){const selected=new DataTransfer();selected.items.add(file);$('attachment-file').files=selected.files;}
  $('issue-create').reset();$('create-feedback').textContent='';await openIssue(result.id);
  if(uploadError)feedback('feedback-message',uploadError);else $('feedback-message').textContent=file?'问题和日志已提交。':'问题已提交，可以继续补充日志。';
});
action('comment-form',async()=>{await feedbackAPI('/api/feedback/'+currentIssue.id+'/comments',{message:$('comment-text').value});$('comment-text').value='';await openIssue(currentIssue.id);});
action('status-form',async()=>{await feedbackAPI('/api/feedback/'+currentIssue.id+'/status',{status:$('status-value').value,message:$('status-message').value});$('status-message').value='';await openIssue(currentIssue.id);});
action('attachment-form',async()=>{const file=$('attachment-file').files[0];if(!file)return;
  $('upload-feedback').textContent='正在上传日志，请稍候…';
  await uploadLog(currentIssue.id,file,$('upload-progress'));$('attachment-form').reset();
  await openIssue(currentIssue.id);$('upload-feedback').textContent='上传完成，日志已交给管理员。';
});
(async()=>{try{if(adminMode){const me=await api('/api/admin');csrf=me.csrf;$('feedback-title').textContent='反馈管理';}else{const me=await api('/api/account/me');accountCSRF=me.csrf;}await loadList();const id=location.pathname.split('/').pop();if(/^[a-f0-9]{32}$/.test(id))await openIssue(id);else if(!viewer.admin&&new URLSearchParams(location.search).get('new')==='1')$('new-issue').click();}catch(e){$('feedback-login').hidden=false;feedback('feedback-message',e);}})();
setInterval(async()=>{if(refreshing||document.hidden||!viewer||!$('issue-create').hidden||document.activeElement?.tagName==='TEXTAREA')return;refreshing=true;try{if(currentIssue)await openIssue(currentIssue.id);else await loadList();}catch(e){feedback('feedback-message',e);}finally{refreshing=false;}},15000);
