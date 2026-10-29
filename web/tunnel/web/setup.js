'use strict';
// Onboarding serves setup.js and setup.css without the normal portal routes.
(() => {
  const key = 'buggcraft.theme';
  const root = document.documentElement;
  let theme = 'dark';
  try { if (localStorage.getItem(key) === 'light') theme = 'light'; } catch (_) {}
  root.dataset.theme = theme;
  const nav = document.querySelector('.theme-nav');
  if (!nav) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'theme-toggle';
  button.className = 'theme-toggle';
  function refresh() {
    const light = root.dataset.theme === 'light';
    button.textContent = light ? '深色模式' : '浅色模式';
    button.setAttribute('aria-label', light ? '切换到深色主题' : '切换到浅色主题');
    button.setAttribute('aria-pressed', String(light));
  }
  button.addEventListener('click', () => {
    root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
    try { localStorage.setItem(key, root.dataset.theme); } catch (_) {}
    refresh();
  });
  window.addEventListener('storage', event => {
    if (event.key !== key) return;
    root.dataset.theme = event.newValue === 'light' ? 'light' : 'dark';
    refresh();
  });
  nav.append(button);
  refresh();
})();
const form = document.querySelector('#setup-form');
const panels = [...form.querySelectorAll('fieldset')];
const next = document.querySelector('#next');
const back = document.querySelector('#back');
const save = document.querySelector('#save');
const error = document.querySelector('#error');
let step = 0;
function display() {
  panels.forEach((panel, i) => { panel.hidden = i !== step; });
  document.querySelectorAll('#steps li').forEach((item, i) => item.classList.toggle('active', i === step));
  back.hidden = step === 0; next.hidden = step === 2; save.hidden = step !== 2;
  document.querySelector('#progress').textContent = `${step + 1} / 3`;
}
function valid(panel) {
  for (const input of panel.querySelectorAll('input')) { if (!input.reportValidity()) return false; }
  return true;
}
next.addEventListener('click', () => { if (valid(panels[step])) { step++; display(); } });
back.addEventListener('click', () => { step--; display(); });
form.elements.host.value = location.hostname.replace(/^\[|\]$/g, '');
form.elements.bandwidth_mb_s.addEventListener('input', () => {
  const bandwidth = Number(form.elements.bandwidth_mb_s.value);
  document.querySelector('#budget').textContent = bandwidth > 0
    ? `当前预算允许最多 ${Math.floor(bandwidth * .65 / (.128 * 1.15))} 位中转访客。保留 35% 空闲，按每人 0.128 MB/s 加 15% 协议开销估算；首次加载区块可能超过预算，仍需根据实际流量调整。`
    : '暂不按带宽限制准入人数。填写实际可用上传带宽后，可启用人数预算；这不能保证所有世界和视距下都流畅。';
});
form.noValidate = true;
form.addEventListener('submit', async event => {
  event.preventDefault(); error.hidden = true;
  for (let i = 0; i < panels.length; i++) {
    if (![...panels[i].querySelectorAll('input')].every(input => input.checkValidity())) {
      step = i; display(); valid(panels[i]); return;
    }
  }
  const data = Object.fromEntries(new FormData(form));
  data.public_join = form.elements.public_join.checked;
  save.disabled = true; back.disabled = true; save.textContent = '正在生成密钥并保存…';
  try {
    const response = await fetch('/setup', {method:'POST', headers:{'Content-Type':'application/json',
      'X-Setup-Token':document.querySelector('#setup-token').value}, body:JSON.stringify(data)});
    if (!response.ok) { let message = '初始化失败，请检查配置后重试。';
      if ((response.headers.get('content-type') || '').includes('application/json')) message = (await response.json()).error || message;
      throw new Error(message);
    }
    const result = await response.json();
    form.reset(); form.hidden = true; document.querySelector('#success').hidden = false;
    const link = document.querySelector('#console-link'); link.href = result.url;
    history.replaceState(null, '', '/');
  } catch (failure) { error.textContent = failure.message; error.hidden = false; }
  finally { save.disabled = false; back.disabled = false; save.textContent = '保存并启动节点'; }
});
