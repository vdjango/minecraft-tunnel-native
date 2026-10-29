# 启动器桥接协议

## 第三方启动器标准入口

### 协议帧与职责边界

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

### 启动顺序和句柄

桌面集成以子进程启动，先调用 `bridge.initialize`，然后将用户提供的普通或个人节点接入码调用一次 `node.add`。保存返回的进程内 `node` 句柄；后续房间调用只使用这个句柄。关闭进程会清理本机监听和远端会话。第三方应用应将接入码放在系统安全存储中，不得放在命令行参数、日志或 URL。匿名身份可以联机但没有好友能力；个人接入码同时是账号授权凭据。

### 方法速查

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
|mods.configure|`{"session":"…","directory":"游戏实例根目录"}`|房主在游戏端口启用前扫描实例的 mods 子目录|
|session.list|`{}`|当前会话状态；访客路径为 `p2p` 或 `relay`，房主为 `auto`（接受混合路径）或 `relay`|
|bridge.diagnostics|`{}`|刷新 Native 诊断队列，返回健康计数及版本；不停止房间|
|feedback.submit|`{"node":"…","consent":true,"archive":"本机 ZIP 绝对路径","metadata":{…}}`|用户确认后的诊断附件上传；返回反馈编号、字节数及 SHA-256|


### 连接策略

房间路径按访客分别建立。`transport:"auto"`（默认）尝试 P2P，无法建连时该访客使用中继；`transport:"relay"` 让该访客跳过 P2P。同一个房间可以同时有 P2P 和中继访客。房主也可以在 `room.host` 指定 `transport:"relay"`，此时该房间所有访客使用中继。启动器应允许每位访客选择连接策略，并显示 SDK 返回的实际路径。

`room.host`/`room.join` 可选 `ice_family:"auto"|"ipv4"|"ipv6"`，默认 `auto`。该选项限制 ICE 本机绑定地址族，供确定 IPv4/IPv6 验证，和 SSH 控制链路地址族及 `transport` 相互独立。双方强制 IPv4、候选对两端均为 IPv4 且实际 P2P 游戏流完成收发，才作为对应环境的 IPv4 数据面证据。

### 诊断附件提交

`feedback.submit` 必须由明确的用户提交动作调用，每次都提供布尔 `consent:true`；不得因联机失败自动上传。节点句柄应为个人账号接入，匿名码不可上传。`metadata` 必须含 `title`、`description`、`game_version`、`launcher_version`、`system`、`loader` 六个字段，沿用节点反馈字段规则。ZIP 必须是普通文件，大小 1 字节至 20 MiB，不接受符号链接。JSON 控制帧仍限制 8 KiB，大文件内容不进入 JSON-RPC。

桥校验固定节点公钥后使用 SSH 子系统 `buggcraft-feedback-upload-v1`。首行为不超过 8192 字节的 JSON `{consent,name,size,metadata}`；节点返回 `{ok:true,ready:true,issue}` 后接收精确 `size` 字节，客户端发送 EOF，最终返回 `{ok:true,issue,attachment,size,sha256}`。缺少确认、账号、容量或有效字段会拒绝；截断不发布附件并清理临时文件，问题说明可能已创建。原生桥核对字节数和哈希回执后才返回成功。

附件通过既有签名反馈事件同步，管理员可由既有管理页面下载；不是公开下载站。节点处理并发、单文件/总容量、账号未完成反馈数量和读取超时限制。上传不属于实时游戏控制，应使用独立桥进程，避免串行 RPC 等待影响联机轮询。先检查 `bridge.initialize.methods` 是否支持该方法，并确认节点版本包含对应子系统。


### 错误码

除 JSON-RPC 标准错误 `-32700`（JSON 解析）、`-32600`（请求）、`-32601`（方法）和 `-32602`（参数）外，`-32000` 表示节点连接失败，`-32004` 表示句柄不存在，`-32009` 表示活动会话阻止删除，`-32010` 表示资源上限，`-32020`/`-32021` 表示房间启动失败/超时。错误文本不包含凭据。更完整字段与发行包由 `/developers` 指引；当前发行包未提供受信任代码签名或 Apple 公证。

`-32022` 表示没有可接纳新房间的节点容量；应更新路由或稍后重试，不能按凭据错误处理。


