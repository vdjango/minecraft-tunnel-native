import {createApp,h,shallowRef,type App} from 'vue';
import NetworkRows,{type NetworkNode} from './NetworkRows.vue';
export function createViews() {
  let app:App|undefined;const nodes=shallowRef<NetworkNode[]>([]);
  return {network:{update(items:NetworkNode[]){nodes.value=items;if(!app){const host=document.getElementById('network-rows');if(host){app=createApp({render:()=>h(NetworkRows,{nodes:nodes.value})});app.mount(host);}}}},dispose(){app?.unmount();}};
}
