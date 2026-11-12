<script setup lang="ts">
import NodeAdminNav from '../NodeAdminNav.vue';
import NodeHeader from '../NodeHeader.vue';
import {onBeforeUnmount,onMounted,ref} from 'vue';
defineProps<{seed:Record<string,string>}>();
const adminAuthenticated=ref(false);
const onAuth=(event:Event)=>{adminAuthenticated.value=Boolean((event as CustomEvent<boolean>).detail);};
onMounted(()=>document.addEventListener('buggcraft:admin-auth',onAuth));
onBeforeUnmount(()=>document.removeEventListener('buggcraft:admin-auth',onAuth));
</script>

<template>
<div class="ambient"></div>
  <NodeHeader v-show="adminAuthenticated" admin />

  <main class="shell console-main">
    <div v-show="adminAuthenticated" class="console-heading"><div class="eyebrow">YOUR NODE, YOUR DECISIONS</div><h1>管理你的节点。</h1><p id="console-subtitle"></p><button id="refresh-admin" class="secondary" type="button">刷新列表</button></div>
    <p class="feedback console-feedback" id="admin-feedback" role="status" aria-live="polite"></p>
    <form id="login-form" class="console-card login-card">
      <span class="eyebrow muted">BUGGCRAFT · ADMINISTRATOR</span><h1>登录</h1><p>登录 Buggcraft 节点管理后台</p>
      <label>用户名<input id="username" autocomplete="username" value="admin" maxlength="64" required></label>
      <label>管理员密码<input id="admin-password" type="password" autocomplete="current-password" maxlength="256" required></label>
      <button class="primary wide" type="submit">登录</button>
    </form>
    <div id="admin-panel" hidden>
      <NodeAdminNav />

      <div class="bandwidth-panel admin-live" id="admin-bandwidth"><div class="section-heading"><h3>服务器实时带宽</h3><div class="bandwidth-units"><button type="button" data-bandwidth-unit="MB/s">MB/s</button><button type="button" data-bandwidth-unit="Mbps">Mbps</button></div></div><div class="bandwidth-rates"><article><span class="bandwidth-direction">接收</span><div class="bandwidth-reading"><strong id="bandwidth-rx">—</strong><span class="bandwidth-unit">MB/s</span></div></article><article><span class="bandwidth-direction">发送</span><div class="bandwidth-reading"><strong id="bandwidth-tx">—</strong><span class="bandwidth-unit">MB/s</span></div></article></div><small id="bandwidth-status">正在采样…</small></div>
      <section class="console-card" id="mail"><span class="eyebrow muted">EMAIL DELIVERY</span><h2>邮件服务</h2><p>用于注册验证和通知。</p>
        <form id="mail-form"><div class="capacity-fields">
          <label>SMTP 服务器<input id="mail-host" autocomplete="off" value="smtp.163.com" required></label>
          <label>加密连接<select id="mail-port"><option value="465">SSL · 465</option><option value="587">STARTTLS · 587</option></select></label>
          <label>发件邮箱<input id="mail-username" type="email" autocomplete="off" required></label>
          <label>SMTP 授权码<input id="mail-password" type="password" autocomplete="new-password" placeholder="留空保留现有授权码"></label>
          <label>发件人名称<input id="mail-from-name" maxlength="40" value="Buggcraft 节点" required></label>
        </div><label class="check"><input id="mail-required" type="checkbox" checked>注册时必须验证邮箱</label><button class="primary" type="submit">保存邮件设置</button></form>
        <div class="divider"></div><h3>测试发信</h3><form id="mail-test-form"><label>收件邮箱<input id="mail-recipient" type="email" required></label><button class="secondary" type="submit">发送测试邮件</button></form><p class="feedback" id="mail-feedback" role="status"></p>
      </section>
      <section class="console-card" id="nodes">
        <div class="section-heading"><div><span class="eyebrow muted">NETWORK MEMBERS</span><h2>节点列表</h2></div><span class="badge" id="peer-count">同步中</span></div>

        <div id="admin-peers" class="admin-node-list"></div>
        <form id="node-action-form" class="action-panel" hidden>
          <span class="eyebrow">REVIEW LOCAL DECISION</span><h3 id="action-title"></h3>
          <p id="action-description"></p><code id="action-node-id"></code>
          <label>操作原因（8–300 字，不要填写密码等隐私）<textarea id="action-reason" rows="2" minlength="8" maxlength="300" required></textarea></label>
          <label>输入上方完整节点 ID 确认<input id="action-confirmation" maxlength="32" autocomplete="off" required></label>
          <label>再次验证本节点管理员密码<input id="action-password" type="password" autocomplete="current-password" maxlength="256" required></label>
          <div class="action-buttons"><button class="primary" type="submit" id="action-submit">确认本地操作</button><button class="secondary" type="button" id="cancel-action">取消</button></div>
          <p id="action-feedback" class="feedback" role="status"></p>
        </form>
      </section>
      <section class="console-card" id="network-settings"><span class="eyebrow muted">TRUSTED CONNECTIONS</span><h2>连接其他节点</h2>
        <p>已有节点的管理员私下发出邀请；加入方导入后采用邀请方的资源下载加速配置，保存到本机，随后自动发现成员。</p>

        <div class="federation-invite-actions"><button id="export-invite" class="secondary">生成联邦邀请</button></div>
        <label>联邦邀请<textarea id="federation-invite" rows="5" placeholder="粘贴可信管理员提供的联邦邀请 JSON" autocomplete="off"></textarea></label>
        <div class="action-buttons"><button id="join-network" class="primary">加入邀请中的网络</button><button id="leave-network" class="quiet">退出联邦</button></div>
        <form id="leave-form" class="action-panel" hidden><h3>只退出当前节点</h3><p>本节点转为独立服务，其他节点不会被退出。已经同步的资料无法远程收回；现有联邦连接会断开。</p>
          <label class="check"><input id="leave-consent" type="checkbox" required>我已确认影响，退出当前节点</label>
          <div class="action-buttons"><button class="secondary" type="submit">确认退出</button><button class="quiet" type="button" id="cancel-leave">取消</button></div></form>
      </section>
      <section class="console-card" id="settings"><span class="eyebrow muted">NODE SETTINGS</span><h2>节点设置</h2>
        <form id="settings-form"><div class="form-grid"><label>节点名称<input id="setting-name" maxlength="80" required></label><label>节点地区<input id="setting-region" maxlength="80" placeholder="例如：中国 · 上海"></label></div>
        <label class="check"><input type="checkbox" id="setting-public">允许旧版客户端匿名联机（兼容设置）</label>
        <label class="check"><input type="checkbox" id="setting-registration">开放用户注册</label>
        <label class="check"><input type="checkbox" id="setting-drain">维护模式：暂停创建新房间</label>
        <div class="divider"></div><h3>房间与带宽容量</h3>
        <div class="capacity-fields">
          <label>最多同时开放房间<input id="setting-max-rooms" type="number" min="1" max="128" step="1" required></label>
          <label>每房间人数上限<input id="setting-room-capacity" type="number" min="1" max="1000" step="1" required></label>
          <label>服务器上传带宽 · Mbps<input id="setting-bandwidth" type="number" min="0" max="1000000" step="0.001" required><small>直接填写云厂商的带宽，例如 6、20、200；使用上传值。</small></label>
          <label>保留空闲带宽 · %<input id="setting-reserve" type="number" min="20" max="80" step="1" required></label>
          <label>每位中转访客预算 · Mbps<input id="setting-player-budget" type="number" min="0.008" max="800" step="0.001" required></label>
        </div>
        <label class="check"><input id="setting-proxy-enabled" type="checkbox">启用可信反向代理</label>
        <div id="trusted-proxy-settings" hidden>
          <label>可信反向代理网段（每行一个 CIDR）<textarea id="setting-proxies" rows="5" spellcheck="false" placeholder="填写自建反向代理的来源网段"></textarea></label>
          <small>仅信任命中这些网段的来源地址，读取转发的客户端 IP 和 HTTPS 协议。</small>
          <label class="check"><input id="setting-cloudflare-enabled" type="checkbox">Web 入口使用 Cloudflare</label>
          <div id="cloudflare-proxy-settings" hidden><small>启用后自动信任 Cloudflare 官方 IPv4/IPv6 网段，每日刷新；失败保留上次成功缓存。</small>
            <p id="cloudflare-proxy-status" role="status" aria-live="polite">正在读取刷新结果…</p>
          </div>
        </div>
        <details><summary>人数是怎样估算的？</summary><p>上传带宽 ×（1 − 预留比例）÷（每人预算 × 1.15），向下取整。默认每人预算 1.024 Mbps，预留 35%，另留 15% 协议开销。填 0 表示未配置带宽预算。</p><p>这里是节点的中转访客预算，不包含房主，也不是 CPU、内存或实际流畅度的保证。地图、视距、模组和区块加载会影响实际负载；每房间人数仍受单独上限限制。</p></details>


        <button class="primary wide" type="submit">保存设置</button></form>
        <div class="divider"></div>
        <div class="section-heading"><div><span class="eyebrow muted">RESOURCE ACCELERATION</span><h3>资源下载加速</h3></div><span class="badge" id="room-origin-state">尚未接入</span></div>
        <p>让源站临时补齐下载失败的模组和资源。先提交文件信息，只在确有需要时向房主请求对应文件；无需上传整个整合包。</p>
        <p id="room-origin-address" class="muted"></p>
        <p class="muted">加入联邦时会采用邀请方的这项配置。独立接入时，在源站后台 → 系统设置 → 镜像设置 → 客户端加速 → 接入联机节点生成邀请；也可从源站的节点管理进入。</p>
        <form id="room-origin-form"><label>源站接入邀请<textarea id="room-origin-invitation" rows="3" maxlength="4096" placeholder="粘贴源站生成的一次性接入邀请，密钥将自动配置" autocomplete="off" spellcheck="false" required></textarea></label>
        <div class="action-buttons"><button class="primary" type="submit">接入源站</button><button id="room-origin-disconnect" class="secondary" type="button" hidden>断开接入</button></div></form>
        <p id="room-origin-feedback" class="feedback" role="status">粘贴一次即可接入，配置自动保存并立即生效，无需重启。邀请仅交给节点管理员。</p>
      </section>
      <section class="console-card" id="users"><span class="eyebrow muted">NETWORK USERS</span><h2>用户列表</h2><label>查找用户<input id="admin-user-filter" type="search" placeholder="用户名、账号 ID 或注册节点"></label><p id="admin-user-count"></p><div class="user-table-scroll"><table class="user-table"><thead><tr><th>用户</th><th>注册节点</th><th>同步状态</th><th>本地资料</th><th>副本</th><th>本地权限</th></tr></thead><tbody id="admin-accounts"></tbody></table></div></section>
      <section class="console-card" id="players"><span class="eyebrow muted">LIVE PLAYERS</span><h2>在线玩家</h2><p id="admin-player-count"></p><div class="user-table-scroll"><table class="user-table"><thead><tr><th>玩家</th><th>角色</th><th>房间</th><th>传输路径</th><th>P2P ping</th><th>节点控制 RTT</th><th>游戏状态 RTT</th></tr></thead><tbody id="admin-players"></tbody></table></div></section>
      <section class="console-card" id="history"><span class="eyebrow muted">LOCAL HISTORY</span><h2>节点操作记录</h2><div id="node-history"></div></section>
    </div>
    <footer v-show="adminAuthenticated"><span>BUGGCRAFT / 节点控制台</span><a href="/developers">开发文档</a></footer>
  </main>
</template>
