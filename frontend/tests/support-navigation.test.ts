import {createApp, type Component} from 'vue';
import {describe, expect, it} from 'vitest';
import FeedbackPage from '../src/pages/feedback.vue';
import DiagnosticsPage from '../src/pages/diagnostics.vue';
import AccountPage from '../src/pages/account.vue';

function render(page: Component, layoutClass: string) {
  const host = document.createElement('div');
  document.body.append(host);
  const app = createApp(page, {seed: {layoutClass}});
  app.mount(host);
  return {host, close() {app.unmount(); host.remove();}};
}

describe('support page navigation after Vue handoff', () => {
  it('uses the same user menu on account and feedback pages', () => {
    const account = render(AccountPage, '');
    const feedback = render(FeedbackPage, '');
    try {
      const links = (root: HTMLElement) => [...root.querySelectorAll<HTMLAnchorElement>('.account-nav a')]
        .map(link => [link.getAttribute('href'), link.textContent?.trim()]);
      expect(links(feedback.host)).toEqual(links(account.host));
      expect(feedback.host.querySelector('.account-nav a[href="/feedback"]')?.getAttribute('aria-current')).toBe('page');
      expect(links(account.host)).toHaveLength(7);
    } finally {account.close(); feedback.close();}
  });

  for(const [name, page] of [['feedback', FeedbackPage], ['diagnostics', DiagnosticsPage]] as const) {
    it(`${name} keeps the administrator menu off public pages`, () => {
      const view = render(page, '');
      try {
        expect(view.host.querySelector('.console-nav')).toBeNull();
        expect(view.host.querySelector('.management-main')).not.toBeNull();
      } finally {view.close();}
    });

    it(`${name} keeps the menu on administrator pages`, () => {
      const view = render(page, 'management-layout');
      try {
        expect(view.host.querySelector('.console-nav a[href="/admin/nodes"]')).not.toBeNull();
        expect(view.host.querySelector('.management-layout .management-main')).not.toBeNull();
      } finally {view.close();}
    });
  }
});
