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

it('hides retired or unreachable nodes and shows them again after recovery',async()=>{
  const host=document.createElement('tbody');
  const {ref}=await import('vue');
  const nodes=ref([{name:'在用节点',status:'online',accepting:true,remaining_visitors:3},
    {name:'已配置节点',status:'offline',accepting:true,remaining_visitors:88},
    {name:'失联节点',status:'suspect',accepting:true,remaining_visitors:2}]);
  const app=createApp({render:()=>h(NetworkRows,{nodes:nodes.value})});app.mount(host);
  try{
    await nextTick();expect(host.querySelectorAll('tr')).toHaveLength(1);
    expect(host.textContent).not.toContain('已配置节点');
    nodes.value[1].status='indirect';
    await nextTick();expect(host.querySelectorAll('tr')).toHaveLength(2);
  }finally{app.unmount();}
});
