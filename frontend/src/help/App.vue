<script setup lang="ts">
import { computed, provide, ref, watch } from 'vue';
import { RouterLink, RouterView, useRoute } from 'vue-router';
import { useHead } from '@unhead/vue';
import { categories, findGuide, guides, guidePath, updated } from './content';
import brand from '../brand.json';

const route = useRoute();
const open = ref(false);
const search = ref('');
provide('helpSearch', search);
watch(() => route.fullPath, () => { open.value = false; });
const guide = computed(() => findGuide(String(route.params.slug || '')));
const matching = computed(() => guides.filter(item => (item.title + item.summary + item.keywords.join(' ')).toLowerCase().includes(search.value.trim().toLowerCase())));
const title = computed(() => guide.value ? `${guide.value.title} - BUGG启动器教程` : 'BUGG启动器（Buggcraft）教程 - 下载游戏、整合包与好友联机');
const description = computed(() => guide.value ? `BUGG启动器（Buggcraft）教程：${guide.value.summary}` : 'BUGG启动器（Buggcraft）使用教程：下载 Minecraft Java 版、导入整合包、安装模组、创建房间、同步客户端和文件下载加速。按步骤准备游戏，和朋友一起玩。');
const canonical = computed(() => 'https://bugg.icu' + route.path.replace(/\/$/, ''));
useHead(() => ({
  title: title.value,
  htmlAttrs: { lang: 'zh-CN' },
  meta: [
    { name: 'description', content: description.value },
    { name: 'keywords', content: [brand.name, ...brand.alternateName, ...(guide.value?.keywords || ['我的世界联机教程', 'Minecraft启动器', '下载游戏', '导入整合包'])].join(',') },
    { name: 'robots', content: route.meta.notFound ? 'noindex,follow' : 'index,follow,max-image-preview:large' },
    { property: 'og:type', content: guide.value ? 'article' : 'website' },
    { property: 'og:title', content: title.value },
    { property: 'og:description', content: description.value },
    { property: 'og:url', content: canonical.value },
    { property: 'og:site_name', content: brand.name },
    { property: 'og:image', content: 'https://bugg.icu/help-assets/tutorial-cover.jpg' },
    { name: 'twitter:card', content: 'summary_large_image' },
  ],
  link: [{ rel: 'canonical', href: canonical.value }],
  script: [{ type: 'application/ld+json', innerHTML: JSON.stringify({
    '@context': 'https://schema.org',
    '@graph': [
      { '@type': 'SoftwareApplication', '@id': brand.productId, name: brand.name, alternateName: brand.alternateName,
        url: brand.url, applicationCategory: 'GameApplication', operatingSystem: 'Windows, macOS, Linux',
        sameAs: ['https://github.com/vdjango/buggcraft'], downloadUrl: 'https://download.bugg.icu/' },
      { '@type': 'WebSite', '@id': brand.websiteId, name: brand.name, alternateName: brand.alternateName, url: brand.url },
      { '@type': guide.value ? 'TechArticle' : 'CollectionPage', headline: title.value, description: description.value,
        url: canonical.value, inLanguage: 'zh-CN', dateModified: updated,
        about: { '@id': brand.productId }, isPartOf: { '@id': brand.websiteId },
        author: { '@type': 'Organization', name: 'BUGG 项目维护者', url: 'https://bugg.icu/' },
        publisher: { '@type': 'Organization', name: 'BUGGCRAFT', url: 'https://bugg.icu/' },
        image: 'https://bugg.icu/help-assets/tutorial-cover.jpg' },
      { '@type': 'BreadcrumbList', itemListElement: [
        { '@type': 'ListItem', position: 1, name: 'BUGGCRAFT', item: 'https://bugg.icu/' },
        { '@type': 'ListItem', position: 2, name: '玩家教程', item: 'https://bugg.icu/docs' },
        ...(guide.value ? [{ '@type': 'ListItem', position: 3, name: guide.value.title, item: canonical.value }] : []),
      ] },
    ],
  }) }],
}));
</script>

<template>
  <a class="skip" href="#main-content">跳至正文</a>
  <header class="header">
    <a href="/" class="brand"><span class="brand-icon">B</span><span>BUGGCRAFT <small>玩家教程</small></span></a>
    <nav aria-label="网站导航" class="top-nav">
      <a href="/">官网</a><RouterLink to="/docs" aria-current="page">教程</RouterLink><a href="https://mirror.bugg.icu/">镜像源</a><a href="https://node.bugg.icu/">联机节点</a>
    </nav>
    <a class="download" href="https://download.bugg.icu/">下载启动器 <span aria-hidden="true">↗</span></a>
    <button type="button" class="menu-toggle" :aria-expanded="open" @click="open = !open">{{ open ? '关闭目录' : '教程目录' }}</button>
  </header>
  <div class="layout">
    <aside class="sidebar" :class="{ open }" aria-label="教程导航">
      <label class="search"><span aria-hidden="true">⌕</span><input v-model="search" type="search" placeholder="搜索教程" aria-label="搜索教程" /></label>
      <RouterLink to="/docs" class="sidebar-home" :class="{ selected: route.path === '/docs' }">全部教程 <span>08</span></RouterLink>
      <div v-for="category in categories" :key="category" class="nav-group">
        <p>{{ category }}</p>
        <RouterLink v-for="item in matching.filter(item => item.category === category)" :key="item.slug" :to="guidePath(item.slug)" :class="{ selected: guide?.slug === item.slug }">{{ item.title }}</RouterLink>
      </div>
      <p v-if="!matching.length" class="search-empty">没有找到相关教程，换个关键词试试。</p>
      <div class="sidebar-bottom"><span class="dot"></span> 游戏 · 整合包 · 好友联机<a href="https://github.com/vdjango/buggcraft" target="_blank" rel="noopener">GitHub ↗</a></div>
    </aside>
    <button v-if="open" class="scrim" aria-label="关闭教程目录" @click="open = false"></button>
    <main id="main-content" class="main"><RouterView /></main>
  </div>
  <footer class="footer"><span>BUGGCRAFT · 玩家教程</span><span>QQ群 849362477</span><a href="https://node.bugg.icu/feedback?new=1">反馈问题 ↗</a><small>与 Mojang / Microsoft 无隶属关系。</small></footer>
</template>
