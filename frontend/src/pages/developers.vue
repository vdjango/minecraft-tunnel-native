<script setup lang="ts">
defineProps<{seed:Record<string,string>}>();
</script>

<template>
<div class="ambient" aria-hidden="true"></div>
  <header class="shell topbar site-header"><a class="brand" href="/"><span class="mark">B<span>·</span></span><span>BUGGCRAFT<small>MINECRAFT COMMUNITY</small></span></a><nav aria-label="网站导航"><a href="/network">节点网络</a><a href="/connect">连接启动器</a><a href="/developers">开发文档</a><a href="/feedback">问题反馈</a><a id="open-account" href="/account">用户中心</a></nav></header>

  <main class="shell docs-main"><div class="docs-layout">
    <aside class="docs-sidebar" aria-label="文档导航"><div class="docs-side-inner">
      <div class="docs-side-title">开发文档 <small>V1</small></div>
      <nav class="docs-toc" aria-label="章节目录">
        <a href="#overview">接入方式</a><a href="#quickstart">开始接入</a><a href="#directory-api">节点和房间</a>
        <a href="#rooms-api">连接方式</a><a href="#lobby-api">准备与成员</a><a href="#mods-api">模组同步</a><a href="#status-api">全网状态口径</a><a href="#recovery-api">故障与备用</a><a href="#friends-api">账号与好友</a><a href="#examples">语言示例</a><a href="#operations-api">节点开发与运维</a><a href="#security-api">安全与测试</a>
      </nav>
      <div class="docs-side-files"><span>协议与参考代码</span><a href="#protocol">分层协议索引</a><a href="#examples">各语言接入示例</a><a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/development.md">仓库开发指南 ↗</a></div>
    </div></aside>
    <div class="docs-content">
      <section class="docs-hero" aria-labelledby="guide-title">
        <div class="eyebrow">LAUNCHER INTEGRATION</div><h1 id="guide-title">把联机带进<br>你的启动器。</h1>
        <p>通过标准 JSON-RPC 2.0 接口添加节点、查询房间并连接游戏。启动器只需管理一个本地桥接进程，不必实现 ICE 打洞或中继协议。</p>
        <div class="docs-hero-actions"><a class="primary" href="#quickstart">开始接入 <span aria-hidden="true">↗</span></a><a class="secondary" href="#protocol">阅读完整协议</a></div>
        <div class="docs-meta"><span>HTTPS · 公开状态</span><span>JSON-RPC 2.0 · UTF-8</span><span>P2P 优先 · 中继回退</span><span>文档核对：2026-10-28 · 以运行时能力为准</span></div>
      </section>

      <section id="overview" class="docs-section">
        <div class="docs-section-heading"><span>01 / OVERVIEW</span><h2>一个桥接层，覆盖不同连接环境</h2><p>推荐通过启动器随附或安装的 Buggcraft bridge 子进程接入。进程负责节点身份、房间信令、P2P 与中继切换。</p></div>
        <div class="docs-paths">
          <article class="docs-path"><div class="docs-path-icon">STATUS</div><h3>读取公开状态</h3><p>无需账号即可显示节点在线状态、容量和公开房间。</p><a href="#directory-api">查看目录接口 →</a></article>
          <article class="docs-path"><div class="docs-path-icon">ROOMS</div><h3>加入游戏房间</h3><p>导入接入码后搜索房间。游戏连接默认尝试 P2P，失败时经节点中转。</p><a href="#rooms-api">查看连接流程 →</a></article>
          <article class="docs-path"><div class="docs-path-icon">FRIENDS</div><h3>使用账号与好友</h3><p>个人启动器接入码用于识别登录账号；匿名接入可玩联机，但没有好友功能。</p><a href="#friends-api">查看账号说明 →</a></article>
        </div>
      </section>

      <section id="quickstart" class="docs-section">
        <div class="docs-section-heading"><span>02 / QUICK START</span><h2>用 JSON-RPC 启动桥接进程</h2><p>每行发送一个 JSON-RPC 2.0 请求，每个请求对应一行响应。先初始化协议，再添加节点，之后创建或加入房间。</p></div>
        <ol class="docs-steps">
          <li><strong>下载并启动桥接程序</strong><p>从 <a href="https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml">原生构建</a> 下载对应系统的产物，解压后随启动器保留完整目录。通过子进程启动 <code>buggcraft-bridge.exe</code>（Windows）或 <code>buggcraft-bridge</code>（Linux/macOS），重定向输入输出并持续消费 stderr；不需要 Python 环境或语言专用 SDK。</p></li>
          <li><strong>初始化并导入节点</strong><p>调用 <code>bridge.initialize</code>，再用 <code>node.add</code> 导入用户从可信节点取得的接入码。桥接进程负责凭据，不要把凭据写入日志。</p></li>
          <li><strong>连接房间</strong><p>用 <code>room.list</code> 读取公开目录。邀请码房间由用户提供邀请后调用 <code>room.join</code>；允许直接加入的公开房间先调用 <code>room.resolve</code>。<code>room.host</code> 可设置公开及邀请策略，创建受保护公开房间需节点支持相应能力。</p></li>
          <li><strong>启动游戏并清理会话</strong><p>把返回的 <code>127.0.0.1:端口</code> 交给 Minecraft。退出时调用 <code>room.close</code>；进程关闭时会清理剩余会话。</p></li>
        </ol>
        <div class="docs-code"><span>启动可执行文件 · 参数不包含任何接入码</span><pre v-pre><code>buggcraft-bridge.exe
