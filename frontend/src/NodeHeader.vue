<script setup lang="ts">
import {adminSectionFor,adminSections,itemActive,siteLinks} from './nodeNavigation';
defineProps<{admin?:boolean}>();
const path=typeof location==='undefined'?'/':location.pathname;
</script>
<template>
  <header class="shell topbar site-header">
    <a class="brand" :href="admin?'/admin':'/'"><span class="mark">B<span>·</span></span><span>BUGGCRAFT<small>{{admin?'NODE MANAGEMENT':'MINECRAFT COMMUNITY'}}</small></span></a>
    <nav :aria-label="admin?'节点管理导航':'网站导航'">
      <template v-if="admin"><a v-for="section in adminSections" :key="section.id" :href="section.href" :aria-current="adminSectionFor(path)===section.id?'location':undefined">{{section.label}}</a></template>
      <template v-else><a v-for="link in siteLinks" :id="'id' in link?link.id:undefined" :key="link.href" :href="link.href" :aria-current="itemActive(path,link.href)?'page':undefined">{{link.label}}</a></template>
    </nav>
  </header>
</template>
