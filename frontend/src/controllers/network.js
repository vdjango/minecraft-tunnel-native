'use strict';
let refreshing=false;
async function refreshNetwork(){
  if(refreshing||document.hidden)return;refreshing=true;
  const button=$('refresh-network');button.disabled=true;button.textContent='正在刷新…';
  $('network-rows').setAttribute('aria-busy','true');$('network-feedback').textContent='正在读取节点状态…';
  try{const state=await api('/api/status'),nodes=state.nodes;
    $('network-total').textContent=nodes.length;$('network-online').textContent=nodes.filter(n=>['online','indirect'].includes(n.status)).length;
    $('network-accepting').textContent=nodes.filter(n=>['online','indirect'].includes(n.status)&&n.accepting&&n.remaining_visitors!==0).length;
    pageViews.network.update(nodes);
    $('network-feedback').textContent='';
  }catch(e){feedback('network-feedback',e);}finally{refreshing=false;button.disabled=false;button.textContent='刷新节点';$('network-rows').setAttribute('aria-busy','false');}
}
$('refresh-network').onclick=refreshNetwork;refreshNetwork();setInterval(refreshNetwork,10000);
