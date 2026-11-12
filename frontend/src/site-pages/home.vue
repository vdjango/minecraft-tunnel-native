<script setup lang="ts">
import {ref} from 'vue';
<<<<<<< HEAD
import '../styles/home.css';
=======
>>>>>>> origin/main

defineProps<{seed:Record<string,string>}>();
const copiedContact=ref('');
const copyStatus=ref('');

async function copyContact(label:string,value:string){
  try{
    if(navigator.clipboard?.writeText){
      await navigator.clipboard.writeText(value);
    }else{
      const field=document.createElement('textarea');
      const focused=document.activeElement;
      field.value=value;
      field.style.cssText='position:fixed;opacity:0;pointer-events:none';
      field.readOnly=true;
      document.body.append(field);
      field.select();
      let copied=false;
      try{copied=document.execCommand('copy');}
      finally{field.remove();if(focused instanceof HTMLElement)focused.focus({preventScroll:true});}
      if(!copied)throw new Error('Copy unavailable');
    }
    copiedContact.value=value;
    copyStatus.value=`已复制${label}：${value}`;
  }catch{
    copiedContact.value='';
    copyStatus.value=`暂时无法自动复制，请选中${label} ${value} 手动复制。`;
  }
}
</script>

<template>
<<<<<<< HEAD
  <header class="site-header wrap">
    <a class="brand" href="#home" aria-label="Bugg 首页"><span class="brand-mark">B</span><strong>Bugg</strong></a>
    <nav class="main-nav" aria-label="网站导航"><a data-section-nav href="#features">启动与管理</a><a data-section-nav href="#multiplayer">好友联机</a><a data-section-nav href="#performance">性能</a><a data-native-navigation href="/docs">玩家教程</a></nav>
    <div class="header-actions"><a class="account-link" href="https://node.bugg.icu/login">联机账号 ↗</a><a class="button small" data-platform-cta href="https://download.bugg.icu/?platform=auto">下载 <span aria-hidden="true">↓</span></a></div>
=======
<header class="site-header">
    <a class="brand" href="#home" aria-label="Buggcraft 首页"><span class="brand-mark">B</span><span class="brand-type"><strong>BUGGCRAFT</strong><small>MINECRAFT JAVA LAUNCHER</small></span></a>
    <nav class="main-nav" aria-label="网站导航"><a class="nav-link active" data-section-nav href="#home" aria-current="location">首页</a><a class="nav-link" data-section-nav href="#features">功能</a><a class="nav-link" data-section-nav href="#nodes">联机节点</a><a class="nav-link" data-section-nav href="#community">玩家社区</a><a class="nav-link" data-section-nav data-native-navigation href="/docs">玩家教程</a></nav>
    <div class="header-actions"><details class="platform-menu"><summary class="header-secondary">Buggcraft 服务 <span aria-hidden="true">⌄</span></summary><div class="platform-menu-panel"><a href="https://mirror.bugg.icu/">镜像源 <span>↗</span><small>Minecraft 资源下载与启动器接入</small></a><a href="https://node.bugg.icu/">好友联机 <span>↗</span><small>连接方法与联机节点状态</small></a><a href="https://translate.bugg.icu/" target="_blank" rel="noopener">共享译文库 <span>↗</span><small>浏览与协作维护 Minecraft 译文</small></a><a href="https://github.com/vdjango/buggcraft" target="_blank" rel="noopener">GitHub 源码 <span>↗</span><small>查看项目代码与开发文档</small></a></div></details><a class="header-download" data-platform-cta href="https://download.bugg.icu/?platform=auto"><span data-platform-label>下载启动器</span> <span>↓</span></a></div>
>>>>>>> origin/main
  </header>
  <main>
