import {createSSRApp,h} from 'vue';
import {renderToString} from 'vue/server-renderer';
import Home from './site-pages/home.vue';
import Index from './pages/index.vue';
import Network from './pages/network.vue';
import Connect from './pages/connect.vue';
import Developers from './pages/developers.vue';
export async function renderPages(){
  const result:Record<string,string>={};
  for(const [name,Page] of Object.entries({home:Home,index:Index,network:Network,connect:Connect,developers:Developers}))
    result[name]=await renderToString(createSSRApp({render:()=>h(Page,{seed:{}})}));
  return result;
}
