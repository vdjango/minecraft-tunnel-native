<script setup lang="ts">
import { onBeforeUnmount } from 'vue';
import NodeHeader from '../NodeHeader.vue';
import { useFeedback } from '../feedback';
import '../styles/account.css';
import '../styles/feedback.css';

const model = useFeedback();
const {keyInput, connected, busy, error, visibleItems, detail, cursor, history, filter, files, selectedFile, previewReady, preview} = model;
onBeforeUnmount(model.dispose);
function logout() { window.location.reload(); }
function date(value: string) { return value ? new Date(value).toLocaleString('zh-CN') : '—'; }
</script>
<template>
  <NodeHeader />
  <main class="shell feedback-main">
    <header class="feedback-heading"><div><p class="intro-label">BUGG · 维护者工作台</p><h1>反馈管理</h1><p class="feedback-muted">从玩家的问题，到可排查的日志。</p></div><button v-if="connected" class="secondary" @click="logout">退出管理</button></header>
    <p v-if="error" class="feedback-error" role="alert">{{error}}</p>
    <section v-if="!connected" class="feedback-login">
      <h2>登录反馈管理</h2><p class="feedback-muted">使用独立的反馈管理密钥。它只允许查看反馈和诊断日志。</p>
      <form @submit.prevent="model.login"><label for="feedback-key">反馈管理密钥<input id="feedback-key" v-model="keyInput" type="password" autocomplete="off" maxlength="4096" required :disabled="busy" placeholder="请输入管理密钥"></label><button class="primary wide" :disabled="busy">{{busy?'正在登录…':'进入反馈管理'}}</button></form>
      <small>密钥仅保留在当前页面，刷新或退出后需要重新登录。这里不使用玩家账号密码。</small>
    </section>
    <template v-else>
      <div class="feedback-toolbar"><input v-model="filter" aria-label="筛选当前页反馈" placeholder="筛选本页的问题、编号或联系方式"><button class="secondary" :disabled="busy" @click="model.refresh">{{busy?'读取中…':'刷新'}}</button></div>
      <div class="feedback-workspace">
        <section class="feedback-list" aria-label="反馈列表"><p class="feedback-muted">第 {{history.length}} 页 · {{visibleItems.length}} 条</p>
          <p v-if="!visibleItems.length" class="feedback-empty">{{busy?'正在读取反馈…':'暂无匹配反馈。可刷新或切换页码。'}}</p>
          <button v-for="item in visibleItems" :key="item.id" class="feedback-row" :class="{selected:detail?.id===item.id}" :disabled="busy" @click="model.open(item)"><span class="feedback-row-heading"><time>{{date(item.created_at)}}</time><span>{{item.has_archive?'含诊断包':'无附件'}}</span></span><strong>{{item.message}}</strong><span class="feedback-muted">{{model.notification(item)}}</span></button>
          <nav class="feedback-pagination" aria-label="反馈分页"><button class="secondary" :disabled="busy||history.length<2" @click="model.page(false)">上一页</button><button class="secondary" :disabled="busy||!cursor" @click="model.page(true)">下一页</button></nav>
        </section>
        <section class="feedback-detail" aria-label="反馈详情">
          <p v-if="!detail" class="feedback-empty">{{busy?'正在读取…':'选择一条反馈，查看问题与诊断。'}}</p>
          <template v-else><div class="feedback-detail-heading"><h2>反馈详情</h2><span class="feedback-muted">{{date(detail.created_at)}}</span></div><p class="feedback-id">{{detail.id}}</p>
            <h3>遇到的问题</h3><p class="feedback-text">{{detail.message}}</p><h3>期望的结果</h3><p class="feedback-text">{{detail.expected||'未填写'}}</p>
            <dl class="feedback-meta"><div><dt>联系方式</dt><dd>{{detail.contact||'未填写'}}</dd></div><div><dt>提交账号</dt><dd>{{detail.account_id||'历史反馈'}}</dd></div><div><dt>邮件通知</dt><dd>{{model.notification(detail)}}</dd></div></dl>
            <div class="feedback-actions"><button class="primary" :disabled="busy||!detail.has_archive" @click="model.archive(false)">查看诊断日志</button><button class="secondary" :disabled="busy||!detail.has_archive" @click="model.archive(true)">下载日志包</button></div>
            <p v-if="!detail.has_archive" class="feedback-muted">这条历史反馈未附带诊断日志。</p>
            <section v-if="previewReady" class="feedback-preview"><label>日志文件<select v-model="selectedFile" aria-label="日志文件"><option v-for="(_,name) in files" :key="name" :value="name">{{name}}</option></select></label><p class="feedback-muted">预览每个文件最多 1 MiB；完整内容请下载日志包。</p><pre tabindex="0">{{preview||'暂无可预览的文本文件，请下载日志包查看。'}}</pre></section>
          </template>
        </section>
      </div>
      <p class="feedback-privacy">诊断可能包含网络地址与游戏信息，仅用于排查。请勿公开分享未经检查的日志包。反馈最长保留 90 天。</p>
    </template>
  </main>
</template>
