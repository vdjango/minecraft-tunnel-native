import { createSSRApp } from 'vue';
import { createRouter, createMemoryHistory, createWebHistory } from 'vue-router';
import App from './App.vue';
import Home from './Home.vue';
import Article from './Article.vue';
import { findGuide } from './content';

export function createHelpApp(server = false) {
  const router = createRouter({
    history: server ? createMemoryHistory() : createWebHistory(),
    routes: [
      { path: '/docs', component: Home },
      { path: '/docs/help/:slug', component: Article, beforeEnter: to => findGuide(String(to.params.slug)) ? true : '/docs/not-found' },
      { path: '/:pathMatch(.*)*', component: Home, meta: { notFound: true } },
    ],
    scrollBehavior(to, from, saved) {
      if (saved) return saved;
      if (to.hash) return { el: to.hash, top: 100, behavior: 'smooth' };
      if (to.path !== from.path) return { top: 0 };
    },
  });
  return { app: createSSRApp(App).use(router), router };
}
