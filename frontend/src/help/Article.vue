<script setup lang="ts">
import { computed } from 'vue';
import { RouterLink, useRoute } from 'vue-router';
import { findGuide, guides, guidePath, updated } from './content';
const route = useRoute();
const guide = computed(() => findGuide(String(route.params.slug))!);
const index = computed(() => guides.findIndex(item => item.slug === guide.value.slug));
const previous = computed(() => guides[index.value - 1]);
const next = computed(() => guides[index.value + 1]);
</script>
<template>
  <div v-if="guide" class="article-layout">
    <article class="article">
      <nav class="breadcrumb" aria-label="当前位置"><RouterLink to="/docs">玩家教程</RouterLink><span>/</span>{{ guide.category }}</nav>
      <header class="article-heading"><p class="eyebrow">{{ guide.category }} · {{ guide.minutes }} 分钟阅读</p><h1>{{ guide.title }}</h1><p class="summary">{{ guide.summary }}</p><p class="article-date">更新于 {{ updated }} · BUGG 项目维护者</p></header>
      <details class="mobile-toc"><summary>本篇目录</summary><RouterLink v-for="section in guide.sections" :key="section.id" :to="{ path: route.path, hash: '#' + section.id }">{{ section.title }}</RouterLink></details>
      <section v-for="(section, number) in guide.sections" :key="section.id" :id="section.id" class="article-section">
        <h2><span>{{ String(number + 1).padStart(2, '0') }}</span>{{ section.title }}</h2>
        <p v-for="paragraph in section.paragraphs" :key="paragraph">{{ paragraph }}</p>
        <pre v-if="section.code" class="code-example"><code>{{ section.code }}</code></pre>
        <ol v-if="section.steps" class="steps"><li v-for="step in section.steps" :key="step">{{ step }}</li></ol>
        <figure v-if="section.image"><a :href="'/help-assets/' + section.image" target="_blank" rel="noopener noreferrer" aria-label="打开原图查看"><img :src="'/help-assets/' + section.image" :alt="section.caption || section.title" :width="section.imageWidth || 960" :height="section.imageHeight || 600" loading="lazy" decoding="async" /></a><figcaption>{{ section.caption }}</figcaption></figure>
        <div v-if="section.links" class="article-sources"><a v-for="link in section.links" :key="link.href" :href="link.href" target="_blank" rel="noopener noreferrer">{{ link.label }} ↗</a></div>
        <aside v-if="section.note" class="note"><strong>记住这一点</strong><p>{{ section.note }}</p></aside>
      </section>
      <nav class="article-pagination" aria-label="继续阅读"><RouterLink v-if="previous" :to="guidePath(previous.slug)"><small>← 上一篇</small><strong>{{ previous.title }}</strong></RouterLink><RouterLink v-if="next" :to="guidePath(next.slug)"><small>下一篇 →</small><strong>{{ next.title }}</strong></RouterLink></nav>
      <div class="article-feedback"><span>界面随版本更新，以当前启动器提示为准。</span><a href="https://bugg.icu/#community">反馈教程问题 ↗</a></div>
    </article>
    <aside class="toc" aria-label="本篇目录"><p>本篇目录</p><RouterLink v-for="section in guide.sections" :key="section.id" :to="{ path: route.path, hash: '#' + section.id }">{{ section.title }}</RouterLink><a class="toc-download" href="https://download.bugg.icu/">还没有启动器？<strong>前往下载 ↗</strong></a></aside>
  </div>
</template>