# Linux / macOS: ./buggcraft-bridge
# 构建信息: buggcraft-bridge --version</code></pre></div>
        <div class="docs-code"><span>请求示例 · node.add 与 room.join</span><pre v-pre><code>{"jsonrpc":"2.0","id":1,"method":"bridge.initialize","params":{"protocol":1}}
{"jsonrpc":"2.0","id":2,"method":"node.add","params":{"code":"buggnode1-&lt;用户接入码&gt;"}}
{"jsonrpc":"2.0","id":3,"method":"room.join","params":{"node":"&lt;返回的节点句柄&gt;","invite":"bugg1-&lt;房间邀请码&gt;","transport":"auto"}}</code></pre></div>
        <div class="docs-note"><strong>桥接接口的版本边界</strong><p>标准报文采用 JSON-RPC 2.0；方法名、参数、限制和错误码由 Buggcraft 的 v1 profile 定义。不支持 batch 或状态通知，可用 <code>session.list</code> 刷新状态。运行时保留完整发行目录，当前产物未提供受信任签名或 Apple 公证。<a href="https://github.com/vdjango/buggcraft/blob/main/docs/technical/bridge-integration.md">查看桥接发行与接入说明</a>。</p></div>
      </section>

      <section id="directory-api" class="docs-section">
        <div class="docs-section-heading"><span>03 / DIRECTORY</span><h2>查询节点与房间</h2><p>公开状态走 HTTPS。桥接接口提供有凭据的目录操作；游戏数据不经过目录查询接口。</p></div>
        <div class="docs-table-wrap"><table class="docs-table"><caption>目录能力</caption><thead><tr><th scope="col">接口</th><th scope="col">用途</th></tr></thead><tbody>
          <tr><td><code>GET /api/status</code></td><td>读取公开状态；<code>players</code> 是存活联邦桥会话总数，容量、路径和游戏人数仍属本节点，详见<a href="#status-api">指标口径</a>。</td></tr>
          <tr><td><code>GET /api/account/players</code></td><td>账号登录后读取全网在线摘要；须处理 <code>partial</code> 和 <code>unavailable_nodes</code>，不提供管理员诊断日志。</td></tr>
          <tr><td><code>node.add</code></td><td>验证并导入接入码，返回进程内使用的不透明节点句柄。</td></tr>
          <tr><td><code>node.selected / node.routes</code>（原生桥）</td><td>读取综合延迟、负载及剩余名额选出的入口和备用线路。</td></tr>
          <tr><td><code>bridge.events</code>（原生桥）</td><td>读取并消费线路切换提示，使用非弹窗提示告知用户。</td></tr>
          <tr><td><code>room.list</code></td><td>分页获取公开房间；传入 offset，最多返回 50 项。</td></tr>
          <tr><td><code>session.list</code></td><td>读取本进程活动会话及当前连接状态。</td></tr>
          <tr><td><code>node.request</code> + <code>account_lookup</code></td><td>个人身份通过桥发送目录查询；例如 <code>{"request":{"op":"account_lookup","identifier":"alex"}}</code> 并带 <code>node</code>。匿名凭据无好友查询权限。</td></tr>
        </tbody></table></div>
        <div class="docs-split"><article class="docs-info-card"><h3>限流与错误</h3><p>单行输入上限 8 KiB；错误使用 JSON-RPC 标准错误结构。超时、节点不可达或凭据失效时提示用户，并采用有上限的退避重试。</p></article><article class="docs-info-card"><h3>节点故障</h3><p>其他联邦入口可以继续提供目录。房间归属节点不可用时房主需重建房间；已经建立的 Minecraft TCP 会话不会自动迁移到另一台服务器。</p></article></div>
      </section>

      <section id="rooms-api" class="docs-section">
        <div class="docs-section-heading"><span>04 / GAME CONNECTION</span><h2>同一房间可以混合使用 P2P 和中继</h2><p>路径由每位加入者分别选择和协商。一个访客 P2P 失败或主动选中继，不会让房间里的其他访客改变路径。</p></div>
        <div class="docs-flow" aria-label="游戏连接流程"><div><span>LAUNCHER</span><strong>标准 JSON-RPC</strong><small>启动或退出会话</small></div><b aria-hidden="true">→</b><div><span>LOCAL BRIDGE</span><strong>按访客选路径</strong><small>P2P 优先 / 中继</small></div><b aria-hidden="true">→</b><div><span>MINECRAFT</span><strong>127.0.0.1:端口</strong><small>原版 TCP 接口</small></div></div>
        <div class="docs-table-wrap"><table class="docs-table"><caption>房间接口 v1</caption><thead><tr><th scope="col">方法</th><th scope="col">参数与结果</th></tr></thead><tbody>
          <tr><td><code>room.host</code></td><td><code>node</code>、<code>game_port</code>、<code>transport</code>、可选 <code>game_directory</code>；返回会话 ID、邀请码和房间状态。传入 Minecraft 实例根目录后，桥扫描其中的 <code>mods</code> 并发布模组摘要。</td></tr>
          <tr><td><code>room.join</code></td><td><code>node</code>、<code>invite</code>、<code>transport</code>；返回会话 ID、本地回环地址和连接状态。</td></tr>
          <tr><td><code>room.close</code></td><td>使用会话 ID 关闭监听、P2P 连接、中继连接与后台任务。</td></tr>
          <tr><td><code>session.list</code></td><td>返回本进程中的活动会话、角色与当前传输状态。</td></tr>
        </tbody></table></div>
        <div class="docs-split"><article class="docs-info-card"><h3>连接方式</h3><p><code>transport:auto</code>（默认）会先尝试 WebRTC ICE / DTLS / SCTP 直连，连不上时该玩家自动走 SSH 中继。<code>transport:relay</code> 会跳过 P2P，仅使用中继。</p></article><article class="docs-info-card"><h3>网络边界</h3><p>P2P 需要双方网络允许 ICE 建连；对称 NAT、防火墙、UDP 阻断等环境可能只能中继。当前节点提供 STUN 地址发现和流量中继，不提供 TURN 服务，也不能保证所有网络都可直连。</p></article></div>
        <div class="docs-note"><strong>隐私与路径选择</strong><p>P2P 建连会让通信双方获知彼此的网络候选地址。若用户不愿向房间对端提供自己的网络地址，选“仅通过节点中转”。中继能隐藏玩家之间的直接地址，但中继节点仍会看到连接端点和转发流量。</p></div>
      </section>

      <section id="lobby-api" class="docs-section">
        <h2>准备房间与成员管理</h2>
        <p>公开准备房间尚未开放游戏世界时，仍会出现在本节点和跨节点目录，状态为 <code>waiting</code>。访客可先凭邀请码加入并安装；设置 <code>require_invite</code> 的房间不会公开入房密钥，私密房间不进入公开列表。</p>
        <p>节点通过 <code>ping</code> 和 <code>describe</code> 的 <code>capabilities</code> 声明 <code>room-lobby-v1</code>。创建时传 <code>game_port:0</code>：玩家可以先加入、聊天和安装；世界开放后由启动器发现，再调用 <code>room.retarget</code>。房间管理者和世界提供者分别标识，转让管理不会搬迁存档。</p>
        <div class="docs-code"><span>使用已有会话读取真实成员快照</span><pre v-pre><code>{"jsonrpc":"2.0","id":30,"method":"room.request","params":{"session":"&lt;session&gt;","request":{"op":"members","offset":0,"limit":32}}}</code></pre></div>
        <div class="docs-table-wrap"><table class="docs-table"><thead><tr><th>request.op</th><th>字段与权限</th></tr></thead><tbody>
          <tr><td><code>members</code></td><td>返回 revision、self_id、manager_id、provider_id、config_id、members、next、online、ready、transfer、logs。每页最多 32 人，继续传 next 为 offset；20 秒无心跳标记 stale。</td></tr>
          <tr><td><code>member_update</code></td><td>发送自己的 config_id、task、递增 seq、name、state、stage、done、total、speed。状态 idle/checking/downloading/installing/verifying/ready/failed/cancelled/starting/playing；阶段 game/java/libraries/assets/loader/mods/resources/configuration/verify/waiting。speed 为字节/秒。旧配置被拒绝，倒序与旧任务不会覆盖新进度。</td></tr>
          <tr><td><code>member_path</code></td><td>自己的 path（p2p/relay/unknown/local）、rtt_ms、rtt_kind（game/node/unknown/local）；节点延迟不能标成游戏延迟。members 请求可合并这些字段。</td></tr>
          <tr><td><code>member_kick / member_unblock</code></td><td>仅管理者，指定 member。移出会关闭目标游戏及传输连接；同房间身份和账号受到重入限制，解除后恢复。匿名玩家重新创建身份的识别能力有限。</td></tr>
          <tr><td><code>owner_offer / owner_answer / owner_cancel</code></td><td>管理者向在线 member 发出转让请求；目标在 30 秒内用 offer ID、显式 accept 接受或拒绝。取消、过期、断线保持原角色；接受后旧管理权失效，世界提供者保持原样。</td></tr>
        </tbody></table></div>
        <div class="docs-note"><strong>安装日志必须单独授权</strong><p>管理者先发送 <code>logs_request(member)</code>。玩家快照中收到 <code>logs.request</code>，90 秒内由本人用 <code>logs_answer(request,allow)</code> 明确允许或拒绝。允许后玩家通过 <code>logs_append(entries)</code> 上传自己的安装日志，每批最多 10 行，行字段为 seq、text、category、level；当前管理者用 <code>logs_read(member,offset)</code> 读取，每批最多 16 行。category 为 game/java/assets/libraries/loader/mods/resources/configuration/verify，level 为 info/warning/error。</p><p>客户端发送前和节点接收后都过滤凭据、下载地址及本地路径。节点仅在内存保留最近 200 行。玩家通过 <code>logs_revoke</code> 立即停止共享并清空缓存；转让房主、断线、退出或踢出均使相关授权失效，其他成员不能读取。已被对方自行保存的副本无法远程删除。</p></div>
        <p>桥使用私有恢复凭据维持当前会话身份，客户端轮询 session.events / session.list 取得成员更新，正常快照约每 4 秒刷新；准备进度建议合并后上报。收到成员变更事件仅作为刷新提示，不能从公开成员 ID 或邀请码伪造目标身份。</p>
      </section>

      <section id="mods-api" class="docs-section">
        <div class="docs-note"><strong>启动器下载顺序</strong><p>优先使用启动器设置的模组与整合包下载源，校验下载内容后安装。只有来源不可用并且该访客已建立 P2P 游戏直连时，才向房主请求文件；只有中继或协商中状态时禁止文件传输。文件通道独立协商，P2P 丢失时停止传输，文件数据不回退 SSH。游戏与加载器下载源设置单独生效。</p></div>
        <div class="docs-note"><strong>整合包与房间共用内容约定</strong><p>必须核对 Minecraft 版本、加载器类型和模组；加载器构建版本不同时提示差异。资源包、光影是可选外观内容，缺少或下载失败不会阻止联机。桥还扫描 <code>resourcepacks</code> 和 <code>shaderpacks</code>，发布 <code>resources_count / resources_hash / resources</code>；当前桥最多发送 8 项预览，完整清单最多 2000 项。旧节点缺少这些字段时视为不支持可选内容。</p><p><code>mods.manifest</code> 和 <code>mods.fetch</code> 增加 <code>kind:"resources"</code>，默认 <code>kind:"mods"</code> 保持兼容。资源条目使用 <code>name,path,size,sha256,sha1</code>，其中 <code>path</code> 只能是 <code>resourcepacks/文件名</code> 或 <code>shaderpacks/文件名</code>；分页、并发、校验及取消规则相同。文件传输仍需房主允许，不能以可选内容绕过授权。</p><p>Buggcraft 导出使用 <code>.buggpack</code>：<code>buggcraft.pack.json</code> 中包含版本、加载器及 <code>path,size,sha256,sha1,embedded,downloads</code> 文件记录。精简模式复用已校验来源，未知或修改的文件内置；常规模式内置实例内容。游戏核心与加载器在导入时安装，常规模式不代表完整离线安装器。存档和配置属于整合包内容，房间同步只处理模组及可选外观文件。</p></div>
        <div class="docs-section-heading"><span>05 / MODS &amp; GAME PROFILE</span><h2>房间可以核对 Minecraft 版本与模组</h2><p>房主可发布版本信息和 JAR 清单，访客可查看并选择下载。桥负责校验与传输，是否安装、启用或重启游戏由启动器决定。</p></div>
        <div class="docs-split"><article class="docs-info-card"><h3>房间信息</h3><p>房主的 <code>game_directory</code> 指向 Minecraft 实例根目录；桥扫描其 <code>mods</code> 子目录，并把模组数量、清单 SHA-256 和最多 24 个 JAR 预览写入房间信息。Minecraft 版本、协议号、加载器和加载器版本仍由启动器通过 <code>settings.game</code> 或 <code>room.retarget</code> 提供。</p></article><article class="docs-info-card"><h3>完整模组清单</h3><p>如果创建房间时没传 <code>game_directory</code>，房主可在 <code>game_port:0</code> 等待阶段调用 <code>mods.configure</code> 并传入实例根目录。桥扫描最多 1000 个 JAR；访客调用 <code>mods.manifest</code>，每页最多 50 项。</p></article></div>
        <div class="docs-table-wrap"><table class="docs-table"><caption>模组传输接口</caption><thead><tr><th scope="col">方法</th><th scope="col">用途</th></tr></thead><tbody>
          <tr><td><code>mods.configure</code></td><td>房主等待游戏启动时配置 Minecraft 实例根目录；桥读取其中的 <code>mods</code> 子目录并更新房间摘要。游戏端口启用后不能更改。</td></tr>
          <tr><td><code>mods.manifest</code></td><td>读取目录。房主本地分页返回；访客先得到 transfer ID，再用 <code>mods.status</code> 轮询并分页读取。</td></tr>
          <tr><td><code>mods.fetch</code></td><td>访客提交清单项及本机目标路径下载 JAR；单文件上限 2 GiB，最多 4 个并行传输。收到文件后桥再次核对 SHA-256，再写入目标目录。</td></tr>
          <tr><td><code>mods.status</code> / <code>mods.cancel</code></td><td>查询传输进度或取消传输。</td></tr>
        </tbody></table></div>
        <div class="docs-note"><strong>不会自动修改游戏</strong><p>模组清单会自动同步给节点目录，用于显示和比对；版本号、协议号与加载器信息由启动器提供。桥不会替用户安装模组、覆盖已有文件、切换加载器或自动重启 Minecraft；启动器应先展示差异并取得用户操作，再决定下载目标目录。</p></div>
        <div class="docs-table-wrap"><table class="docs-table"><caption>game 字段与缺失值</caption><thead><tr><th scope="col">字段</th><th scope="col">来源与含义</th></tr></thead><tbody>
          <tr><td><code>version / protocol</code></td><td>启动器提供版本和整数协议号；空版本或 <code>null</code> 协议表示未知。</td></tr>
          <tr><td><code>loader / loader_version</code></td><td>启动器提供加载器名称与版本，桥不通过 JAR 自动识别。</td></tr>
          <tr><td><code>mods_count / mods_hash</code></td><td>桥扫描完整清单的数量与摘要；未扫描或空清单时哈希可能缺失。</td></tr>
          <tr><td><code>mods</code></td><td>最多 24 项 <code>name,size,sha256</code> 预览。本节点 <code>room.list</code> 会省略预览，联邦公开目录可以包含；完整清单另取。</td></tr>
        </tbody></table></div>
        <div class="docs-code"><span>房主先等待并发布环境，再启用本机世界；占位符需替换</span><pre v-pre><code>{"jsonrpc":"2.0","id":10,"method":"room.host","params":{"node":"&lt;node&gt;","game_port":0,"game_directory":"&lt;实例根目录&gt;","settings":{"public":true,"require_invite":true,"game":{"version":"&lt;Minecraft版本&gt;","loader":"fabric","loader_version":"&lt;加载器版本&gt;"}}}}
{"jsonrpc":"2.0","id":11,"method":"room.retarget","params":{"session":"&lt;房主session&gt;","port":25565,"game":{"version":"&lt;Minecraft版本&gt;","loader":"fabric","loader_version":"&lt;加载器版本&gt;"}}}</code></pre></div>
        <div class="docs-code"><span>访客加入桥会话后取完整清单，再按用户决定下载</span><pre v-pre><code>{"jsonrpc":"2.0","id":20,"method":"mods.manifest","params":{"session":"&lt;访客session&gt;"}}
{"jsonrpc":"2.0","id":21,"method":"mods.status","params":{"session":"&lt;访客session&gt;","transfer":"&lt;transfer&gt;","offset":0}}
{"jsonrpc":"2.0","id":22,"method":"mods.fetch","params":{"session":"&lt;访客session&gt;","item":{"name":"example.jar","size":1234,"sha256":"&lt;清单中的SHA-256&gt;"},"target":"&lt;实例&gt;/mods/example.jar"}}
{"jsonrpc":"2.0","id":23,"method":"mods.cancel","params":{"session":"&lt;访客session&gt;","transfer":"&lt;已完成的transfer&gt;"}}</code></pre></div>
        <p>访客异步任务完成后读取 <code>items,offset,total</code>，以 <code>offset + items.length</code> 请求下一页，直到到达 <code>total</code>；每页最多 50 项。最多 4 个活动传输、16 条保留任务。私密房间跨节点不公开环境与标题；缺少环境字段不代表原版或完全匹配。哈希相同不证明模组安全。完整约束见<a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/protocol/game-metadata.md">游戏环境与模组同步</a>。</p>
      </section>

      <section id="status-api" class="docs-section">
        <div class="docs-section-heading"><span>NETWORK STATUS</span><h2>全网在线与本机负载分开读取</h2><p>同一响应中存在不同范围的指标。在线总数是桥会话数，包含房主、访客及私密房间，不是去重账号数。</p></div>
        <div class="docs-table-wrap"><table class="docs-table"><caption>公开状态字段</caption><thead><tr><th scope="col">字段</th><th scope="col">范围与时效</th></tr></thead><tbody>
          <tr><td><code>players / nodes[].players</code></td><td>有效联邦公告的聚合/单节点会话人数，远端公告有效期 30 秒。旧公告无统计字段时仅按公开房间回退。</td></tr>
          <tr><td><code>rooms / connections / streams / capacity</code></td><td>本节点房间、连接、游戏流及容量；不能与全网人数相减计算剩余名额。</td></tr>
          <tr><td><code>p2p_players / relay_players</code></td><td>本节点访客路径上报，未确认路径由 <code>unconfirmed_players</code> 表示。</td></tr>
          <tr><td><code>game_players</code></td><td>本节点可用世界最近 15 秒内的游戏人数上报之和；用 <code>game_counts_unknown</code> 判断缺失覆盖。</td></tr>
        </tbody></table></div>
        <p><code>GET /api/account/players</code> 需账号 Cookie；返回 <code>players,partial,unavailable_nodes,total</code>。最多并发 3 个成员，单节点 4 秒、整批 5 秒预算，缓存 5 秒。节点不可达或旧版不支持时明确提示部分可用；<code>total</code> 来自公告，不必等于详情数组长度。</p>
        <p>详情含昵称、角色、脱敏房间名、节点 ID/名称、路径与 RTT；超过 20 秒未上报时路径为 <code>unknown</code>、RTT 为 <code>null</code>。时间戳为 Unix 秒，RTT 为毫秒。全网详情不开放原始地址或诊断日志。</p>
      </section>

      <section id="recovery-api" class="docs-section">
        <div class="docs-section-heading"><span>FAILURE RECOVERY</span><h2>备用节点有上限，游戏连接需要重新加入</h2><p>房主桥选路决定新房间承载节点，联邦传播签名归属；访客先定位，再连接该节点，没有全网选主投票。</p></div>
        <div class="docs-flow" aria-label="节点恢复流程"><div><span>DISCOVERY</span><strong>预先缓存可信入口</strong><small>固定公钥与有效凭据</small></div><b aria-hidden="true">→</b><div><span>RECONNECT</span><strong>失联后清理并重建</strong><small>创建时的候选，有界退避</small></div><b aria-hidden="true">→</b><div><span>STOP</span><strong>全部失效明确停止</strong><small>重新探测或更新接入码</small></div></div>
        <p>Buggcraft 启动器持久缓存最多 32 个入口，刷新失败或空拓扑保留最近有效结果；其他启动器须自行实现。桥导入最多 5 个备用配置，活动会话不会自动订阅后续新增候选。</p>
        <p>首次创建失败会返回错误，不留下永久重试任务；活动会话 SSH 失联后进入 <code>reconnecting</code>，连续失败最多延迟重试 6 次，间隔 1/2/4/8/10/10 秒，连接耗时另计。最终 <code>stopped</code>、原因 <code>backup-unavailable</code>；成功重建重置次数。</p>
        <div class="docs-note"><strong>不能承诺无缝恢复</strong><p>仅 P2P 失效时，新游戏连接可经健康节点中继；SSH 故障会清理该会话的游戏流与 P2P。没有 TCP 状态复制，房主/访客独立尝试也可能落到不同节点。不要关闭公钥验证来恢复连接；所有备用不可用时提示可信来源更新接入码。详见<a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/launcher-federation-flow.md">联机与联邦流程图</a>及<a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/protocol/compatibility.md">兼容文档</a>。</p></div>
      </section>

      <section id="friends-api" class="docs-section">
        <div class="docs-section-heading"><span>06 / ACCOUNTS &amp; FRIENDS</span><h2>登录账号与匿名联机分开</h2><p>匿名接入可以添加节点和加入房间，但不显示好友入口。好友需要账号身份、对方同意以及可达的联邦目录。</p></div>
        <div class="docs-flow" aria-label="个人身份接入步骤"><div><span>01</span><strong>节点网页登录</strong><small>注册或登录</small></div><b aria-hidden="true">→</b><div><span>02</span><strong>领取个人启动器接入码</strong><small>主动导入启动器</small></div><b aria-hidden="true">→</b><div><span>03</span><strong>搜索并添加好友</strong><small>双方确认</small></div></div>
        <div class="docs-split"><article class="docs-info-card"><h3>跨节点用户名搜索</h3><p>联邦节点同步公开用户名、账号 ID 和公钥。用户名目录不包含密码；查询结果不代表已经获得好友或房间权限。</p></article><article class="docs-info-card"><h3>登录与备份</h3><p>没有额外持久备份时，其他入口仍可路由到在线注册节点登录；注册节点不可用时需恢复文件。用户授权的加密副本可提供额外恢复来源，公开用户名目录本身不能恢复账号。</p></article></div>
        <div class="docs-code"><span>个人身份通过本机桥查询好友目录；account_lookup 不是独立 JSON-RPC 方法</span><pre v-pre><code>{"jsonrpc":"2.0","id":30,"method":"node.request","params":{"node":"&lt;node&gt;","request":{"op":"account_lookup","identifier":"alex"}}}</code></pre></div>
        <div class="docs-note"><strong>个人启动器接入码是授权凭据</strong><p>新版接入码只包含签发节点的 HTTPS 地址和随机兑换引用，不再把节点连接配置直接编码进字符串；桥通过 HTTPS 兑换，最长有效 7 天。旧版接入码仍可导入。兑换后桥仍需取得节点连接地址，因此这不是端到端隐藏服务器地址的加密方案。持有该码的设备可代表账号操作；使用系统安全存储保护接入码，不要写入日志或公开配置文件。</p></div>
      </section>

      <section id="examples" class="docs-section">
        <div class="docs-section-heading"><span>07 / CODE EXAMPLES</span><h2>各语言复用同一个接口</h2><p>下列示例按语言分开。Java、C#、Python 展示本机子进程管道；C 和 C++ 展示官方 C ABI。接入码、邀请码和游戏启动函数由你的启动器提供，协议方法与返回结构保持一致。</p></div>
        <div class="docs-samples">
          <details class="docs-sample" open><summary><span>Python</span><small>subprocess + JSON</small></summary><pre v-pre><code>import json, subprocess