<<<<<<< HEAD
    <section class="hero wrap" id="home" aria-labelledby="hero-title">
      <div class="hero-copy"><p class="eyebrow">为 Minecraft Java 版而造</p><h1 id="hero-title">打开游戏。<br>也打开朋友的世界。</h1><p class="hero-lead">启动、模组、整合包与好友联机。<br>熟悉的方块世界，一个更顺手的入口。</p><div class="hero-actions"><a class="button" data-platform-cta href="https://download.bugg.icu/?platform=auto"><span data-platform-label>下载启动器</span> ↓</a><a class="text-link" href="#features">认识 Bugg <span>↗</span></a></div><p class="platform-note" data-platform-detail>Windows · macOS · Linux</p></div>
      <figure class="hero-preview"><div class="preview-stack"><img class="preview-back" src="/help-assets/native-plaza.webp" alt="新版 Bugg 广场界面示例" width="960" height="600" decoding="async"><img class="preview-middle" src="/help-assets/native-create.webp" alt="新版 Bugg 创建联机房间界面示例" width="960" height="600" decoding="async"><img class="preview-front" src="/bugg-native-home-20261110.webp" alt="Bugg 原生启动器的风景启动首页" width="944" height="582" fetchpriority="high"></div><figcaption><span class="live-dot"></span> C++ / Qt Quick 原生界面 <span>启动 · 广场 · 联机</span></figcaption></figure>
    </section>
    <div class="feature-strip wrap"><span>原生 C++ / Qt Quick</span><i></i><span>游戏与内容，一处管理</span><i></i><span>一份联机账号，和朋友相连</span></div>

    <section class="section wrap" id="features" aria-labelledby="features-title">
      <div class="section-heading"><div><p class="eyebrow">少一点折腾，多一点开玩</p><h2 id="features-title">从想玩，到开玩。</h2></div><p>把版本、内容和任务收进清楚的工作区，<br>让下一步操作一眼就能找到。</p></div>
      <div class="feature-grid"><article class="feature-card"><span class="feature-icon">▧</span><h3>你的实例，各有位置</h3><p>管理多个游戏目录，选择版本与加载器。不同玩法分别保存，不必每次重新整理。</p><a data-native-navigation href="/docs/help/import-modpack">导入已有整合包 ↗</a></article><article class="feature-card"><span class="feature-icon">↓</span><h3>想找的内容，就在广场</h3><p>浏览模组、整合包、资源包与光影。按游戏版本和加载器筛选，再查看介绍与可用文件。</p><a data-native-navigation href="/docs">查看玩家教程 ↗</a></article><article class="feature-card"><span class="feature-icon">✓</span><h3>下载进度，清清楚楚</h3><p>查看每个任务与文件的状态。已经成功的文件会复用，失败项可以重试，不必整包重下。</p><a href="#performance">了解体验优化 ↗</a></article></div>
    </section>

    <section class="section multiplayer-section" id="multiplayer" aria-labelledby="multiplayer-title"><div class="wrap multiplayer-grid"><div><p class="eyebrow">好友联机 / 一起才好玩</p><h2 id="multiplayer-title">同一个世界。<br>不用研究一堆地址。</h2><p class="section-lead">在启动器里登录联机账号，创建房间或加入朋友。统一服务负责账号与连接分配，游戏数据通过直连或联机节点传输。</p><div class="numbered-steps"><p><b>01</b><span>打开「联机」，在浏览器确认登录</span></p><p><b>02</b><span>创建房间，把邀请发给朋友</span></p><p><b>03</b><span>连接成功，进入同一个世界</span></p></div><a class="text-link" href="https://node.bugg.icu/login">管理联机账号与设备 <span>↗</span></a></div><div class="connection-card"><div class="connection-topline"><span class="live-dot"></span> 连接你的朋友</div><div class="players"><div><span class="player-avatar">A</span><small>房主的世界</small></div><div class="connection-line"><span>↔</span><small>P2P / 中继</small></div><div><span class="player-avatar second">B</span><small>朋友的电脑</small></div></div><div class="connection-details"><p><strong>能直连，就直连</strong><span>网络允许时尝试 P2P，无法直连时使用中继。</span></p><p><strong>存档，仍在你手里</strong><span>房主运行游戏、保管世界。联机节点负责连接。</span></p><p><strong>默认服务，开箱使用</strong><span>通常无需填写地址；也保留自建服务配置。</span></p></div></div></div></section>
