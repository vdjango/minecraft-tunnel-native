# 当前节点通信协议

本文件描述原生节点核心与启动器桥。服务端入口为 `web/main.py`，Python 控制业务位于 `web/tunnel/`；C++ SSH/STUN 与转发核心位于 `core/`，C++ 启动器 JSON-RPC 桥位于 `bridge/`。启动器通过 C ABI 动态库或本机进程管道传递控制参数，不实现网络联机协议。动态库入口、线程和内存所有权见 [C ABI 文档](docs/bridge-library.md)。

## 传输与身份

联机控制、信令与中继使用 SSH 加密连接；P2P 游戏流量使用 WebRTC DTLS/SCTP。客户端必须校验节点配置中的固定服务器公钥。玩家访问凭据与管理员网页密码分开。房主、访客、目录与联邦成员分别验证权限，只允许本房间的转发目标，不开放 shell、SFTP 或任意目标代理。

网页使用 HTTPS。接入码封装节点入口、公钥与玩家凭据，属于敏感信息；编码本身不构成加密，也不能阻止客户端得知实际连接地址。

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
|configure / availability / close|房主更新房间资料、世界可用性与结束状态，具体字段见实现|
|snapshot|经验证的联邦身份获取签名成员公告、房间目录、授权公开的账号目录及分配给本节点的加密副本|
|account_record|联邦身份取回用户已授权跨节点使用的加密账号记录，不返回明文密码|
|account_update|联邦身份向指定持久副本节点提交用户签名的新设置及复制策略|
|introduce|将已签名的节点公告交给本地联邦验证，不绕过入网认证|

游戏数据使用独立 SSH 转发通道及 libssh 流控，由 C++ 核心直接转发，不封装为目录 JSON。聊天使用房间内独立控制通道，限制帧大小、速率与历史长度，不跨房间广播。

`configure` 的 `public` 与 `require_invite` 均为布尔值，后者默认 false。`public=false` 时始终需要邀请码，且目录不展示；`public=true, require_invite=false` 表示大厅直接加入；两者均为 true 时公开展示但须持有邀请码。房间描述与签名联邦目录均含 `require_invite`。目录身份不能获取受保护房间的 secret，游戏客户端认证仍校验房间随机密钥。创建受保护的公开房间前，SDK 必须核对 `ping.capabilities` 含 `room-invite-policy-v1`，旧节点缺少该能力时停止创建。

## 联邦与存活状态

成员以自己的 Ed25519 私钥签名公告；同一网络还需入网密钥证明成员资格。私钥不进入同步副本。公告经验证后逐步传播，节点彼此探测，无需固定主节点。不设置联邦成员总数上限；每节点每 0.8–1.2 秒调度一次，最多同时交换 3 个成员，单次交换最多 4 秒。失败节点按 2–60 秒指数退避并加入随机偏移，不等待离线节点再调度健康节点。签名公告有效期为 30 秒，重复签名公告不能延长有效期；过期只移除可用路由，不删除已学习的成员配置，不向其他节点发送级联退出命令。

成员目录使用 `record_cursor` 分页，每次至多 32 条成员记录，其中优先包含响应节点自身的签名公告；请求只携带对应页的版本摘要，保持在 16 KiB 控制帧内。响应返回下一页游标，空字符串表示本轮结束。32 是单次交换预算，不是成员总数限制。账户 RPC 使用至多 3 个工作任务和总时限，不为每位成员同时启动网络请求。更大的联邦仍受服务器资源、出口带宽和公告传播速度约束，不承诺无限容量。

`snapshot` 可附加 `record_versions`（节点 ID → `{state,issued}`）和 `snapshot_versions`（`accounts`、`account_routes` 摘要）。目录状态不变时只发送 `{refresh:{id,issued,state},proof,signature}`；接收方用本地同摘要的目录恢复原公告后验证原 Ed25519 签名，缺少基础记录或验证失败则拒绝。无版本参数的旧客户端仍收到完整公告。未改变的账号区段返回空列表并附最新摘要，空列表不表示删除本地记录。单节点出站联邦同步池最多保留 3 条固定公钥 SSH 连接。

