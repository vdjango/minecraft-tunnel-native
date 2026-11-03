<script setup lang="ts">
import NodeHeader from '../NodeHeader.vue';
import ProductLinks from '../ProductLinks.vue';
defineProps<{seed:Record<string,string>}>();
</script>

<template>
<NodeHeader />
<main class="shell user-main"><div class="page-heading"><h1>连接启动器</h1><p>使用节点地址，在浏览器确认登录。</p></div>
<section class="user-card" style="max-width:720px;margin:0 auto"><h2>当前节点地址</h2><p>在启动器中添加此节点，即可连接个人账号、房间和好友。</p>
<div class="node-address-row"><code id="node-address"></code><button id="copy-node-address" type="button" class="primary">复制节点地址</button></div><p id="node-address-feedback" class="feedback" role="status" aria-live="polite"></p>
<ol class="auth-steps" style="display:grid"><li>打开启动器「联机」→「添加节点」</li><li>输入上方地址，点击「浏览器登录」</li><li>在自动打开的浏览器中登录账号并确认设备</li></ol><p>登录成功后，启动器自动返回大厅。勾选「保持登录」后，无需每次重新授权。</p>
<div class="auth-switch"><a class="primary" href="/login?next=/account/access">登录账号</a><a class="secondary" href="/register?next=/account/access">注册账号</a></div></section></main>
<ProductLinks />
</template>
