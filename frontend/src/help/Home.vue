<script setup lang="ts">
import { computed, inject, ref, type Ref } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import { guides, categories, guidePath } from './content';
const search = inject<Ref<string>>('helpSearch', ref(''));
const route = useRoute();
const visible = computed(() => guides.filter(item => (item.title + item.summary + item.keywords.join(' ')).toLowerCase().includes(search.value.trim().toLowerCase())));
</script>
<template>
  <div class="home">
    <div class="breadcrumb"><a href="/">BUGGCRAFT</a><span>/</span>玩家教程</div>
    <header class="home-heading"><p class="eyebrow">从准备游戏，到一起开玩</p><h1>{{ route.meta.notFound ? '这篇教程暂时找不到' : '准备好你的世界。' }}</h1><p>下载游戏、导入整合包，或者约上朋友开个房间。<br class="desktop" />从你现在要做的那一步开始。</p></header>
    <div v-if="!search && !route.meta.notFound" class="start-path" aria-label="推荐学习顺序">
      <RouterLink to="/docs/help/download-game"><span>01</span><div><strong>准备游戏</strong><small>版本与加载器</small></div><b>→</b></RouterLink>
      <RouterLink to="/docs/help/import-modpack"><span>02</span><div><strong>准备玩法</strong><small>整合包与模组</small></div><b>→</b></RouterLink>
      <RouterLink to="/docs/help/minecraft"><span>03</span><div><strong>叫上朋友</strong><small>建房与客户端同步</small></div><b>→</b></RouterLink>
    </div>
    <p v-if="search" class="search-result">“{{ search }}” · {{ visible.length }} 篇教程</p>
    <section v-for="(category, index) in categories" :key="category" class="guide-group">
      <template v-if="visible.some(item => item.category === category)">
        <h2><span>0{{ index + 1 }}</span>{{ category }}</h2>
        <div class="guide-grid"><RouterLink v-for="item in visible.filter(item => item.category === category)" :key="item.slug" :to="guidePath(item.slug)" class="guide-card"><div class="card-meta">{{ item.minutes }} 分钟阅读 <span>↗</span></div><h3>{{ item.title }}</h3><p>{{ item.summary }}</p></RouterLink></div>
      </template>
    </section>
    <div v-if="!visible.length" class="empty">没有找到相关教程。试试“整合包”“房间”或“模组”。</div>
    <div class="help-strip"><div><strong>遇到问题？先把问题定位清楚。</strong><p>下载失败、加载器不匹配、进不去世界，分别查看对应的排查步骤。</p></div><RouterLink to="/docs/help/troubleshooting">查看排查方法 →</RouterLink></div>
  </div>
</template>
