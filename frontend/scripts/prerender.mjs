import {build} from 'vite';
import vue from '@vitejs/plugin-vue';
import {mkdtemp,readFile,writeFile,rm,readdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';
const directory=await mkdtemp(resolve('.prerender-'));
const nodeRoot='../web/tunnel/web';
const metadata=JSON.parse(await readFile('../web/tunnel/public-seo.json','utf8'));
const brand=JSON.parse(await readFile('src/brand.json','utf8'));
const esc=value=>value.replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;').replaceAll('>','&gt;');
function headFor(seo){
  const product={'@type':'SoftwareApplication','@id':brand.productId,name:brand.name,alternateName:brand.alternateName,url:brand.url,applicationCategory:'GameApplication',operatingSystem:'Windows, macOS, Linux',sameAs:['https://github.com/vdjango/buggcraft'],downloadUrl:'https://download.bugg.icu/',offers:{'@type':'Offer',price:'0',priceCurrency:'CNY'}};
  const origin=new URL('/',seo.url).href;
  const website={'@type':'WebSite','@id':origin+'#website',url:origin,name:origin===brand.url?brand.name:'BUGG联机节点',alternateName:origin===brand.url?brand.alternateName:['Buggcraft 联机节点'],about:{'@id':brand.productId}};
  const graph={'@context':'https://schema.org','@graph':[product,website,{'@type':'WebPage','@id':seo.url+'#page',url:seo.url,name:seo.title,description:seo.description,isPartOf:{'@id':website['@id']},about:{'@id':product['@id']}}]};
  return `<title>${esc(seo.title)}</title><meta name="description" content="${esc(seo.description)}"><meta name="keywords" content="${esc(seo.keywords)}"><meta name="robots" content="index,follow"><link rel="canonical" href="${seo.url}"><meta property="og:type" content="website"><meta property="og:site_name" content="${esc(website.name)}"><meta property="og:title" content="${esc(seo.title)}"><meta property="og:description" content="${esc(seo.description)}"><meta property="og:url" content="${seo.url}"><meta name="twitter:card" content="summary"><meta name="twitter:title" content="${esc(seo.title)}"><meta name="twitter:description" content="${esc(seo.description)}"><script type="application/ld+json">${JSON.stringify(graph).replaceAll('<','\\u003c')}</script>`;
}
try{
  await build({configFile:false,plugins:[vue({template:{transformAssetUrls:false}})],build:{ssr:'src/prerender.ts',outDir:directory,emptyOutDir:true,rollupOptions:{output:{entryFileNames:'render.mjs'}}},logLevel:'warn'});
  const {renderPages}=await import(pathToFileURL(resolve(directory,'render.mjs')).href);
  const pages=await renderPages();
  for(const [name,seo] of Object.entries(metadata)){
    const path=name==='home'?'../site/index.html':`${nodeRoot}/${name}.html`;
    let html=await readFile(path,'utf8');
    html=html.replace(/<title>[\s\S]*?<\/title>|<meta\b[^>]*(?:name="(?:description|keywords|robots|twitter:[^"]+)"|property="og:[^"]+")[^>]*>|<link\b[^>]*rel="canonical"[^>]*>|<script\b[^>]*type="application\/ld\+json"[^>]*>[\s\S]*?<\/script>/g,'');
    html=html.replace('</head>',headFor(seo)+'</head>');
    html=html.replace(/(<body[^>]*>)[\s\S]*?<\/body>/,'$1'+pages[name]+'</body>');
    await writeFile(path,html);
  }
  for(const file of await readdir(nodeRoot)){
    if(!file.endsWith('.html')||metadata[file.slice(0,-5)])continue;
    const path=`${nodeRoot}/${file}`;
    const html=(await readFile(path,'utf8')).replace(/<meta\b[^>]*name="robots"[^>]*>/g,'').replace('</head>','<meta name="robots" content="noindex,nofollow"></head>');
    await writeFile(path,html);
  }
  const urls=Object.entries(metadata).filter(([key])=>key!=='home').map(([,seo])=>seo.url);
  await writeFile(`${nodeRoot}/sitemap.xml`,'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+urls.map(url=>`<url><loc>${url}</loc></url>`).join('')+'</urlset>\n');
  await writeFile(`${nodeRoot}/robots.txt`,'User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nDisallow: /login\nDisallow: /register\nDisallow: /account\nDisallow: /launcher-login\nDisallow: /feedback\nSitemap: https://node.bugg.icu/sitemap.xml\n');
  console.log('Public SEO generated from Vue components');
}finally{await rm(directory,{recursive:true,force:true});}
