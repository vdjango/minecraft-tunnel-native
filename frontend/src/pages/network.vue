<script setup lang="ts">
import NodeHeader from '../NodeHeader.vue';
import ProductLinks from '../ProductLinks.vue';
defineProps<{seed:Record<string,string>}>();
</script>

<template>
<NodeHeader />
<main class="shell user-main"><div class="page-heading"><h1>节点网络</h1><p>找到适合你的联机入口。</p></div><p id="network-feedback" class="feedback" role="status"></p><div class="network-stats"><div><span>已知节点</span><strong id="network-total">—</strong></div><div><span>在线节点</span><strong id="network-online">—</strong></div><div><span>可接纳节点</span><strong id="network-accepting">—</strong></div></div><section class="user-card"><div class="page-title-row"><h2>在线节点</h2><button class="secondary" id="refresh-network" type="button">刷新状态</button></div><div class="user-table-scroll"><table class="network-table"><thead><tr><th>节点</th><th>地区</th><th>状态</th><th>节点间延迟</th><th>带宽负载</th><th>预计中转余量</th></tr></thead><tbody id="network-rows"></tbody></table></div><p>仅显示心跳正常的节点，离线后自动隐藏。人数按上传带宽预算估算，指额外可加入的访客，不含房主；CPU、内存、世界和视距仍会影响实际体验。延迟以当前节点为观察点。</p></section></main>
<ProductLinks />
</template>
