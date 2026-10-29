(() => {
  function detect() {
    const hints = [navigator.userAgentData?.platform, navigator.platform, navigator.userAgent].filter(Boolean).join(' ');
    if (/Windows/i.test(hints)) return 'windows';
    if (/Mac|iPhone|iPad/i.test(hints)) return 'mac';
    if (/Linux|Android|X11/i.test(hints)) return 'linux';
    return 'windows';
  }

  window.BuggcraftPlatform = { detect };
})();
