# 房间目录协议

## 房间目录

SSH 子系统名为 `buggcraft-rooms-v1`，使用 UTF-8 JSON Lines，一行一个对象。请求示例：

```json
{"op":"ping"}
```

响应格式：

```json
{"ok":true,"result":{"version":1,"rooms":0,"max_streams":512,"capabilities":["room-invite-policy-v1"]}}
{"ok":false,"error":"Invalid room ID"}
```

请求缓冲上限 16 KiB；单身份每 10 秒最多 32 次请求，并限制控制通道数量。错误信息截断为 160 字符。列表每页最多 50 项，并限制序列化预算；客户端仍须按 SDK 限制读取响应。

|操作|权限与用途|
|---|---|
|ping|协议版本和容量信息|
|list|目录身份获取公开房间，使用 offset 分页|
|state|目录身份查询房间就绪、等待、缺失或关闭状态|
|topology|目录身份获取已验证联邦成员入口及签名地区、负载、接纳提示；可传 `offset`、`limit`（1–32，默认 32），返回 `nodes,routes,next,total`，供客户端分批缓存备用节点|
|locate|目录身份按随机房间 ID 查找归属节点|
|resolve|目录身份解析公开房间；需要邀请码时只返回 room，允许直接加入时还返回 secret；私密房间拒绝解析|
|account_lookup|个人目录身份按 identifier 精确查用户名（不区分大小写）或完整账号 ID，优先读取本地已验证的签名目录，未命中时受限补查；返回 accounts 列表，每项只有 account 和 username，最多 8 项。公开用户名目录不要求开启加密副本；匿名凭据不能查好友|
|configure / availability / close|声明 `room-lobby-v1` 时由当前管理者更新房间资料/关闭房间，世界提供者更新游戏清单/世界可用性；转让不会搬迁存档，授权细节见[准备房间](room-lobby.md)|
|snapshot|经验证的联邦身份获取签名成员公告、房间目录、授权公开的账号目录及分配给本节点的加密副本|
|account_record|联邦身份取回用户已授权跨节点使用的加密账号记录，不返回明文密码|
|account_update|联邦身份向指定持久副本节点提交用户签名的新设置及复制策略|
|online_players|仅联邦身份读取本节点在线房主/访客摘要；`offset` 为 0–999，返回 `players,more`，每页最多 48 项，不返回诊断日志和地址|
|introduce|将已签名的节点公告交给本地联邦验证，不绕过入网认证|

游戏数据使用独立 SSH 转发通道及 libssh 流控，由 C++ 核心直接转发，不封装为目录 JSON。聊天使用房间内独立控制通道，限制帧大小、速率与历史长度，不跨房间广播。

`configure` 的 `public` 与 `require_invite` 均为布尔值，后者默认 false。`public=false` 时始终需要邀请码，且目录不展示；`public=true, require_invite=false` 表示大厅直接加入；两者均为 true 时公开展示但须持有邀请码。房间描述与签名联邦目录均含 `require_invite`。目录身份不能获取受保护房间的 secret，游戏客户端认证仍校验房间随机密钥。创建受保护的公开房间前，SDK 必须核对 `ping.capabilities` 含 `room-invite-policy-v1`，旧节点缺少该能力时停止创建。

## 游戏信息与分页边界

房间 `game` 包含版本、协议、加载器、加载器版本与模组摘要。本节点 `list` 为节省响应省略 `game.mods`；联邦公开房间可带最多 24 项预览，客户端不能要求所有列表项都有预览。完整清单通过桥的独立模组通道读取，字段、参数与调用顺序见[游戏环境与模组同步](game-metadata.md)。

`list` 除每页最多 50 条，还受序列化预算限制；用实际返回的 `next` 继续，不能固定加 50。`topology` 同理按 `next` 分页，并核对所返回入口的公钥与信任来源。私密房间只跨节点传播随机 ID；不存在可公开枚举完整私密环境的接口。
