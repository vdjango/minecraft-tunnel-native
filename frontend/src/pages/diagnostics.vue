<script setup lang="ts">
defineProps<{seed:Record<string,string>}>();
</script>

<template>
<header class="shell topbar site-header"><a class="brand" href="/"><span class="mark">B<span>·</span></span><span>BUGGCRAFT<small>MINECRAFT COMMUNITY</small></span></a><nav aria-label="网站导航"><a href="/network">节点网络</a><a href="/connect">连接启动器</a><a href="/developers">开发文档</a><a href="/feedback">问题反馈</a><a id="open-account" href="/account">用户中心</a></nav></header>
<main class="shell user-main"><div :class="seed.layoutClass"><nav class="console-nav" aria-label="管理功能">
  <span class="sidebar-label">节点管理</span>
  <a href="/admin/nodes">节点列表</a>
  <a href="/admin/network">连接网络</a>
  <a href="/admin/settings">节点设置</a>
  <span class="sidebar-label">用户与支持</span>
  <a href="/admin/users">用户列表</a>
  <a href="/admin/players">在线玩家</a>
  <a href="/admin/feedback">反馈管理</a>
  <a href="/admin/diagnostics">连接诊断</a>
  <span class="sidebar-label">运维</span>
  <a href="/admin/mail">邮件服务</a>
  <a href="/admin/history">操作记录</a>
  <button id="logout" class="quiet" type="button">退出登录</button>
</nav>
<div class="management-main"><div class="page-heading"><span class="eyebrow">CONNECTION DIAGNOSTICS</span><h1>连接诊断</h1><p>查看断开原因、拥塞和异常时间点。</p></div><p id="diagnostic-feedback" class="feedback" role="status"></p><form id="diagnostic-login" class="user-card" hidden><h2>管理员登录</h2><label>管理员用户名<input id="username" value="admin" autocomplete="username" required></label><label>密码<input id="password" type="password" autocomplete="current-password" required></label><button class="primary" type="submit">登录</button></form><section id="diagnostic-panel" hidden><div class="user-card"><label>查找玩家<input id="player-filter" type="search" placeholder="用户名或匿名会话"></label><label>连接记录<select id="session-select"></select></label><p id="journal-health"></p><small>诊断记录按 7 天及容量上限轮转，关键事件优先保留，不包含游戏正文。</small></div><div id="session-detail" hidden><section class="user-card"><h2 id="session-title"></h2><p id="session-meta"></p><div class="diagnostic-metrics"><div><small>上传峰值 · MB/s</small><strong id="peak-upload">—</strong></div><div><small>下载峰值 · MB/s</small><strong id="peak-download">—</strong></div><div><small>节点调度延迟峰值 · ms</small><strong id="peak-lag">—</strong></div><div><small>游戏状态往返峰值 · ms</small><strong id="peak-rtt">—</strong></div></div><p id="findings" class="diagnostic-summary"></p><h3>异常时间点</h3><div id="diagnostic-alerts" class="action-buttons"></div></section><section class="user-card"><div class="section-heading"><h2>流量时间线</h2><button id="export-diagnostics" class="secondary" type="button">导出当前记录</button></div><canvas id="traffic-chart" height="180" aria-label="上传深紫色、下载浅紫色，单位 MB/s"></canvas><p>深紫色：上传　浅紫色：下载。P2P 数据不经过节点。</p><label>回看时间<input id="replay-position" type="range" min="0" max="0" value="0"></label><div class="action-buttons"><button id="replay-toggle" class="secondary" type="button">播放时间线</button><span id="replay-time"></span></div><details><summary>查看当前采样原始记录</summary><pre v-pre id="sample-detail" class="sample-detail"></pre></details></section><section class="user-card"><h2>连接事件</h2><div id="event-list"></div></section></div></section></div></div></main>
</template>
