import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { resolve } from 'node:path';
export default defineConfig({
  base: '/',
  plugins: [vue({ template: { transformAssetUrls: false } })],
  build: {
    target: 'es2022', outDir: '../site/help-assets', emptyOutDir: false, manifest: 'manifest.json',
    rollupOptions: {
      input: resolve(import.meta.dirname, 'src/help/client.ts'),
      output: { entryFileNames: 'help-[hash].js', chunkFileNames: 'chunk-[hash].js', assetFileNames: 'help-[hash][extname]' },
    },
  },
});
