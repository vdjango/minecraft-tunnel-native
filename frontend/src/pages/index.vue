<script setup lang="ts">
defineProps<{seed:Record<string,string>}>();
</script>

<template>
<div class="ambient"></div>
  <header class="shell topbar site-header"><a class="brand" href="/"><span class="mark">B<span>·</span></span><span>BUGGCRAFT<small>MINECRAFT COMMUNITY</small></span></a><nav aria-label="网站导航"><a href="/network">节点网络</a><a href="/connect">连接启动器</a><a href="/developers">开发文档</a><a href="/feedback">问题反馈</a><a id="open-account" href="/account">用户中心</a></nav></header>

  <main class="shell">
    <section class="hero">
      <div><div class="eyebrow"><span class="live-dot"></span><span id="health">正在获取节点状态</span></div>
        <h1 id="node-name-heading">社区联机节点</h1>
        <p class="intro">连接朋友的世界。</p>
        <div class="hero-actions"><a id="open-connect" class="primary" href="/connect">连接启动器 <span>↗</span></a></div>
      </div>
      <div class="orbit" aria-label="节点网络示意">
        <div class="orbit-ring ring-one"></div><div class="orbit-ring ring-two"></div>
        <div class="orbit-line l1"></div><div class="orbit-line l2"></div><div class="orbit-line l3"></div>
        <div class="planet p1"></div><div class="planet p2"></div><div class="planet p3"></div>
        <div class="core"><span class="core-icon">⌘</span><span id="node-name">COMMUNITY NODE</span><small>YOUR WORLD, CONNECTED</small></div>
        <span class="orbit-caption">INDEPENDENT NODES. SHARED CONNECTIONS.</span>
      </div>
    </section>
    <section class="metrics" aria-label="实时指标">
      <article><span class="metric-label">本机房间</span><strong id="rooms">—</strong><small id="room-limit">等待同步</small></article>
      <article><span class="metric-label">联邦在线会话</span><strong id="players">—</strong><small id="player-paths">P2P与中转统计待确认</small></article>
      <article><span class="metric-label">游戏内玩家</span><strong id="game-players">—</strong><small id="game-coverage">等待游戏上报</small></article>
      <article><span class="metric-label">连续运行</span><strong id="uptime">—</strong><small id="total-days" title="自启用累计统计起，更新节点后继续累加">累计运行统计中</small></article>
    </section>


<section class="user-card"><div class="page-title-row"><div><h2>节点网络</h2><p>查看在线节点、地区与连接质量。</p></div><a class="secondary" href="/network">查看全部节点 →</a></div></section>
    <section class="details"><div><span class="eyebrow muted">NODE IDENTITY</span><h3>核对节点身份</h3><p>通过可信渠道获取接入码。接入码包含节点公钥，启动器会在每次连接时核对；可向管理员核实右侧指纹。</p></div><div class="identity"><span>SSH 公钥指纹</span><code id="fingerprint">—</code><span id="node-id">—</span></div></section>
    <footer><span>BUGGCRAFT <span class="muted">/ 联机，由你和朋友一起搭建。</span></span><span>独立节点 · 共享连接</span></footer>
  </main>
</template>