### 高级方法与能力

桥支持好友委托签名、公开建房设置、房间解析、游戏聊天和整合包传输；网页登录仍由网页完成。`room.host.params.settings` 接收 `public`、`require_invite` 和游戏元数据；`game_port:0` 表示等待本机游戏世界，之后通过 `room.retarget` 指定端口。`room.resolve` 返回获准加入的公开房间密钥，再调用 `room.join`。账号材料不得交给其他网页。完整能力以初始化返回的方法列表为准。进程不向 stdout 主动推送通知，启动器轮询 `session.events` / `bridge.events` 和 `session.list`；关闭 stdin 后清理会话并退出。标准错误应持续读取，避免管道堵塞。


### 游戏元数据与模组

准备房间的成员进度、房主转让、移出及玩家日志授权使用 `room.request`，能力为 `room-lobby-v1`，字段和权限见[准备房间与成员管理](room-lobby.md)。房间管理者与世界提供者分别标识；转让管理不搬迁世界。

逐字段定义、房主等待/发布顺序、访客完整清单分页和下载请求见[游戏环境与模组同步](game-metadata.md)。缺少元数据表示未知，不能当成原版或完全匹配。备用全部失效的状态和重试上限见[恢复与兼容](compatibility.md)。

房间游戏元数据可含 Minecraft `version` / `protocol`、`loader` / `loader_version`、`mods_hash`、`mods_count` 和最多 24 项公开模组预览。`room.host` 的 `game_directory` 为 Minecraft 实例根目录；如果提供，桥扫描其 `mods` 子目录并自动把模组摘要同步到节点房间目录。也可在房主等待游戏启动时调用 `mods.configure`，参数 `directory` 同样是实例根目录。桥计算 JAR 名称、大小和 SHA-256，完整清单最多 1000 项、按每页 50 项返回。访客通过 `mods.manifest` 创建读取任务，再轮询 `mods.status`；`mods.fetch` 接收清单项与本机 `.jar` 目标路径，最多 4 项并发、单文件不超过 2 GiB，完成后校验 SHA-256 并原子写入目标。Minecraft 版本、协议、加载器和加载器版本由启动器通过游戏元数据提供。桥只传输和校验文件，不负责加载器兼容判断、安装覆盖决策或重启游戏，必须由启动器明确处理。


### 接入码与网络信任

网页提供当前启动器兼容码与新版随机码两种选项。随机码需要新版原生桥及受信任的 HTTPS 网页证书。新版 `buggnode2-` 接入码包含 HTTPS 签发地址和随机引用，桥用 HTTPS 兑换节点配置；旧版 `buggnode1-` 仍兼容。新版避免从接入码字符串直接读出节点 IP，但兑换后的配置仍包含实际连接地址。Cloudflare 普通 HTTP 代理只代理网页请求；节点 SSH/游戏中继使用独立 TCP 端口，不能因为网站接入 CDN 就自动隐藏该端口的源站地址。若要隐藏原站 TCP 地址，需要专用 TCP 代理服务或继续通过中继架构提供服务。


### 最小接入流程

接入序列示例：

```json
{"jsonrpc":"2.0","id":1,"method":"bridge.initialize","params":{"protocol":1}}
{"jsonrpc":"2.0","id":2,"method":"node.add","params":{"code":"buggnode1-<用户导出的接入码>"}}
{"jsonrpc":"2.0","id":3,"method":"room.join","params":{"node":"<node句柄>","invite":"bugg1-<房间邀请码>","transport":"auto"}}
{"jsonrpc":"2.0","id":4,"method":"room.close","params":{"session":"<session句柄>"}}
```

访客启动后将结果中的本机回环地址交给 Minecraft。`transport:"auto"` 先尝试 WebRTC 直连，失败时在连接开始前选择 SSH 中转。已建立的 TCP 游戏连接若 P2P 路径中断会断开；玩家重连后可使用备用中转，不会伪称为透明迁移。直连会使同一房间对端获知网络地址，重视地址隐私时指定 `transport:"relay"`。端到端直连受 NAT 和防火墙影响，服务器 UDP 地址探测不是 TURN 中继，也不保证所有网络均能打洞。
