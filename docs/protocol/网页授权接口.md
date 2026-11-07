# 网页授权与状态 API

## 网页授权接口

HTTP 请求/响应为 JSON，接口不开放 CORS；非本机明文 HTTP 拒绝授权。正常状态 200，注册成功 201；无有效登录 401、权限/CSRF/Origin 拒绝 403、无效输入 400、并发版本冲突 409、频率超限 429、备份暂时不可写 503。部分错误返回纯文本，客户端应先检查 HTTP 状态并兼容 JSON `error` 和纯文本。

|方法与路径|请求与响应|
|---|---|
|GET /api/status|公开名称、节点地区/心跳/节点间延迟、接纳提示；`players` 为存活联邦节点桥会话总数，其余多项指标仍为本节点，见下表；不返回 token 或私密房间标题|
|GET /api/account/players|需账号 Cookie，返回全网在线摘要 `players,partial,unavailable_nodes,total`；不提供管理员诊断日志|
|GET /api/admin|需管理员 Cookie；返回节点设置、容量预算和服务器实时收发带宽|
|POST /api/profile|节点访问密码 `{"password":"..."}`；开放领取或已有账号 Cookie 可传 `{}`。默认格式 1 返回 `code,profile,credential,fingerprint` 以兼容现有客户端；传 `"format":2` 返回 `buggnode2-` 随机引用码及 `credential,fingerprint`，不返回 profile。个人私钥仅在浏览器附加，不上传节点|
|POST /api/account/enroll|开放注册时传 `{}`，返回 `account,nonce,message,iterations`|
|POST /api/account/email-code|开放注册时传 `email,username,nonce`，绑定该次注册挑战，返回 `ticket,expires_in,retry_after`|
|POST /api/account/register|`nonce,username,public_key,state,signature,copies,routing,routing_signature,proof`；要求邮箱时还须 `email_ticket,email_code`。校验初始身份与复制策略用户签名，返回 account。用户名为 3–32 位 ASCII 字母/数字/下划线/连字符，大小写不区分查重。完整生成方法参照 `/accounts.js`|
|POST /api/account/challenge|`{"account":"用户名或完整ID"}`，路由取回 `nonce,message,record`，多匹配应使用完整 ID；不发送用户密码|
|POST /api/account/login|`nonce,signature`，签名覆盖原始 message UTF-8；返回 `account,csrf` 及 HttpOnly/SameSite=Strict 的 account_session Cookie，HTTPS 下 Secure；会话 1 小时|
|GET /api/account/me|需 Cookie，返回 `account,username,home,revision,backup,csrf`|
|GET /api/account/record/{account}|可获取本节点允许访问的账号密文记录，不是账号枚举接口；不可当作密码保护的下载|
|POST /api/account/backup|需 Cookie 和 X-Account-CSRF；`state,signature,routing,routing_signature`，revision 必须比当前新 1，返回 `ok,revision`|
|POST /api/account/recover|`{"record":加密恢复记录}`，验证来源网络与版本；返回登录挑战，不免除签名验证|
|POST /api/account/logout|`{}`，需 Cookie 和 X-Account-CSRF，清除会话|

账号签名为 P-256 ECDSA/SHA-256，64 字节 r||s 的 Base64URL 无填充格式。加密为 PBKDF2-HMAC-SHA256（600000 次）+ AES-256-GCM，16 字节 salt、12 字节 iv，AAD 为 `account|revision` UTF-8。canonical JSON 递归按键排序、无空格、ASCII Unicode 转义。挑战签名必须覆盖返回的原始 message；先验证协议 p、Origin o、nonce r、账号 a 及期限等字段符合客户端授权，避免盲签。客户端验证证书、加密状态和路由签名；完整约束参照 accounts.py、accounts.js 与 account_replication.py。

个人接入码在不同设备可使用，其 `acct1` token 最长 7 天有效，泄露相当于泄露该授权。不得给第三方网站或日志。无需账号的匿名联机仍可使用联邦目录、跨入口找房与备用入口。

## 状态指标范围

| 字段 | 范围与口径 |
|---|---|
| `/api/status.players` | 本节点实时数量加有效联邦公告的数量，房主/访客桥会话均计入，包含私密房间；不是去重账号数或实际游戏人数 |
| `nodes[].players` | 对应节点的公告人数，过期节点计 0；旧公告无 `statistics.players` 时仅按其公开房间 `members` 回退 |
| `rooms,connections,streams,capacity,max_rooms,max_connections` | 本节点，不能与全网人数直接比较推算剩余容量 |
| `p2p_players,relay_players,unconfirmed_players` | 本节点访客路径上报，不是全网路径分布 |
| `game_players` | 本节点可用世界最近 15 秒内房主 Minecraft 状态上报之和；未上报不代表无人 |
| `game_counts_reported,game_counts_unknown` | 本节点有有效游戏人数报告/缺少报告的可用世界数 |

在线详情最多并发查询 3 个成员，每节点 4 秒、整批 5 秒预算，结果缓存 5 秒，并合并同时请求。失败/旧版/截断时 `partial:true`，`unavailable_nodes` 为未完整取得详情的节点 ID。`total` 来自公告聚合，可与 `players.length` 不同，不能以缺失列表判定所有人离线。公告 TTL 为 30 秒，跨节点人数并非强一致实时计数。

详情每项有 `username,role,room,started,path,control_rtt_ms,game_rtt_ms,peer_rtt_ms,last_report_at,node,node_name`。私密标题替换为“私密房间”；超过 20 秒未上报时路径为 `unknown`、RTT 为 `null`。时间戳为 Unix 秒，RTT 单位为毫秒。该接口仅提供会话摘要，管理员诊断仍限本节点。
