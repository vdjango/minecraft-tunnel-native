'use strict';
let refreshing=false;
async function refreshNetwork(){
  if(refreshing||document.hidden)return;refreshing=true;
  try{const state=await api('/api/status'),nodes=state.nodes;
    $('network-total').textContent=nodes.length;$('network-online').textContent=nodes.filter(n=>['online','indirect'].includes(n.status)).length;
    $('network-accepting').textContent=nodes.filter(n=>['online','indirect'].includes(n.status)&&n.accepting&&n.remaining_visitors!==0).length;
    const statuses={online:'在线',indirect:'在线',suspect:'连接不稳定',offline:'离线',blocked:'已隔离'};
    $('network-rows').replaceChildren(...nodes.map(n=>{const row=document.createElement('tr');for(const value of [n.name+(n.local?' · 当前节点':''),n.region||'未设置',statuses[n.status]||'未知',n.local?'本节点':Number.isFinite(n.latency_ms)?n.latency_ms.toFixed(1)+' ms':'暂无测量',Number.isFinite(n.utilization)?Math.round(n.utilization*100)+'%':'未配置',n.accepting?(n.remaining_visitors===null?'未配置':n.remaining_visitors+' 人'):'暂停接纳']){const td=document.createElement('td');td.textContent=value;row.append(td);}return row;}));
    $('network-feedback').textContent='';
  }catch(e){feedback('network-feedback',e);}finally{refreshing=false;}
}
$('refresh-network').onclick=refreshNetwork;refreshNetwork();setInterval(refreshNetwork,10000);
