'use strict';
let bandwidthUnit='MB/s';
function renderAdminBandwidth(sample){
  const panel=document.getElementById('admin-bandwidth');if(!panel)return;
  const ready=sample?.status==='ready',factor=bandwidthUnit==='Mbps'?8e-6:1e-6;
  for(const [id,key] of [['bandwidth-rx','rx_bytes_per_second'],['bandwidth-tx','tx_bytes_per_second']])document.getElementById(id).textContent=ready?(sample[key]*factor).toFixed(3):'—';
  for(const label of panel.querySelectorAll('.bandwidth-unit'))label.textContent=bandwidthUnit;
  for(const button of panel.querySelectorAll('[data-bandwidth-unit]'))button.setAttribute('aria-pressed',String(button.dataset.bandwidthUnit===bandwidthUnit));
  document.getElementById('bandwidth-status').textContent=ready?'服务器网卡收发速率':({warming_up:'正在采样…',stale:'采样已过期，等待更新',unavailable:'当前系统无法读取网卡计数器',error:'采样失败，请检查节点日志'}[sample?.status]||'暂无有效采样');
  for(const button of panel.querySelectorAll('[data-bandwidth-unit]'))button.onclick=()=>{bandwidthUnit=button.dataset.bandwidthUnit;renderAdminBandwidth(sample);};
}
