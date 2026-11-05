import { renderToString } from 'vue/server-renderer';
import { createHead, renderSSRHead } from '@unhead/vue/server';
import { createHelpApp } from './app';
import { guides, guidePath } from './content';

export const paths = ['/docs', ...guides.map(guide => guidePath(guide.slug))];
export async function render(path: string) {
  const { app, router } = createHelpApp(true);
  const head = createHead();
  app.use(head);
  await router.push(path);
  await router.isReady();
  const body = await renderToString(app);
  return { body, ...(await renderSSRHead(head)) };
}
