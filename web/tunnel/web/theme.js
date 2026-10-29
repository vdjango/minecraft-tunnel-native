/* Apply the saved palette before styles load. Only the theme preference is stored. */
(() => {
  const key = 'buggcraft.theme';
  const root = document.documentElement;
  let theme = 'dark';
  try { if (localStorage.getItem(key) === 'light') theme = 'light'; } catch (_) {}
  root.dataset.theme = theme;

  function refresh() {
    const button = document.getElementById('theme-toggle');
    if (!button) return;
    const light = root.dataset.theme === 'light';
    button.textContent = light ? '深色模式' : '浅色模式';
    button.setAttribute('aria-label', light ? '切换到深色主题' : '切换到浅色主题');
    button.setAttribute('aria-pressed', String(light));
    button.title = button.getAttribute('aria-label');
  }
  function mount() {
    const nav = document.querySelector('.topbar > nav, .nav-links, .theme-nav');
    if (!nav || document.getElementById('theme-toggle')) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.id = 'theme-toggle';
    button.className = 'theme-toggle';
    button.addEventListener('click', () => {
      root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
      try { localStorage.setItem(key, root.dataset.theme); } catch (_) {}
      refresh();
    });
    nav.append(button);
    refresh();
  }
  window.addEventListener('storage', event => {
    if (event.key === key) {
      root.dataset.theme = event.newValue === 'light' ? 'light' : 'dark';
      refresh();
    }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
})();