原生桥和节点以收到的 SSH 数据包更新存活时间；空闲约 5 秒发送标准 SSH keepalive，20 秒没有收到 SSH 数据则清理连接。TCP ACK 不延长此期限；繁忙连接不额外发送心跳。日志记录 `heartbeat-timeout`/`heartbeat_timeout`。房主异常失联先将房间置为等待，默认保留 120 秒恢复窗口；正常结束房间立即执行关闭流程。访客退出只释放自己的流和容量。A→B、C→A 完成成员发现后，C 可独立联系 B，A 的退出或故障不会使 C 退出。仅承载在故障节点自身的中转流无法在另一节点上无缝续接。

入网邀请关系不是转发路径。即使按长链加入，成员发现后会保留各成员的地址和固定公钥；`list`、`locate`、`resolve` 从本地签名目录回答，不沿邀请关系逐跳查询。原生桥加入邀请码房间前调用 `locate`，随后直接连接所属节点并验证其固定公钥。目录访问失败时最多尝试 3 个已缓存入口，成功的备用入口保留给后续操作；不会跟随无限重定向。只读目录请求可切换入口，写请求不自动重放。

好友公开目录由后台快照复制。正常查询命中不产生节点间 RPC；未命中时一个节点最多进行一批补查，每批最多直查 2 个成员，两批启动间隔至少 1 秒，单次补查最多 4 秒，重复查询合并，未命中缓存 2 秒。完整账号 ID 优先定位其 home 节点；联邦身份的 `account_lookup` 只读本地目录，不递归补查。补查结果验证账号发行和用户路由签名，并只保存公开头信息，不复制加密资料；与心跳共用最多 3 条出站连接。目录最终一致，新账号尚未传播时可能需要稍后重试，已同步的同名身份仍分别返回完整账号 ID。

上述故障隔离要求健康节点之间有实际网络可达性，并且已学习成员入口。如果 H 是两侧唯一的物理网络通道，或在成员互相发现前失联，缓存不能创造缺失的网络路径。单个房间所在节点故障也不等于房间可以自动迁移。长链及故障回归见 `tests/test_federation_chain.py`。

公开房间同步有限元数据；私密房间只同步随机 ID，不同步名称、邀请码密钥或聊天记录。账号同步包含公钥、签名与客户端加密记录，不能把全库字段都当作私密数据。

账号复制必须包含用户签名的 `routing`（account、revision、copies、digest）与 `routing_signature`，绑定加密状态版本及摘要；无用户授权的旧格式记录不再导入。copies 为 0 时仍同步公开用户名、账号 ID、公钥与签名目录，但不向其他节点持久复制加密身份包；其他节点可以路由到在线注册节点登录，注册节点不可用时则需要恢复文件。copies 为 1–8 时维护注册节点加指定数量的额外持久副本。心跳 `account_copies` 只公告账号和当前密文摘要，用于移交确认与故障补位。登录节点可按目录临时取回密文，不自动成为持久副本。

个人 `acct1` 凭据同时携带节点签名与用户对登录授权的 P-256 签名，绑定账号、签发节点、网络和最长 7 天的有效期；无用户证明的旧版个人凭据不再接受。联机 SSH password 仅使用 `~` 之前的凭据段，好友操作使用客户端保留的用户证明段。网页注册与登录无需向节点发送用户密码，管理员密码仍走原有 HTTPS 管理鉴权。

签名提供来源认证，不保证成员内容诚实，也不提供强一致性或全网封禁。分区期间拒绝选择歧义的房间归属。

## 恢复与兼容

客户端首次发现网络后缓存备用入口。失联时可以重新查询目录并尝试其他节点；归属节点失败需要房主重建房间、访客重新进入游戏。旧 SSH/TCP、游戏会话和聊天历史不能自动迁移。

原来的自定义二进制包头、PacketType、主从注册与 Raft 文档已随未使用框架一起移出当前实现。接入应复用现有 SDK，不能按旧文档构造消息。

部署与信任边界见 [部署指南](docs/deployment.md)，性能测试方法见 [性能说明](docs/performance.md)，原生接口与加载方式见 [桥接库指南](docs/bridge-library.md)。

## 第三方启动器标准入口

