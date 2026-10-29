<script setup lang="ts">
defineProps<{seed:Record<string,string>}>();
</script>

<template>
<nav class="theme-nav" aria-label="主题"></nav><main><aside><a class="brand" href="#">Buggcraft <span>NODE</span></a><h1>从这里，<br>连接你的朋友。</h1><p>完成三步设置，节点就可以提供联机服务。以后可以在管理后台修改设置。</p>
<ol id="steps"><li class="active">01 <span>节点与网络</span></li><li>02 <span>管理员账号</span></li><li>03 <span>容量与开放范围</span></li></ol>
<p class="aside-note">初始化链接是一次性凭据，请勿转发。保存成功后，服务会自动进入运行状态。</p></aside>
<section class="content"><div class="eyebrow">首次部署</div><form id="setup-form">
<input type="hidden" id="setup-token" :value="seed.setupToken">
<fieldset><h2>给节点一个名字</h2><p class="intro">这个名字会显示在网页和启动器的节点列表中。</p>
<label>节点名称<input name="name" required maxlength="80" value="我的联机节点" autocomplete="off"></label>
<label>服务器域名或公网 IP<input name="host" required maxlength="253" placeholder="node.example.com" autocomplete="off"><small>只填写地址，不带 https:// 或端口。可以使用已有的防护入口域名。</small></label>
<div class="columns"><label>联机端口<input name="port" type="number" min="1024" max="65535" value="3333" required><small>需要放行 TCP 和 UDP，服务账户使用 1024 以上端口。</small></label>
<label>网页端口<input name="web_port" type="number" min="1024" max="65535" :value="seed.webPort" required><small>需要放行 TCP，服务账户使用 1024 以上端口。</small></label></div>
<details><summary>已有独立 TCP 代理入口</summary><p>仅在已经配置 TCP 防护或中转入口时填写；网页脱敏无法隐藏实际连接地址。</p>
<label>对外联机域名<input name="entry_host" maxlength="253" placeholder="relay.example.com"></label>
<label>对外联机端口<input name="entry_port" type="number" min="1" max="65535" placeholder="443"></label></details></fieldset>
<fieldset hidden><h2>设置管理员</h2><p class="intro">这个账号用于管理节点，与玩家账号分开。密码不会写入初始化日志。</p>
<label>管理员用户名<input name="username" value="admin" required pattern="[A-Za-z0-9_-]{1,32}" maxlength="32" autocomplete="username"><small>使用英文字母、数字、下划线或短横线。</small></label>
<label>管理员密码<input name="password" type="password" required minlength="12" maxlength="256" autocomplete="new-password"><small>至少 12 个字符，建议使用密码管理器生成。</small></label>
<label>确认密码<input name="password_confirm" type="password" required minlength="12" maxlength="256" autocomplete="new-password"></label>
<div class="note">请保存好管理员密码。初始化生成的密钥和数据文件需要定期备份，不能上传到公开仓库。</div></fieldset>
<fieldset hidden><h2>决定节点怎么开放</h2><p class="intro">容量是准入上限；实际可用人数还取决于世界、视距和玩家活动。</p>
<div class="columns"><label>最多房间数<input name="max_rooms" type="number" min="1" max="128" required value="32"></label>
<label>每个房间人数上限<input name="room_capacity" type="number" min="1" max="1000" required value="16"></label></div>
<label>可用于中转的上传带宽 · MB/s<input name="bandwidth_mb_s" type="number" min="0" max="125000" step="0.001" value="0" required><small>运营商标注的 Mbps ÷ 8 = MB/s；云服务器也请核实单位。填 0 暂不按带宽限流。</small></label>
<p class="budget" id="budget">填写带宽后，会按保留 35% 空闲、每位中转访客预算 0.128 MB/s 和 15% 协议开销估算准入人数。这是初始预算，不是流畅度保证。</p>
<label class="check"><input name="public_join" type="checkbox">允许未登录的玩家领取匿名启动器接入码</label>
<div class="note">默认关闭玩家注册。启动后可以在后台设置邮件验证码、开放注册，或在“连接网络”中加入已有联邦。独立运行不需要加入联邦。</div>
<label class="check"><input name="acknowledge" type="checkbox" required>我已确认端口可用，并会妥善保存节点数据。</label></fieldset>
<p id="error" role="alert" hidden></p><div class="actions"><button type="button" id="back" class="secondary" hidden>上一步</button><span id="progress">1 / 3</span><button type="button" id="next">继续</button><button type="submit" id="save" hidden>保存并启动节点</button></div>
</form><div id="success" hidden><div class="done">✓</div><h2>节点已配置</h2><p>正在切换到节点服务。初始化入口已关闭，请使用刚设置的管理员账号登录。</p><p>首次使用自动生成的证书，浏览器可能再次提示确认。面向公众使用前，请换成可信证书或配置 HTTPS 代理。</p><a class="button" id="console-link">进入管理后台</a></div></section></main>
</template>
