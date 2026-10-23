'use strict';
const $ = id => document.getElementById(id);
let csrf='', accountCSRF='';
async function api(path, data, timeoutMs = 20000) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
  const response = await fetch(path, {method:data === undefined ? 'GET' : 'POST', signal:controller.signal,
    headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,'X-Account-CSRF':accountCSRF},
    ...(data === undefined ? {} : {body:JSON.stringify(data)})});
  const text = await response.text();
  let value;
  try { value = JSON.parse(text); } catch { value = {error:text}; }
  if (!response.ok) {const error=new Error((typeof value.error === 'string' && !value.error.trimStart().startsWith('<')) ? value.error : '请求失败，请稍后重试。');error.status=response.status;throw error;}
  return value;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时，请检查网络后重试。');
    if (error instanceof TypeError) throw new Error('无法连接节点，请检查网络后重试。');
    throw error;
  } finally { clearTimeout(timer); }
}
function feedback(id, error) { $(id).textContent = error.message || String(error); }
if($('logout')) $('logout').onclick=async()=>{try{csrf=(await api('/api/admin')).csrf;await api('/api/logout',{});location.replace('/admin');}catch(e){const status=$('diagnostic-feedback')||$('feedback-message');if(status)status.textContent=e.message;}};
