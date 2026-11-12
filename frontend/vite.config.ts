import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import {updatePageAssets,prunePageAssets} from './build/page-assets';

const web = resolve(import.meta.dirname, '../web/tunnel/web');
const pages = JSON.parse(readFileSync(new URL('./pages.json', import.meta.url), 'utf8')) as Record<string, string[]>;
export default defineConfig({
  base: '/',
  plugins: [vue(), {
    name: 'existing-page-controllers',
    writeBundle(_options,bundle) {
      prunePageAssets(web,bundle);

      for(const name of Object.keys(pages)) {
        const html=resolve(web, name+'.html');
        updatePageAssets(html,name,bundle);
        const previous=readFileSync(html,'utf8');
        let updated=previous;
        updated=updated.replace(/<script(?![^>]*data-cfasync)/g,'<script data-cfasync="false"');
        updated=updated.replace(/<link(?![^>]*data-shell-styles)(?=[^>]*href="\/theme\.css)/g,'<link data-shell-styles');
        updated=updated.replace(/(src|href)="\/([^"/?]+\.(?:js|css))(?:\?v=[^"]*)?"/g,(match,attribute,file)=>{const path=resolve(web,file);return !file.startsWith('vue-runtime-')&&existsSync(path)?`${attribute}="/${file}?v=${createHash('sha256').update(readFileSync(path)).digest('hex').slice(0,12)}"`:match;});
        if(previous!==updated)writeFileSync(html,updated);
      }
    },
    resolveId(id) { if (id.startsWith('page:')) return '\0' + id; },
    load(id) {
      if (!id.startsWith('\0page:')) return;
      const name = id.slice(6);
      let source = pages[name].map(file => {
        const override=resolve(import.meta.dirname,'src/controllers',file);
        const path=existsSync(override)?override:resolve(web,file);
        this.addWatchFile(path);
        return readFileSync(path,'utf8');
      }).join('\n;\n');
      source = source.replace(/(?:window|document)\.addEventListener\(['"]DOMContentLoaded['"],/g, 'scope.ready(');
      source = source.replace(/(window|document)\.addEventListener\(/g, 'scope.listen($1,');
      const exports = ['accountView', 'loadSessions', 'refresh'].map(key => `if(typeof ${key}==='function') scope.expose('${key}',${key});`).join('\n');
      return `import {mountPage} from '${resolve(import.meta.dirname,'src/runtime.ts').replaceAll('\\','/')}';
        import Page from '@pages/${name}.vue';
        export function mount(){ mountPage(function(scope) {
          const {fetch,setTimeout,clearTimeout,setInterval,clearInterval}=scope;
          ${source}\n${exports}\n
        }, Page); } if(!window.__buggcraftRouteLoading)mount();`;
    },
  }],
  resolve:{alias:{'@pages':resolve(import.meta.dirname,'src/pages')}},
  build: {
    target: 'es2022', outDir: web, emptyOutDir: false, sourcemap: false,
    rollupOptions: {
      preserveEntrySignatures: 'exports-only',
      input: Object.fromEntries(Object.keys(pages).map(name => ['page-' + name, 'page:' + name])),
      output: {entryFileNames:'vue-[name]-[hash].js', chunkFileNames:'vue-runtime-[hash].js', assetFileNames:'vue-[name]-[hash][extname]'},
    },
  },
});