=======
    <section class="hero" id="home" aria-labelledby="hero-title">
      <div class="hero-inner">
        <div class="hero-copy">
          <p class="eyebrow"><span class="status-dot"></span> BUGG 启动器 · Minecraft Java 版</p>
          <h1 id="hero-title">Minecraft 版本管理<br><span>与好友联机</span></h1>
          <p class="hero-lead">安装游戏、管理模组、导入整合包，在一个启动器里完成。想和朋友一起玩，就创建一个房间。</p>
          <div class="hero-actions"><a class="download-button" data-platform-cta href="https://download.bugg.icu/?platform=auto"><span><span data-platform-label>下载启动器</span> <b>↓</b></span><small data-platform-detail>自动匹配 Windows、macOS 或 Linux</small></a><a class="text-link" href="https://translate.bugg.icu/" target="_blank" rel="noopener">共享译文库 <span>↗</span></a></div>
          <div class="hero-facts"><span><i>✓</i> 免费开源</span><span><i>✓</i> Java 版</span><span><i>✓</i> 好友联机</span></div>
          <a class="hero-community-link" href="#community"><span class="status-dot" aria-hidden="true"></span>每日联机来微信 <strong>junsi223</strong><span aria-hidden="true">↗</span></a>
        </div>
        <figure class="launcher-preview"><div class="preview-window"><picture><source type="image/webp" srcset="/launcher-preview-480-d1a783088222.webp 480w, /launcher-preview-926-b6ae62178aa8.webp 926w" sizes="(max-width: 600px) calc(100vw - 48px), (max-width: 900px) min(630px, calc(100vw - 82px)), 560px"><img fetchpriority="high" decoding="async" src="/launcher-preview.png" width="926" height="505" alt="Buggcraft 启动器主页，展示游戏登录、版本选择与联机大厅"></picture></div><figcaption><span><i class="caption-dot"></i> BUGGCRAFT LAUNCHER</span><span>游戏 · 下载 · 设置</span></figcaption></figure>
        <span class="hero-spark spark-one" aria-hidden="true">✳</span><span class="hero-spark spark-two" aria-hidden="true">✧</span>
      </div>
      <a class="scroll-hint" href="#features"><span>认识一下启动器</span><span class="scroll-line"></span><span>↓</span></a>
    </section>

    <section class="community-section section-wrap" id="community" aria-labelledby="community-title">
      <div class="community-heading"><div><p class="eyebrow">玩家社区 / 一起开玩</p><h2 id="community-title">每日联机，来微信一起玩。</h2><p class="community-intro">找队友、约联机、交流整合包，遇到问题也可以来聊聊。<br>优先添加微信 <strong>junsi223</strong> 联系入群。</p></div><span class="community-badge">建房 · 同步配置 · 一起开玩</span></div>
      <div class="community-contacts">
        <div class="community-contact community-contact-primary"><div class="community-contact-label"><span>微信 · 联系入群</span><span class="community-priority">优先推荐</span></div><div class="community-contact-value"><strong>junsi223</strong><button type="button" aria-label="复制微信号 junsi223" @click="copyContact('微信号','junsi223')">{{copiedContact==='junsi223'?'已复制 ✓':'复制微信号'}}</button></div><p>每日联机来微信，添加后联系入群。</p></div>
        <div class="community-contact"><div class="community-contact-label"><span>QQ 群</span></div><div class="community-contact-value"><strong>849362477</strong><button type="button" aria-label="复制 QQ 群号 849362477" @click="copyContact('QQ 群号','849362477')">{{copiedContact==='849362477'?'已复制 ✓':'复制群号'}}</button></div><p>在 QQ 搜索群号，和大家保持联系。</p></div>
        <div class="community-contact"><div class="community-contact-label"><span>频道号</span></div><div class="community-contact-value"><strong>pd40037206</strong><button type="button" aria-label="复制频道号 pd40037206" @click="copyContact('频道号','pd40037206')">{{copiedContact==='pd40037206'?'已复制 ✓':'复制频道号'}}</button></div><p>搜索频道号，找到我们的玩家社区。</p></div>
      </div>
      <p class="community-copy-status" role="status" aria-live="polite">{{copyStatus}}</p>
      <div class="community-help"><span>遇到问题？</span><a data-native-navigation href="/docs/help/launcher-windows">Windows 打不开 ↗</a><a data-native-navigation href="/docs/help/launcher-macos">macOS 打不开 ↗</a><a data-native-navigation href="/docs">查看玩家教程 ↗</a><a href="https://node.bugg.icu/feedback?new=1">提交反馈 ↗</a></div>
    </section>

    <section class="feature-section section-wrap" id="features" aria-labelledby="features-title">
      <div class="section-heading"><div><p class="eyebrow">你需要的，都在这里</p><h2 id="features-title">从启动游戏到约上朋友。<br><span>每一步都顺手。</span></h2></div><p class="section-intro">熟悉的启动方式，清楚的管理界面。把时间花在游戏里，而不是整理文件。</p></div>
      <div class="feature-grid">
        <article class="feature-card feature-card-large"><div class="card-topline"><span>01 / 游戏管理</span><span class="card-glyph">▧</span></div><div><h3>每个世界，都有自己的位置。</h3><p>管理多个 Minecraft 版本和游戏目录。实例、加载器和存档各自独立，切换玩法不用重新收拾。</p></div><a href="https://github.com/vdjango/buggcraft">了解游戏实例 <span>↗</span></a><div class="card-decoration version-stack" aria-hidden="true"><span>26.3</span><span>26.2 · Forge</span><span>生存整合包</span></div></article>
        <article class="feature-card"><div class="card-topline"><span>02 / 内容安装</span><span class="card-glyph">↓</span></div><h3>模组与整合包，一处管理。</h3><p>安装 Fabric、Forge、NeoForge 等加载器，也可以导入已有整合包。</p><a data-native-navigation href="/docs/help/import-modpack">整合包说明 <span>↗</span></a></article>
        <article class="feature-card"><div class="card-topline"><span>03 / 一起游玩</span><span class="card-glyph">♧</span></div><h3>朋友的房间，进来就能玩。</h3><p>添加联机节点，在大厅发现房间、发出邀请，和朋友加入同一个世界。</p><a data-native-navigation href="/docs/help/minecraft">了解连接方式 <span>↗</span></a></article>
        <article class="feature-card feature-card-wide"><div class="card-topline"><span>04 / 联机方式</span><span class="card-glyph">⌘</span></div><div><h3>P2P 优先，必要时中继。</h3><p>网络允许时尝试点对点连接；无法直连时，通过社区节点中继。</p></div><a href="https://github.com/vdjango/minecraft-tunnel-native">了解联机核心 <span>↗</span></a></article>
      </div>
    </section>
