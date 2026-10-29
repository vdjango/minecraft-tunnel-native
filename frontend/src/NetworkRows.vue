<script setup lang="ts">
export interface NetworkNode {name:string;local?:boolean;region?:string;status:string;latency_ms?:number;utilization?:number;accepting:boolean;remaining_visitors:number|null}
defineProps<{nodes:NetworkNode[]}>();
const statuses:Record<string,string>={online:'在线',indirect:'在线',suspect:'连接不稳定',offline:'离线',blocked:'已隔离'};
const values=(n:NetworkNode)=>[n.name+(n.local?' · 当前节点':''),n.region||'未设置',statuses[n.status]||'未知',n.local?'本节点':Number.isFinite(n.latency_ms)?n.latency_ms!.toFixed(1)+' ms':'暂无测量',Number.isFinite(n.utilization)?Math.round(n.utilization!*100)+'%':'未配置',n.accepting?(n.remaining_visitors===null?'未配置':n.remaining_visitors+' 人'):'暂停接纳'];
</script>
<template><tr v-for="(node,index) in nodes" :key="index"><td v-for="(value,i) in values(node)" :key="i">{{ value }}</td></tr></template>