p = subprocess.Popen([bridge_path], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, text=True, encoding="utf-8")
def rpc(i, method, params):
    p.stdin.write(json.dumps({"jsonrpc":"2.0", "id":i,
        "method":method, "params":params}) + "\n")
    p.stdin.flush(); reply = json.loads(p.stdout.readline())
    if "error" in reply: raise RuntimeError(reply["error"]["message"])
    return reply["result"]
rpc(1, "bridge.initialize", {"protocol":1})
node = rpc(2, "node.add", {"code": access_code})["node"]
room = rpc(3, "room.join", {"node":node, "invite":invite, "transport":"auto"})
launch_minecraft(room["address"])
rpc(4, "room.close", {"session":room["session"]})</code></pre></details>
          <details class="docs-sample"><summary><span>C</span><small>bridge.h · C ABI</small></summary><pre v-pre><code>#include &lt;buggcraft/bridge.h&gt;
#include &lt;stdio.h&gt;
#include &lt;string.h&gt;

int main(void) {
  buggcraft_bridge *bridge = NULL;
  const char request[] = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"bridge.initialize\",\"params\":{\"protocol\":1}}";
  char *response = NULL; size_t size = 0;
  if (buggcraft_bridge_abi_version() != 1 || buggcraft_bridge_create(&amp;bridge)) return 1;
  int status = buggcraft_bridge_call(bridge, request, strlen(request), &amp;response, &amp;size);
  if (!status &amp;&amp; response) fwrite(response, 1, size, stdout);
  buggcraft_bridge_free(response); buggcraft_bridge_destroy(bridge);
  return status != 0;
}</code></pre></details>
          <details class="docs-sample"><summary><span>C++</span><small>bridge.h + nlohmann/json</small></summary><pre v-pre><code>#include &lt;buggcraft/bridge.h&gt;
