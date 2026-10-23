'use strict';
let sessions=[], selected=null, events=[], sampleIndex=0, playing=null, loading=false, following=true;
const stamp=at=>new Date(at*1000).toLocaleString();
const names={connected:'连接并通过认证',disconnected:'连接关闭',service_shutdown:'节点服务停止',sample:'流量采样',stream_open:'打开转发流',stream_closed:'转发流关闭',stream_eof:'一端结束发送',stream_transport_lost:'转发传输关闭',backpressure_start:'下游背压开始',backpressure_end:'下游背压结束',host_connect_failed:'无法建立房主转发',client_report:'启动器上报',peer_offer:'P2P 协商请求',peer_answer:'P2P 协商应答',journal_rotated:'日志滚动保留'};
function stopReplay(){if(playing)clearInterval(playing);playing=null;$('replay-toggle').textContent='播放时间线';}
function renderSelection(){
  const filter=$('player-filter').value.trim().toLowerCase();
  const options=sessions.filter(s=>(s.username+' '+s.player+' '+s.session).toLowerCase().includes(filter));
  $('session-select').replaceChildren(...options.map(s=>{const o=document.createElement('option');o.value=s.session;o.textContent=(s.online?'在线 · ':'历史 · ')+s.username+' · '+(s.role==='host'?'房主':'访客')+' · '+stamp(s.started);return o;}));
  if(selected&&options.some(s=>s.session===selected.session))$('session-select').value=selected.session;
  else if(options.length){selected=options[0];$('session-select').value=selected.session;}
  else{selected=null;$('session-detail').hidden=true;}
}
async function loadSessions(){
  if(loading||document.hidden)return;loading=true;
  try{const result=await api('/api/admin/diagnostics');sessions=result.sessions;$('diagnostic-login').hidden=true;$('diagnostic-panel').hidden=false;
    renderSelection();$('journal-health').textContent=sessions.length+' 条连接记录 · 队列丢弃 '+result.dropped_events+' 条 · 写盘失败 '+result.write_errors+' 次';
    if(!sessions.length)$('journal-health').textContent+=' · 等待玩家连接';
    if(selected&&!playing)await loadEvents(false);
  }catch(e){$('diagnostic-login').hidden=false;feedback('diagnostic-feedback',e);}finally{loading=false;}
}
async function loadEvents(reset=true){
  if(reset){stopReplay();following=true;selected=sessions.find(s=>s.session===$('session-select').value);}
  if(!selected)return;
  const result=await api('/api/admin/diagnostics/'+encodeURIComponent(selected.player)+'/'+encodeURIComponent(selected.session));events=result.events;
  $('session-detail').hidden=false;$('session-title').textContent=selected.username+' · '+(selected.authenticated?'账号玩家':'匿名连接');
  $('session-meta').textContent=(selected.role==='host'?'房主':'访客')+' · '+stamp(selected.started)+' · '+(selected.online?'当前在线':'已断开')+(result.truncated?' · 最近记录':'');
  const samples=events.filter(e=>e.event==='sample'),max=key=>Math.max(0,...samples.map(e=>Number(e[key])||0));
  $('peak-upload').textContent=(max('upload_bps')/1e6).toFixed(3);$('peak-download').textContent=(max('download_bps')/1e6).toFixed(3);$('peak-lag').textContent=max('loop_lag_ms').toFixed(1);
  const reports=events.filter(e=>e.event==='client_report');const rtts=reports.map(e=>e.game_rtt_ms).filter(Number.isFinite);$('peak-rtt').textContent=rtts.length?Math.max(...rtts).toFixed(1):'未上报';
  const blocks=events.filter(e=>e.event==='backpressure_end');
  const closed=[...events].reverse().find(e=>e.event==='disconnected'),reason={heartbeat_timeout:'心跳超时',transport_error:'传输异常',service_shutdown:'节点服务重启',transport_closed:'连接已关闭'};
  const blocked=blocks.reduce((n,e)=>n+(Number(e.duration_ms)||0),0),longest=Math.max(0,...blocks.map(e=>Number(e.duration_ms)||0));
  const paths={p2p:'P2P 直连',relay:'节点中继',mixed:'混合连接',auto:'自动选择',unavailable:'不可用'};
  $('findings').textContent=(closed?'断开原因：'+(reason[closed.reason]||'原因未知')+'。':selected.online?'连接保持在线。':'缺少断开记录。')+' 背压 '+blocks.length+' 次，累计 '+(blocked/1000).toFixed(2)+' 秒，最长 '+(longest/1000).toFixed(2)+' 秒。节点调度峰值 '+max('loop_lag_ms').toFixed(1)+' ms。'+(reports.length?'最近路径：'+(paths[reports.at(-1).path]||'未上报')+'。':'尚无客户端路径上报。');
  const signals=events.map((e,index)=>({e,index})).filter(({e})=>e.event==='backpressure_end'&&e.duration_ms>=100||e.event==='sample'&&e.loop_lag_ms>=100||e.event==='disconnected'&&['heartbeat_timeout','transport_error'].includes(e.reason));
  $('diagnostic-alerts').replaceChildren(...signals.slice(-12).map(({e,index})=>{const button=document.createElement('button');button.className='secondary';button.textContent=stamp(e.at)+' · '+(e.event==='sample'?'节点调度延迟':e.event==='disconnected'?'异常断开':'下游拥塞');button.onclick=()=>{stopReplay();sampleIndex=index;showTime();$('replay-position').scrollIntoView({block:'center'});};return button;}));
  if(!signals.length)$('diagnostic-alerts').textContent='当前记录未发现超过 100 ms 的背压或节点调度峰值。';
  $('replay-position').max=Math.max(0,events.length-1);sampleIndex=following?Math.max(0,events.length-1):Math.min(sampleIndex,events.length-1);showTime();drawChart(samples);
}
function showTime(){
  $('replay-position').value=sampleIndex;const e=events[sampleIndex];$('replay-time').textContent=e?stamp(e.at):'暂无采样';
  $('sample-detail').textContent=e?JSON.stringify(e,null,2):'连接刚建立，请稍后刷新。';
  const rows=events.slice(0,sampleIndex+1).filter(e=>!['sample','client_report'].includes(e.event)).slice(-150).reverse();
  $('event-list').replaceChildren(...rows.map(e=>{const row=document.createElement('div');row.className='history-entry';const title=document.createElement('strong'),detail=document.createElement('p');title.textContent=stamp(e.at)+' · '+(names[e.event]||e.event);detail.textContent=JSON.stringify(Object.fromEntries(Object.entries(e).filter(([k])=>!['at','event','player','session'].includes(k))));row.append(title,detail);return row;}));
}
function drawChart(samples){
  const canvas=$('traffic-chart'),width=Math.max(280,canvas.clientWidth),height=180,dpr=window.devicePixelRatio||1;canvas.width=width*dpr;canvas.height=height*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);const peak=Math.max(1,...samples.flatMap(s=>[s.upload_bps,s.download_bps]));
  ctx.strokeStyle='#e4dfee';for(let i=0;i<4;i++){ctx.beginPath();ctx.moveTo(0,12+i*50);ctx.lineTo(width,12+i*50);ctx.stroke();}
  for(const [key,color] of [['upload_bps','#1769cf'],['download_bps','#188254']]){ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();samples.forEach((s,i)=>{const x=width*i/Math.max(1,samples.length-1),y=165-s[key]/peak*145;if(i)ctx.lineTo(x,y);else ctx.moveTo(x,y);});ctx.stroke();}
}
$('diagnostic-login').onsubmit=async event=>{event.preventDefault();const b=event.submitter;b.disabled=true;try{await api('/api/login',{username:$('username').value,password:$('password').value});$('password').value='';$('diagnostic-feedback').textContent='';await loadSessions();}catch(e){feedback('diagnostic-feedback',e);}finally{b.disabled=false;}};
$('session-select').onchange=()=>loadEvents().catch(e=>feedback('diagnostic-feedback',e));$('player-filter').oninput=()=>{renderSelection();if(selected)loadEvents(false).catch(e=>feedback('diagnostic-feedback',e));};
$('replay-position').oninput=()=>{stopReplay();following=false;sampleIndex=Number($('replay-position').value);showTime();};
$('replay-toggle').onclick=()=>{if(playing){stopReplay();return;}if(sampleIndex>=events.length-1)sampleIndex=0;$('replay-toggle').textContent='暂停';playing=setInterval(()=>{if(sampleIndex>=events.length-1){stopReplay();return;}sampleIndex++;showTime();},250);};
$('export-diagnostics').onclick=()=>{const blob=new Blob([JSON.stringify({format:'buggcraft-session-metadata-v1',session:selected,events},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='buggcraft-session-'+selected.session+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
loadSessions();setInterval(loadSessions,5000);window.addEventListener('resize',()=>drawChart(events.filter(e=>e.event==='sample')));
