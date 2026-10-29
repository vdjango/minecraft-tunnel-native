<script setup lang="ts">
defineProps<{seed:Record<string,string>}>();
</script>

<template>
<header class="site-header"><nav class="wrap nav" aria-label="网站导航">
    <a class="brand" href="https://bugg.icu/"><span class="brand-mark">B</span><span class="brand-name"><strong>BUGGCRAFT</strong><small>MINECRAFT JAVA LAUNCHER</small></span></a>
    <a class="nav-link" href="https://bugg.icu/">返回官网</a><a class="nav-link" href="https://download.bugg.icu/admin/">版本管理</a>
    <button class="theme-toggle" id="theme-toggle" type="button" aria-pressed="false">浅色主题</button>
  </nav></header>
  <main class="wrap">
    <section class="page-intro" aria-labelledby="page-title">
      <div><p class="eyebrow">BUGGCRAFT LAUNCHER · OFFICIAL DOWNLOADS</p><h1 id="page-title">下载 <span>Buggcraft</span></h1></div>
      <aside class="release-summary" hidden><small>最近公开发行</small><strong id="release-summary-version">正在读取版本…</strong><span id="release-summary-date">读取官网版本缓存</span></aside>
    </section>

    <div class="download-layout">
      <section class="download-main" aria-labelledby="platform-title">
        <p class="section-overline">选择操作系统</p>
        <div class="platform-tabs" role="group" aria-label="操作系统">
          <button class="platform-tab" type="button" data-platform="windows" aria-pressed="false"><span class="symbol" aria-hidden="true">▦</span>Windows</button>
          <button class="platform-tab" type="button" data-platform="mac" aria-pressed="false"><span class="symbol" aria-hidden="true">⌘</span>macOS</button>
          <button class="platform-tab" type="button" data-platform="linux" aria-pressed="false"><span class="symbol" aria-hidden="true">◈</span>Linux</button>
        </div>
        <div class="selected-platform">
          <div><h2 id="platform-title">Windows</h2><p class="platform-description" id="platform-description"></p><div class="arch-picker" id="platform-architecture" hidden><button class="arch-button" type="button" data-arch="arm64" aria-pressed="true">Apple Silicon · M 系列</button><button class="arch-button" type="button" data-arch="x86_64" aria-pressed="false">Intel</button></div></div>
          <span class="detected-pill" id="detected-pill">与你的系统相符</span>
        </div>
        <div class="release-box"><div><h3 id="selected-release-title">正在检查公开发行文件</h3><p id="selected-release-meta">下载链接会对应上方所选系统。</p></div><a id="selected-download" class="download-button" href="#" aria-disabled="true">正在读取下载列表</a></div>
        <div class="release-links"><a id="checksum-link" class="text-link" href="#" hidden>SHA-256 校验文件 ↓</a><a id="release-notes-link" class="text-link" href="#history-title">查看本地发行说明 ↓</a></div>
        <p class="status-message" id="download-status" role="status" aria-live="polite"></p>
      </section>
      </div>

    <section class="history-section" aria-labelledby="history-title"><div class="history-heading"><div><h2 id="history-title">历史版本</h2><p>新版本由发布后台管理，发布后立即出现在这里。</p></div></div><div class="history-list" id="release-history" aria-live="polite"><div class="history-empty">正在读取版本记录…</div></div><button class="history-more" id="history-more" type="button" hidden>显示更多版本</button></section>

    <div class="support-details">
      <details class="info-details"><summary>第一次使用？</summary><ol class="steps"><li><span class="step-no">01</span><span>下载当前系统的启动器。</span></li><li><span class="step-no">02</span><span>解压下载的 ZIP：Windows 运行 Buggcraft.exe，macOS 打开 Buggcraft.app，Linux 运行启动程序。</span></li><li><span class="step-no">03</span><span>使用你自己的 Minecraft Java 版账号和游戏文件。</span></li></ol><p class="fine-print">Buggcraft 是 Minecraft Java 版启动器，不包含 Minecraft 游戏本体。Windows 版本可能显示 SmartScreen 提示；请确认下载来源为本官网提供的安装文件。macOS 芯片类型可在上方手动切换。</p></details>
      <details class="info-details"><summary>发布新版本</summary><p>GitHub Actions 构建完成后，平台自动同步四个平台的安装包并缓存、校验，无需手动上传。后台选择需要审核时，版本先进入待审核；选择无需审核时，校验通过后直接发布。修改设置只影响后续入库版本。</p><a class="text-link" href="https://download.bugg.icu/admin/">打开版本管理后台 ↗</a></details>
    </div>
  </main>
  <footer class="site-footer wrap"><span>Buggcraft · Minecraft Java 启动器</span><nav aria-label="更多信息"><a href="https://github.com/vdjango/buggcraft">开源代码 ↗</a><a href="https://node.bugg.icu/network">联机节点</a><a href="https://node.bugg.icu/feedback?new=1">问题反馈</a></nav></footer>
</template>
