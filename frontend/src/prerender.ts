import {createSSRApp,h} from 'vue';
import {renderToString} from 'vue/server-renderer';
import Home from './site-pages/home.vue';
import Download from './site-pages/download.vue';
export async function renderPages(){
  const result:Record<string,string>={};
  for(const [name,Page] of Object.entries({home:Home,download:Download}))
    result[name]=await renderToString(createSSRApp({render:()=>h(Page,{seed:{}})}));
  return result;
}
