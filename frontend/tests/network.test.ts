import {expect,it} from 'vitest';
import {createApp,h,nextTick} from 'vue';
import NetworkRows from '../src/NetworkRows.vue';
it('retains zero latency, zero load and zero remaining visitor capacity',async()=>{
  const host=document.createElement('tbody');
  const app=createApp({render:()=>h(NetworkRows,{nodes:[{name:'Example',status:'online',accepting:true,remaining_visitors:0,latency_ms:0,utilization:0}]})});app.mount(host);
  try{
    await nextTick();
    const cells=[...host.querySelectorAll('td')].map(td=>td.textContent);
    expect(cells).toEqual(['Example','未设置','在线','0.0 ms','0%','0 人']);
  }finally{app.unmount();}
});
