'use strict';
async function copyLauncherCode() {
  const field = $('player-code'), status = $('connect-feedback');
  const code = field.value.trim();
  if (!code) { status.textContent = '请先生成接入码。'; return; }
  field.value = code;
  try {
    await navigator.clipboard.writeText(code);
    status.textContent = '接入码已复制。';
    return;
  } catch {}
  field.focus();
  field.select();
  try {
    if (document.execCommand('copy')) { status.textContent = '接入码已复制。'; return; }
  } catch {}
  status.textContent = '接入码已选中，请按 Ctrl+C 或长按复制。';
}
$('copy-code').onclick = copyLauncherCode;
$('player-code').onclick = () => $('player-code').select();
