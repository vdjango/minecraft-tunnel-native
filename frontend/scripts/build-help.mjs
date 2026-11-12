import { build } from 'vite';
import vue from '@vitejs/plugin-vue';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
const root = resolve(import.meta.dirname, '..');
const site = resolve(root, '../site');
await build({ root, configFile: resolve(root, 'vite.help.config.ts') });
const ssr = resolve(root, 'node_modules/.cache/bugg-help/server.mjs');
await build({ root, configFile: false, plugins: [vue({ template: { transformAssetUrls: false } })],
  ssr: { noExternal: true }, build: { ssr: resolve(root, 'src/help/server.ts'), outDir: resolve(ssr, '..'),
    emptyOutDir: true, rollupOptions: { output: { entryFileNames: 'server.mjs' } } } });
const { paths, render } = await import(pathToFileURL(ssr).href + '?v=' + Date.now());
const manifest = JSON.parse(await readFile(resolve(site, 'help-assets/manifest.json'), 'utf8'));
const entry = manifest['src/help/client.ts'];
for (const path of paths) {
  const result = await render(path);
  const assets = (entry.css || []).map(css => `<link rel="stylesheet" href="/help-assets/${css}">`).join('');
  const html = `<!DOCTYPE html><html ${result.htmlAttrs}><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#faf9fc"><link rel="icon" href="/favicon.ico">${result.headTags}${assets}<script data-cfasync="false" type="module" src="/help-assets/${entry.file}"></script></head><body ${result.bodyAttrs}>${result.bodyTagsOpen || ''}<div id="app">${result.body}</div>${result.bodyTags || ''}</body></html>`;
  const directory = resolve(site, '.' + path);
  await mkdir(directory, { recursive: true });
  await writeFile(resolve(directory, 'index.html'), html);
}
const sitemapPath = resolve(site, 'sitemap.xml');
let sitemap = await readFile(sitemapPath, 'utf8');
sitemap = sitemap.replace(/<url>\s*<loc>https:\/\/bugg\.icu\/docs(?:\/[^<]*)?<\/loc>[\s\S]*?<\/url>/g, '');
<<<<<<< HEAD
sitemap = sitemap.replace('</urlset>', paths.map(path => `<url><loc>https://bugg.icu${path}</loc><lastmod>2026-11-11</lastmod></url>`).join('') + '</urlset>');
=======
sitemap = sitemap.replace('</urlset>', paths.map(path => `<url><loc>https://bugg.icu${path}</loc><lastmod>2026-11-06</lastmod></url>`).join('') + '</urlset>');
>>>>>>> origin/main
await writeFile(sitemapPath, sitemap);
console.log(`Pre-rendered ${paths.length} tutorial pages with canonical metadata and sitemap entries.`);
