/* Apply the saved palette before styles load. Only the theme preference is stored. */
(() => {
  function updateDownloadLinks() {
    const platform = window.BuggcraftPlatform?.detect() || 'windows';
    const details = {
      windows: ['Windows', 'Windows 10 / 11 · x64'],
      mac: ['macOS', 'macOS · Intel / Apple Silicon'],
      linux: ['Linux', 'Linux · x86_64']
    };
    const [name, detail] = details[platform];
    for (const link of document.querySelectorAll('[data-platform-cta]')) {
      const url = new URL(link.href, location.href);
      url.searchParams.set('platform', platform);
      link.href = url.href;
      const label = link.querySelector('[data-platform-label]');
      if (label) label.textContent = `下载 ${name} 启动器`;
      const description = link.querySelector('[data-platform-detail]');
      if (description) description.textContent = detail;
      link.dataset.detectedPlatform = platform;
    }
  }

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
    const nav = document.querySelector('.main-nav, .topbar > nav, .nav-links, .theme-nav');
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
  function initialize() {
    mount();
    updateDownloadLinks();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
