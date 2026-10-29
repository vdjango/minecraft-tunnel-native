'use strict';
let refreshing=false;
function timeLabel(seconds){return seconds>=86400?(seconds/86400).toFixed(1)+' 天':seconds>=3600?(seconds/3600).toFixed(1)+' 时':Math.floor(seconds/60)+' 分';}
async function refresh(){if(refreshing)return;refreshing=true;try{
  const s=await api('/api/status');document.title=s.name+' · Buggcraft';
  for(const id of ['node-name','node-name-heading'])$(id).textContent=s.name;
  $('health').textContent=s.status==='online'?'节点在线':'维护中';
  for(const key of ['rooms','players'])$(key).textContent=s[key];
  $('player-paths').textContent='全网房主与访客；路径为本节点访客：P2P '+s.p2p_players+' · 中转 '+s.relay_players+' · 待确认 '+s.unconfirmed_players;
  $('game-players').textContent=s.game_counts_reported === 0 ? '—' : (s.game_counts_unknown ? s.game_players+'+' : s.game_players);
  $('game-coverage').textContent=s.game_counts_unknown?'等待 '+s.game_counts_unknown+' 个房间更新':'游戏内在线人数';
  $('uptime').textContent=timeLabel(s.uptime_seconds);$('room-limit').textContent='上限 '+s.max_rooms+' 个房间';
  $('fingerprint').textContent=s.fingerprint;$('node-id').textContent=s.id;
  document.body.classList.remove('offline');
}catch{$('health').textContent='暂时无法连接';document.body.classList.add('offline');}finally{refreshing=false;}}
refresh();setInterval(refresh,5000);