第三方启动器使用独立连接组件，不实现 ICE、打洞、DTLS、SCTP 或 SSH 转发。组件通过 **JSON-RPC 2.0 over stdio** 与启动器通信：一行一个 UTF-8 JSON 请求和响应，版本字段固定为 `"2.0"`；当前不支持 batch 请求；请求上限 8 KiB。标准输入输出只承载协议帧，诊断写标准错误。组件负责节点固定公钥校验、P2P 探测、加密中转回退与回环地址监听。

桥可使用 `node.add.params.alternatives` 导入最多 5 个固定公钥的备用入口。最多并行探测 3 个已导入节点，每 30 秒刷新；加权分数考虑实测目录往返时间、出口带宽负载、剩余访客名额及延迟抖动。新线路须明显胜过当前线路才切换，普通切换有 90 秒保持期；当前入口失联或不再接纳房间时立即切到可用备用入口。满载时仍可读目录，但会拒绝在该节点新建房间。`node.selected` 返回当前入口与备用路由，`node.routes` 可读取其状态，`node.failed` 报告连接失败；`bridge.events` 读取待展示的一次性线路切换提示。选路改变仅影响之后的新连接，已建立的游戏 TCP 会话继续走原线路，并非透明迁移。若本地原生桥缺失、能力过旧或操作系统阻止启动，启动器明确提示失败；需将桥及其依赖完整打包后使用。