#include &lt;nlohmann/json.hpp&gt;
#include &lt;iostream&gt;
#include &lt;stdexcept&gt;
#include &lt;string&gt;

using json = nlohmann::json;
int main() {
  buggcraft_bridge *bridge = nullptr;
  if (buggcraft_bridge_abi_version() != 1 || buggcraft_bridge_create(&amp;bridge))
    throw std::runtime_error("bridge initialization failed");
  const auto request = json{{"jsonrpc","2.0"},{"id",1},
    {"method","bridge.initialize"},{"params",{{"protocol",1}}}}.dump();
  char *response = nullptr; size_t size = 0;
  int status = buggcraft_bridge_call(bridge, request.data(), request.size(), &amp;response, &amp;size);
  if (!status &amp;&amp; response) std::cout.write(response, static_cast&lt;std::streamsize&gt;(size));
  buggcraft_bridge_free(response); buggcraft_bridge_destroy(bridge);
  return status != 0;
}</code></pre></details>
          <details class="docs-sample"><summary><span>Java</span><small>ProcessBuilder + Jackson</small></summary><pre v-pre><code>var process = new ProcessBuilder(bridgePath).start();
var in = new java.io.BufferedWriter(new java.io.OutputStreamWriter(process.getOutputStream(), java.nio.charset.StandardCharsets.UTF_8));
var out = new java.io.BufferedReader(new java.io.InputStreamReader(process.getInputStream(), java.nio.charset.StandardCharsets.UTF_8));
var json = new com.fasterxml.jackson.databind.ObjectMapper();
var sequence = new java.util.concurrent.atomic.AtomicInteger();
java.util.function.BiFunction&lt;String, java.util.Map&lt;String,Object&gt;, com.fasterxml.jackson.databind.JsonNode&gt; rpc = (method, params) -&gt; {
  try {
    var q=java.util.Map.of("jsonrpc","2.0", "id",sequence.incrementAndGet(), "method",method, "params",params);
    in.write(json.writeValueAsString(q)); in.newLine(); in.flush();
    var reply=json.readTree(out.readLine());
    if (reply.has("error")) throw new RuntimeException(reply.get("error").toString());
    return reply.get("result");
  } catch (java.io.IOException e) { throw new RuntimeException(e); }
};
rpc.apply("bridge.initialize", java.util.Map.of("protocol",1));
var node=rpc.apply("node.add", java.util.Map.of("code",accessCode)).get("node").asText();
var room=rpc.apply("room.join", java.util.Map.of("node",node, "invite",invite, "transport","auto"));
launchMinecraft(room.get("address").asText());
rpc.apply("room.close", java.util.Map.of("session",room.get("session").asText()));</code></pre></details>
          <details class="docs-sample"><summary><span>C#</span><small>Process + System.Text.Json</small></summary><pre v-pre><code>var start = new System.Diagnostics.ProcessStartInfo(bridgePath) {
    RedirectStandardInput=true, RedirectStandardOutput=true, UseShellExecute=false, CreateNoWindow=true };
