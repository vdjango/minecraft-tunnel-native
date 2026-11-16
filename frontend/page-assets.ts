import {readFileSync, readdirSync, unlinkSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import type {OutputBundle, OutputChunk} from 'rollup';

// Hand-authored shells need CSS links and content-hash entry names, too.
export function updatePageAssets(path:string, name:string, bundle:OutputBundle) {
  const entry=Object.values(bundle).find(item=>item.type==='chunk'&&item.name==='page-'+name) as OutputChunk|undefined;
  if(!entry)throw new Error('Missing page entry: '+name);
  const dependencies=new Set<string>();
  const styles=new Set<string>();
  const visit=(chunk:OutputChunk)=>{
    const metadata=(chunk as OutputChunk & {viteMetadata?:{importedCss:Set<string>}}).viteMetadata;
    for(const css of metadata?.importedCss||[])styles.add(css);
    for(const file of chunk.imports){
      if(dependencies.has(file))continue;
      dependencies.add(file);
      const child=bundle[file];
      if(child?.type==='chunk')visit(child);
    }
  };
  visit(entry);
  const previous=readFileSync(path,'utf8');
  const pattern=new RegExp('src="/vue-page-'+name+'(?:-[A-Za-z0-9_-]+)?\\.js(?:\\?v=[^"]*)?"');
  if(!pattern.test(previous))throw new Error('Missing module script in '+path);
  const updated=previous.replace(pattern,'src="/'+entry.fileName+'"')
    .replace(/<script[^>]*\bdata-usage-script[^>]*><\/script>/g,'')
    .replace(/<link rel="modulepreload"[^>]*>\s*|<link data-page-style[^>]*>\s*/g,'')
    .replace('</head>',[...dependencies].map(file=>`<link rel="modulepreload" href="/${file}">`).join('')+
      [...styles].map(file=>`<link data-page-style rel="stylesheet" href="/${file}">`).join('')+
      '<script data-usage-script defer src="https://node.bugg.icu/usage.js?v=20261116"></script></head>');
  if(previous!==updated)writeFileSync(path,updated,'utf8');
}

export function prunePageAssets(directory:string,bundle:OutputBundle) {
  const keep=new Set(Object.keys(bundle));
  for(const name of readdirSync(directory)){
    if(/^vue-[A-Za-z0-9_-]+\.(js|css)$/.test(name)&&!keep.has(name))unlinkSync(resolve(directory,name));
  }
}
