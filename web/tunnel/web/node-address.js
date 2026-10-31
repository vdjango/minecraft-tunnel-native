'use strict';
const nodeAddress = $('node-address');
nodeAddress.textContent = location.origin;
$('copy-node-address').onclick = async () => {
  const status = $('node-address-feedback');
  try {
    await navigator.clipboard.writeText(location.origin);
    status.textContent = '节点地址已复制。';
  } catch {
    const selection = getSelection(), range = document.createRange();
    range.selectNodeContents(nodeAddress);selection.removeAllRanges();selection.addRange(range);
    status.textContent = '节点地址已选中，请按 Ctrl+C 或长按复制。';
  }
};
