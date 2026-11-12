<script setup lang="ts">
import {adminSectionFor,adminSections,itemActive} from './nodeNavigation';
const path=location.pathname;
const section=adminSections.find(entry=>entry.id===adminSectionFor(path))!;
</script>
<template>
  <nav class="console-nav" :aria-label="section.label+'导航'">
    <span class="sidebar-label">{{section.label}}</span>
    <a v-for="item in section.items" :key="item.href" :href="item.href" :aria-current="itemActive(path,item.href)?'page':undefined">{{item.label}}</a>
    <button id="logout" class="quiet" type="button">退出登录</button>
  </nav>
</template>
