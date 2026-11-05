import { createHead } from '@unhead/vue/client';
import { createHelpApp } from './app';
import './style.css';

const { app, router } = createHelpApp();
app.use(createHead());
router.isReady().then(() => app.mount('#app'));
