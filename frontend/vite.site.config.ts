import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import {updatePageAssets,prunePageAssets} from './build/page-assets';

const web = resolve(import.meta.dirname, '../site');
const pages = JSON.parse(readFileSync(new URL('./pages.site.json', import.meta.url), 'utf8')) as Record<string, string[]>;
export default defineConfig({
  base: '/',
  plugins: [vue({template:{transformAssetUrls:false}}), {
    name: 'existing-page-controllers',
    writeBundle(_options,bundle) {
      prunePageAssets(web,bundle);

      for(const name of Object.keys(pages)) {
<<<<<<< HEAD
=======
        const entry=bundle['vue-page-'+name+'.js'];
        if(!entry || entry.type!=='chunk')continue;
>>>>>>> origin/main
        // The download domain has a separate static root. Salt its URLs after
        // correcting deployment to that root, so an old immutable CDN entry
        // cannot serve the previous bundle under the new page's revision.
        const rootRevision=name==='download'?'-download-root-v2':'';
<<<<<<< HEAD
=======
        const revision=createHash('sha256').update(entry.code).digest('hex').slice(0,12)+rootRevision;
>>>>>>> origin/main
        const html=resolve(web, name==='home'?'index.html':'download/index.html');
        updatePageAssets(html,name,bundle);
        const previous=readFileSync(html,'utf8');
        let updated=previous;
        updated=updated.replace(/<script[^>]*src="\/vue-navigation-shell\.js[^\"]*"[^>]*><\/script>/g,'');
        updated=updated.replace(/<script(?![^>]*data-cfasync)/g,'<script data-cfasync="false"');
<<<<<<< HEAD
=======
        const dependencies=new Set<string>();
        const visit=(chunk:typeof entry)=>{for(const dependency of chunk.imports){if(dependencies.has(dependency))continue;dependencies.add(dependency);const item=bundle[dependency];if(item?.type==='chunk')visit(item);}};
        visit(entry);
        updated=updated.replace(/<link rel="modulepreload"[^>]*>\s*/g,'');
        updated=updated.replace('</head>',[...dependencies].map(file=>`<link rel="modulepreload" href="/${file}">`).join('')+'</head>');
>>>>>>> origin/main
        updated=updated.replace(/(src|href)="\/([^"/?]+\.(?:js|css))(?:\?v=[^"]*)?"/g,(match,attribute,file)=>{const path=resolve(web,file);return !file.startsWith('vue-runtime-')&&existsSync(path)?`${attribute}="/${file}?v=${createHash('sha256').update(readFileSync(path)).digest('hex').slice(0,12)}${rootRevision}"`:match;});
        if(previous!==updated)writeFileSync(html,updated);
      }
      const sites=new Map<string,{directory:string;urls:string[]}>();
      for(const name of Object.keys(pages)){
        const directory=name==='home'?web:resolve(web,name);
        const html=readFileSync(resolve(directory,'index.html'),'utf8');
        const canonical=html.match(/<link\s+rel="canonical"\s+href="([^"]+)"/)?.[1];
        if(!canonical)throw new Error('Missing canonical URL for '+name);
        const origin=new URL(canonical).origin;
        const site=sites.get(origin)||{directory,urls:[]};site.urls.push(canonical);sites.set(origin,site);
      }
      for(const [origin,{directory,urls}] of sites){
        const escaped=urls.map(url=>url.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'));
        const sitemap='<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+escaped.map(url=>'<url><loc>'+url+'</loc></url>').join('')+'</urlset>\n';
        writeFileSync(resolve(directory,'sitemap.xml'),sitemap);
        writeFileSync(resolve(directory,'robots.txt'),'User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nSitemap: '+origin+'/sitemap.xml\n');
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
        import Page from '@site-pages/${name}.vue';
        export function mount(){ mountPage(function(scope) {
          const {fetch,setTimeout,clearTimeout,setInterval,clearInterval}=scope;
          ${source}\n${exports}\n
        }, Page); } if(!window.__buggcraftRouteLoading)mount();`;
    },
  }],
  resolve:{alias:{'@site-pages':resolve(import.meta.dirname,'src/site-pages')}},
  build: {
    target: 'es2022', outDir: web, emptyOutDir: false, sourcemap: false,
    rollupOptions: {
      preserveEntrySignatures: 'exports-only',
      input: Object.fromEntries(Object.keys(pages).map(name => ['page-' + name, 'page:' + name])),
      output: {entryFileNames:'vue-[name]-[hash].js', chunkFileNames:'vue-runtime-[hash].js', assetFileNames:'vue-[name]-[hash][extname]'},
    },
  },
});