原生组件位于 [minecraft-tunnel-native 仓库](https://github.com/vdjango/minecraft-tunnel-native)。从 [原生构建](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml) 下载对应系统的产物，解压保留完整目录，启动 Windows 的 `buggcraft-bridge.exe` 或 Linux/macOS 的 `buggcraft-bridge`，通过子进程管道通信。接入方无需 Python 或语言专用 SDK；桥运行在玩家本机，不提供公网 RPC 端口。`--version` 和 `bridge.initialize.result.build.version` 返回版本；后者另含 `implementation:cpp`、协议版本和签名状态。

桥本身不需要 Python。完成 CMake 安装后可直接运行：

```sh
# Linux/macOS
./runtime/bin/buggcraft-bridge
```

```powershell
# Windows PowerShell
./runtime/bin/buggcraft-bridge.exe
```

桌面集成以子进程启动，先调用 `bridge.initialize`，然后将用户提供的普通或个人节点接入码调用一次 `node.add`。保存返回的进程内 `node` 句柄；后续房间调用只使用这个句柄。关闭进程会清理本机监听和远端会话。第三方应用应将接入码放在系统安全存储中，不得放在命令行参数、日志或 URL。匿名身份可以联机但没有好友能力；个人接入码同时是账号授权凭据。

|JSON-RPC 方法|请求参数|返回值|
|---|---|---|
|bridge.initialize|`{"protocol":1}`|协议版本、能力和资源上限|
|node.add|`{"code":"buggnode1-…","alternatives":[]}`|进程内节点句柄、节点名称和身份类型；原生桥最多接纳 5 个备用节点配置|
|node.remove|`{"node":"…"}`|删除节点；仍有活动房间时返回 `-32009`|
|node.selected（原生桥）|`{"node":"…"}`|当前入口配置、名称、最多 3 个备用入口和接纳状态；配置含凭据，仅在本机管道传递|
|node.routes（原生桥）|`{"node":"…","refresh":false}`|已探测路由的延迟、负载、剩余名额和分数；不返回入口地址或凭据|
|node.failed（原生桥）|`{"node":"…","profile":{…}}`|标记指定入口失败并使选择缓存过期|
|bridge.events（原生桥）|`{}`|消费待展示的线路切换提示；重复读取不重放|
|room.list|`{"node":"…","offset":0}`|公开房间及分页字段|
|room.host|`{"node":"…","game_port":25565,"transport":"auto"}`|房间邀请码及会话；启动器继续在本机游戏服监听端口上运行|
|room.join|`{"node":"…","invite":"bugg1-…","transport":"auto"}`|会话和本机地址，例如 `127.0.0.1:54123`；把该地址交给游戏客户端|
|room.close|`{"session":"…"}`|关闭房间会话和本地端口|
|node.request|`{"node":"…","request":{"op":"…"}}`|原生桥执行目录请求，包含好友查询等操作|
|social.operate|`{"node":"…","target":"账号ID","operation":"request"}`|用个人接入码的本地委托密钥签署好友操作并发送；业务操作见 `web/tunnel/social.py`|
|game.status / game.discover|`{"port":25565}` / `{"duration_ms":3000,"exclude":[]}`|原生游戏状态探测 / 本机局域网世界发现|
|room.request / room.configure / room.retarget|会话句柄及 `request` / `settings` / `port,game`|房间控制、访问策略与游戏端口更新|
|chat.send / chat.poll|会话句柄及 `text` / 会话句柄|聊天发送及状态读取|
|session.events|`{"session":"…"}`|消费房间状态、聊天、游戏聊天和路径事件|
|mods.manifest / mods.fetch / mods.status / mods.cancel|会话句柄及对应参数|异步整合包清单、分页、SHA-256 下载校验、进度及取消|
|session.list|`{}`|当前会话状态；访客路径为 `p2p` 或 `relay`，房主为 `auto`（接受混合路径）或 `relay`|

房间路径按访客分别建立。`transport:"auto"`（默认）尝试 P2P，无法建连时该访客使用中继；`transport:"relay"` 让该访客跳过 P2P。同一个房间可以同时有 P2P 和中继访客。房主也可以在 `room.host` 指定 `transport:"relay"`，此时该房间所有访客使用中继。启动器应允许每位访客选择连接策略，并显示 SDK 返回的实际路径。

除 JSON-RPC 标准错误 `-32700`（JSON 解析）、`-32600`（请求）、`-32601`（方法）和 `-32602`（参数）外，`-32000` 表示节点连接失败，`-32004` 表示句柄不存在，`-32009` 表示活动会话阻止删除，`-32010` 表示资源上限，`-32020`/`-32021` 表示房间启动失败/超时。错误文本不包含凭据。更完整字段与发行包由 `/developers` 指引；当前发行包未提供受信任代码签名或 Apple 公证。

桥支持好友委托签名、公开建房设置、房间解析、游戏聊天和整合包传输；网页登录仍由网页完成。`room.host.params.settings` 接收 `public`、`require_invite` 和游戏元数据；`game_port:0` 表示等待本机游戏世界，之后通过 `room.retarget` 指定端口。`room.resolve` 返回获准加入的公开房间密钥，再调用 `room.join`。账号材料不得交给其他网页。完整能力以初始化返回的方法列表为准。进程不向 stdout 主动推送通知，启动器轮询 `session.events` / `bridge.events` 和 `session.list`；关闭 stdin 后清理会话并退出。标准错误应持续读取，避免管道堵塞。

接入序列示例：

```json
{"jsonrpc":"2.0","id":1,"method":"bridge.initialize","params":{"protocol":1}}
{"jsonrpc":"2.0","id":2,"method":"node.add","params":{"code":"buggnode1-<用户导出的接入码>"}}
{"jsonrpc":"2.0","id":3,"method":"room.join","params":{"node":"<node句柄>","invite":"bugg1-<房间邀请码>","transport":"auto"}}
{"jsonrpc":"2.0","id":4,"method":"room.close","params":{"session":"<session句柄>"}}
```

访客启动后将结果中的本机回环地址交给 Minecraft。`transport:"auto"` 先尝试 WebRTC 直连，失败时在连接开始前选择 SSH 中转。已建立的 TCP 游戏连接若 P2P 路径中断会断开；玩家重连后可使用备用中转，不会伪称为透明迁移。直连会使同一房间对端获知网络地址，重视地址隐私时指定 `transport:"relay"`。端到端直连受 NAT 和防火墙影响，服务器 UDP 地址探测不是 TURN 中继，也不保证所有网络均能打洞。

每对玩家的原生桥共用一条 `probe` DataChannel 探活，不为每条游戏流单独创建心跳。先交换文本 `BCH1?` / `BCH1!` 协商能力；未获得响应最多探测 3 次，旧版仍使用 ICE/SCTP 自带的存活检测，不按新协议判超时。支持方每 5 秒发送 `BCP1:<递增序号>`，对端回复 `BCA1:<原序号>`；仅当前探测的有效回复延长 20 秒存活期限，重复、无效或仅单向收到的 ping 均不能续期。每帧最多 32 字节，回复限频。

直连失效仅释放对应玩家的 P2P 流，保留健康 SSH、房间、本地监听地址及已有中转流；新游戏连接走中转。信令仍可用时，以 2/4/8/16/30 秒退避重新协商直连，成功后新连接优先直连，已有中转流继续原路径。首次协商超时也会退避重试。SSH 自身失效仍按房间重连处理。信令子通道故障不等于 SSH 故障，不应关闭健康 SSH。底层 ICE/SCTP 可能早于应用心跳发现故障。

游戏 DataChannel 使用库分配并回收的 SCTP 流编号，不使用会耗尽协商范围的单调递增编号；创建失败时，仅该新游戏连接退回中转。日志包含 `peer_heartbeat_enabled`、30 秒汇总 `peer_heartbeat_sample`、`peer_heartbeat_timeout`、`peer_fallback`、`peer_retry_started`、`peer_recovered` 及游戏流的 `sctp_stream`，按玩家分别落盘。

原生目录与转发协议作为高级扩展保留给不采用此连接组件的实现者：接入码包含 `server_host`、`server_port`、`token`、`server_public_key`。必须固定校验 SSH 主机公钥；目录用户名 `directory:rooms`，密码为 JSON 字符串 `{"token":"…"}`。房主用户名为 `host:ROOM_ID`、访客为 `client:ROOM_ID`，密码另含 `secret`。SDK 的 SSH 隧道直接支持中转模式；若第三方选择原生实现，必须自行处理安全、拥塞、重连、权限和协议兼容，且不会自动获得 P2P 能力。

聊天子系统 `buggcraft-chat-v1`：先发 `{"op":"hello","name":"昵称"}`，然后 `{"op":"send","text":"你好"}`；文本最多 256 UTF-16 单元，每帧最多 4096 字节，访客每 10 秒最多 10 个请求。服务端发 `ready`、`room`、`message`、`bridge`、`error`；历史消息以 `message` 加 `replay:true` 重放。好友认证使用消息内服务端确定的 `account`，不可相信自报昵称。

## 网页授权接口

HTTP 请求/响应为 JSON，接口不开放 CORS；非本机明文 HTTP 拒绝授权。正常状态 200，注册成功 201；无有效登录 401、权限/CSRF/Origin 拒绝 403、无效输入 400、并发版本冲突 409、频率超限 429、备份暂时不可写 503。部分错误返回纯文本，客户端应先检查 HTTP 状态并兼容 JSON `error` 和纯文本。

|方法与路径|请求与响应|
|---|---|
|GET /api/status|公开名称、节点地区/心跳/节点间延迟、接纳提示、房间成员及游戏人数；不返回原始网卡带宽、IP、token 或私密房间资料|
|GET /api/admin|需管理员 Cookie；返回节点设置、容量预算和服务器实时收发带宽|
|POST /api/profile|节点访问密码 `{"password":"..."}`；开放领取或已有账号 Cookie 可传 `{}`；返回 `code,profile,fingerprint`|
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

## 节点管理的权限与流程

`/admin` 是独立管理页面，不含公开管理数据。节点之间没有可远程调用的全网踢人命令；SSH 联邦身份不能调用本机 HTTP 管理接口。本地隔离只是该运营者拒收指定节点的心跳和联邦连接，其他运营者独立决策。

`POST /api/federation/block` 需要本机管理员会话、X-CSRF-Token、`id,blocked,confirmation,reason,password`。confirmation 必须等于完整目标 ID，reason 去除两端空白后 8–300 字，password 重新验证本机管理员。拒绝自己/未知目标、未确认输入和错误密码；重复状态返回 409。隔离后关闭已认证的该成员入站联邦会话、拒绝新联邦认证和新心跳；不撤销已发放的玩家 token，也不使本地以外的客户端停止连接该节点。

恢复执行同一确认流程。操作原因/时间/目标仅写本机配置，最多 100 条，管理员页显示最近 20 条，不同步、不公开；服务器运营者可修改本机记录。泄露共享入网密钥者可能换身份重入，因此邀请只发给可信运营者；若密钥失守，需要可信运营者协调创建新网络、轮换凭据并重新发出邀请，不能声称单个 ID 隔离已消除风险。