using var bridge = System.Diagnostics.Process.Start(start)!;
async System.Threading.Tasks.Task&lt;System.Text.Json.JsonElement&gt; Rpc(int id, string method, object parameters) {
  var q=System.Text.Json.JsonSerializer.Serialize(new { jsonrpc="2.0", id, method, @params=parameters });
  await bridge.StandardInput.WriteLineAsync(q); await bridge.StandardInput.FlushAsync();
  using var doc=System.Text.Json.JsonDocument.Parse((await bridge.StandardOutput.ReadLineAsync())!);
  var root=doc.RootElement; if (root.TryGetProperty("error",out var error)) throw new Exception(error.ToString());
  return root.GetProperty("result").Clone();
}
await Rpc(1,"bridge.initialize",new { protocol=1 });
var node=(await Rpc(2,"node.add",new { code=accessCode })).GetProperty("node").GetString();
var room=await Rpc(3,"room.join",new { node, invite, transport="auto" });
LaunchMinecraft(room.GetProperty("address").GetString());
await Rpc(4,"room.close",new { session=room.GetProperty("session").GetString() });</code></pre></details>
        </div>
        <div class="docs-callout"><strong>测试请使用自己部署的节点</strong><p>连接能否直达取决于两端网络、路由器、防火墙及 UDP 状态。请分别测试 P2P 成功、UDP 被阻断后中继、手动仅中继、断线清理和多名玩家混合路径。</p><a href="#protocol">阅读协议细节 →</a></div>
      </section>

      <section id="operations-api" class="docs-section">
        <div class="docs-section-heading"><span>NODE DEVELOPMENT</span><h2>节点开发、代理与诊断</h2><p>服务入口为 <code>web/main.py</code>；<code>web/tunnel/</code> 负责账号、目录与联邦，<code>core/</code> 处理 SSH/STUN 和转发，<code>bridge/</code> 提供玩家本机连接组件。</p></div>
        <div class="docs-split"><article class="docs-info-card"><h3>Cloudflare 网段自动更新</h3><p>启动后后台读取官方 <code>/ips-v4/</code> 与 <code>/ips-v6/</code> 纯文本列表，每 24 小时刷新，失败保留缓存。手工 CIDR 只填额外代理；首次尚无缓存且抓取失败时，不能假定已信任 Cloudflare。</p></article><article class="docs-info-card"><h3>后台权限</h3><p>公共导航不提供管理入口，管理员会话与账号会话独立，写操作仍校验 CSRF/Origin。隐藏路径不是授权；后台与会话日志不能通过玩家凭据访问。</p></article></div>
        <p>仅 TCP 对端位于可信代理网段时读取 <code>X-Forwarded-Proto</code> 和 <code>CF-Connecting-IP</code>；代理须覆盖客户端伪造头，不能信任默认路由。管理员可查看 <code>cloudflare_proxy_cidrs</code> 和最近成功时间 <code>cloudflare_proxy_updated</code>。</p>
        <ol class="docs-steps">
          <li><strong>先确认房间归属</strong><p>全网在线列表跨节点，诊断日志只属于当前节点；在实际承载房间的节点登录诊断。</p></li>
          <li><strong>按阶段排查</strong><p>依次核对认证会话、世界 ready/waiting、本机回环监听、游戏流及握手拒绝；网页能打开不等于桥已认证。</p></li>
          <li><strong>合并桥与节点证据</strong><p>查看桥事件/stderr、P2P 回退、SSH 重连、EOF、背压和诊断丢弃/写盘错误。P2P 不经过节点，节点转发量为零可正常；没有上报的 RTT 不能显示成 0 ms。</p></li>
        </ol>
        <p>模块职责与回归命令见<a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/development.md">节点开发指南</a>；代理和实际 systemd 权限见<a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/deployment.md">部署指南</a>。安装包模板变化不会自动改写既有自定义 unit。</p>
      </section>

      <section id="security-api" class="docs-section">
        <div class="docs-section-heading"><span>08 / RELEASE CHECK</span><h2>上线前逐项验证</h2><p>请在自己部署的节点上完成端到端测试，并明确告诉用户连接状态和路径。</p></div>
        <ul class="docs-checklist">
          <li>验证 SSH 节点公钥和 HTTPS 证书；身份不匹配时中止连接。</li>
          <li>接入码放入操作系统安全存储；日志隐藏 token、邀请码和签名私钥。</li>
          <li>本地游戏地址仅监听回环接口；房间退出后释放端口与后台任务。</li>
          <li>测试 P2P 自动连接、P2P 失败回退、仅中继和同房间混合连接。</li>
          <li>测试容量上限、邀请码错误、节点失联、登录过期、匿名用户权限和好友双方确认。</li>
          <li>说明中继不会使房间会话自动迁移；节点恢复或更换后需重新加入。</li>
          <li>测试全部备用失效后的停止原因和重新探测入口；不以无限重试掩盖失败。</li>
        </ul>
        <div class="docs-note"><strong>握手过滤和官方桥识别的限制</strong><p>房主原生桥在写入本机游戏服务前检查首个 Minecraft Handshake，原生桥的未完成握手期限为 10 秒；合法握手后允许后续游戏数据。这不持续验证全部游戏包，也不能证明官方二进制。持有效凭据且实现相同协议的第三方仍可通信；SHA-256 校验也不是代码签名。边界见<a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/security.md">安全说明</a>。</p></div>
      </section>
      <section id="protocol" class="docs-section">
        <div class="docs-section-heading"><span>09 / PROTOCOL MAP</span><h2>协议按层拆开，各自解决一件事</h2><p>启动器只需接入本机桥。目录、联邦和游戏传输由原生组件处理；需要自行实现节点服务或底层客户端时，再阅读相应协议。</p></div>
        <div class="docs-protocol-lead"><strong>推荐路径</strong><span>JSON-RPC 2.0 → 本机原生桥 → 节点控制与游戏连接</span><a href="#examples">按语言查看示例 ↓</a></div>
        <div class="docs-protocol-stack">
          <article><span>01 · 启动器接口</span><h3>JSON-RPC 2.0 over stdio</h3><p>一行一个 UTF-8 请求，桥返回一行响应。也可通过 C ABI 调用同样的方法。</p><a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/protocol/launcher-bridge.md">启动器桥接规范 ↗</a></article>
          <article><span>02 · 节点目录</span><h3>SSH + JSON Lines</h3><p>节点目录、房间查询和权限校验由桥调用；启动器无需直连或拼接内部请求。</p><a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/protocol/directory.md">房间目录规范 ↗</a></article>
          <article><span>03 · 节点联邦</span><h3>签名公告与存活同步</h3><p>节点用签名公告交换成员和目录状态。签名证明来源，不代表成员内容可信。</p><a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/protocol/federation.md">联邦同步规范 ↗</a></article>
          <article><span>04 · 游戏数据</span><h3>WebRTC 或 SSH 中继</h3><p>直连使用 DTLS/SCTP；自动模式失败后走 SSH 中继。既有游戏连接不会透明迁移。</p><a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/protocol/transports.md">游戏传输协议 ↗</a></article>
        </div>
        <div class="docs-protocol-reference">
          <div class="docs-section-heading"><span>JSON-RPC PROFILE V1</span><h3>消息怎么收发</h3><p>协议版本字段为 <code>"2.0"</code>，桥配置协议为 <code>1</code>；不支持 batch。stdout 只承载响应，诊断信息写 stderr。</p></div>
          <div class="docs-protocol-exchange">
            <div class="docs-code"><span>请求</span><pre v-pre><code>{"jsonrpc":"2.0","id":1,"method":"bridge.initialize","params":{"protocol":1}}</code></pre></div>
            <div class="docs-code"><span>成功响应</span><pre v-pre><code>{"jsonrpc":"2.0","id":1,"result":{...}}</code></pre></div>
            <div class="docs-code"><span>错误响应</span><pre v-pre><code>{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"..."}}</code></pre></div>
          </div>
          <div class="docs-split">
            <article class="docs-info-card"><h3>先读取运行时能力</h3><p><code>bridge.initialize</code> 返回协议版本、可用方法、传输能力和资源上限。保存进程内 <code>node</code> 与 <code>session</code> 句柄；重启桥后重新创建。</p></article>
            <article class="docs-info-card"><h3>请求上限与关闭</h3><p>单个请求最多 8 KiB。关闭标准输入会清理会话并结束进程；使用动态库时，每个 response 都必须由 <code>buggcraft_bridge_free</code> 释放。</p></article>
          </div>
          <div class="docs-table-wrap"><table class="docs-table"><caption>常见错误码</caption><thead><tr><th scope="col">错误码</th><th scope="col">含义</th><th scope="col">客户端处理</th></tr></thead><tbody>
            <tr><td><code>-32700 / -32600</code></td><td>JSON 无法解析 / 请求格式错误</td><td>修正报文，不自动重试。</td></tr>
            <tr><td><code>-32601 / -32602</code></td><td>方法不存在 / 参数无效</td><td>检查 `initialize.methods` 和参数类型。</td></tr>
            <tr><td><code>-32000</code></td><td>节点连接失败</td><td>提示用户并允许重试或更换节点。</td></tr>
            <tr><td><code>-32004 / -32009</code></td><td>句柄不存在 / 活动会话阻止删除</td><td>重建句柄，或先关闭会话。</td></tr>
            <tr><td><code>-32010 / -32020 / -32021</code></td><td>资源上限 / 房间启动失败 / 房间启动超时</td><td>释放资源，显示具体失败状态。</td></tr>
            <tr><td><code>-32022</code></td><td>没有可接纳新房间的节点容量</td><td>更新路由或稍后重试，不能当成凭据错误。</td></tr>
          </tbody></table></div>
        </div>
        <div class="docs-protocol-links"><a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/PROTOCOL_STANDARD.md">打开协议总索引 ↗</a><a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/protocol/README.md">浏览分层文档 ↗</a><a href="https://github.com/vdjango/minecraft-tunnel-native/blob/main/docs/protocol/examples.md">语言选择与接入约定 ↗</a></div>
      </section>
      <footer class="docs-footer"><span>BUGGCRAFT / LAUNCHER GUIDE</span><a href="#guide-title">回到开头 ↑</a></footer>
    </div>
  </div></main>
</template>
