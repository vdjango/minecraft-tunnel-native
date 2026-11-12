'use strict';
const nodeAddress = $('node-address');
nodeAddress.textContent = location.origin;
$('copy-node-address').onclick = async () => {
  const status = $('node-address-feedback');
  try {
    await navigator.clipboard.writeText(location.origin);
    status.textContent = (nodeAddress.dataset.label || '节点地址') + '已复制。';
  } catch {
    const selection = getSelection(), range = document.createRange();
    range.selectNodeContents(nodeAddress);selection.removeAllRanges();selection.addRange(range);
    status.textContent = (nodeAddress.dataset.label || '节点地址') + '已选中，请按 Ctrl+C 或长按复制。';
  }
};