>>>>>>> origin/main

    <section class="section wrap" id="performance" aria-labelledby="performance-title"><div class="section-heading"><div><p class="eyebrow">性能，落在每一次操作里</p><h2 id="performance-title">做得更少，响应更好。</h2></div><p>C++ 是实现方式。减少等待、避免重复处理，<br>才是你能感受到的改变。</p></div><div class="performance-grid"><article><span class="metric-label">文件处理</span><h3>一遍读取，多种校验</h3><p>文件需要多个摘要时，复用同一次磁盘读取。目录扫描和解析也复用结果，减少重复 I/O。</p></article><article><span class="metric-label">界面响应</span><h3>繁重工作，放到后台</h3><p>网络、解析、哈希与文件安装使用受控后台任务。列表分页、图片限制解码尺寸，减轻界面负担。</p></article><article><span class="metric-label">原生联机</span><h3>同一个桥，连接各个入口</h3><p>启动器与 SDK 复用 C++ 桥和转发实现。指令集按 CPU 能力选择，并保留通用路径。</p></article></div><p><a class="text-link" data-native-navigation href="/docs/help/experience">这些优化，玩起来有什么不同？ <span>↗</span></a></p><div class="comparison"><h3>把原本分散的操作，放在一起</h3><div class="comparison-row"><span>手动整理文件与补依赖</span><b>→</b><strong>内容筛选、安装与任务状态集中查看</strong></div><div class="comparison-row"><span>另外找联机工具、交换连接地址</span><b>→</b><strong>在启动器里创建房间、邀请朋友</strong></div><div class="comparison-row"><span>失败后重新处理整包</span><b>→</b><strong>保留成功项，重试未完成内容</strong></div><p class="fine-print">以上比较针对操作流程。原生实现与 SIMD 优化不等于游戏 FPS 提升；连接延迟仍受线路、带宽与网络环境影响。</p></div></section>

    <section class="section community-section wrap" id="community" aria-labelledby="community-title"><div><p class="eyebrow">找队友，也找同好</p><h2 id="community-title">下一个世界，一起建。</h2><p>交流整合包、约朋友联机，或者聊聊你遇到的问题。<br>每日联机优先添加微信，联系入群。</p></div><div class="community-contacts"><div><span>微信 · 联系入群</span><strong>junsi223</strong><button type="button" @click="copyContact('微信号','junsi223')">{{copiedContact==='junsi223'?'已复制 ✓':'复制微信号'}}</button></div><div><span>QQ 群</span><strong>849362477</strong><button type="button" @click="copyContact('QQ 群号','849362477')">{{copiedContact==='849362477'?'已复制 ✓':'复制群号'}}</button></div><div><span>频道号</span><strong>pd40037206</strong><button type="button" @click="copyContact('频道号','pd40037206')">{{copiedContact==='pd40037206'?'已复制 ✓':'复制频道号'}}</button></div><p class="copy-status" role="status" aria-live="polite">{{copyStatus}}</p></div></section>

    <section class="section faq-section wrap" id="faq"><div><p class="eyebrow">开始之前</p><h2>你可能想知道。</h2></div><div class="faq-list"><details><summary>我应该下载哪个版本？</summary><p>下载页会识别你的系统，并列出实际已经发布的版本与架构。可下载情况以当前发行清单为准。</p></details><details><summary>需要自己安装 Java 吗？</summary><p>启动器会检查游戏运行环境。使用你拥有的游戏账号和合法的游戏文件；Bugg 不包含 Minecraft 游戏本体。</p></details><details><summary>普通玩家需要部署节点吗？</summary><p>通常不需要。直接在启动器使用默认统一服务登录，再创建或加入房间。自建节点面向希望贡献中继资源的用户。</p></details><details><summary>怎么反馈问题？</summary><p>请使用启动器中的「问题反馈」，填写复现步骤。反馈发送到统一服务，不依赖某一台联机节点的网页。</p></details></div></section>
    <section class="download-banner wrap"><div><p class="eyebrow">准备好你的世界</p><h2>下一次见面，在游戏里。</h2></div><a class="button" data-platform-cta href="https://download.bugg.icu/?platform=auto"><span data-platform-label>下载启动器</span> ↓</a></section>
  </main>
  <footer class="site-footer wrap"><a class="brand" href="#home"><span class="brand-mark">B</span><strong>Bugg</strong></a><nav aria-label="页脚导航"><a data-native-navigation href="/docs">玩家教程</a><a data-native-navigation href="/docs/help/developer-multiplayer">开发者接入</a><a href="https://download.bugg.icu/">下载</a><a href="https://node.bugg.icu/account">联机账号</a><a href="https://mirror.bugg.icu/">镜像源</a><a href="https://translate.bugg.icu/" target="_blank" rel="noopener">共享译文库 ↗</a><a href="https://github.com/vdjango/bugg">GitHub ↗</a></nav><p>Bugg 与 Mojang / Microsoft 无隶属关系。 <a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener">京ICP备2021040327号</a></p></footer>
</template>

