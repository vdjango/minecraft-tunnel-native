(() => {
  const links = [...document.querySelectorAll('[data-section-nav]')];
  if (!links.length || !('IntersectionObserver' in window)) return;

  const sections = links
    .map(link => document.querySelector(link.getAttribute('href')))
    .filter(Boolean);
  const linkBySection = new Map(
    links.map(link => [document.querySelector(link.getAttribute('href')), link])
  );

  const observer = new IntersectionObserver(entries => {
    const visible = entries
      .filter(entry => entry.isIntersecting)
      .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
    if (!visible.length) return;

    const active = linkBySection.get(visible[0].target);
    for (const link of links) {
      const selected = link === active;
      link.classList.toggle('active', selected);
      if (selected) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    }
  }, {
    rootMargin: '-18% 0px -58% 0px',
    threshold: 0
  });

  for (const section of sections) observer.observe(section);
  scope.onDispose(() => observer.disconnect());

  document.addEventListener('click', event => {
    for (const menu of document.querySelectorAll('.platform-menu[open]')) {
      if (!menu.contains(event.target)) menu.open = false;
    }
  });
})();
