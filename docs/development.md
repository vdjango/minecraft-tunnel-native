# 节点开发指南

本文按 2026-10-28 的仓库实现整理。源码、线上节点和启动器所带桥可能不是同一版本，排错时应同时记录节点部署版本、`buggcraft-bridge --version` 和 `bridge.initialize` 返回的能力。

## 从哪里修改

| 需求 | 主要实现 | 同步维护的契约 |
|---|---|---|
| 启动器方法、参数和错误码 | `bridge/rpc.cpp`、`include/buggcraft/bridge.h` | [桥接协议](protocol/launcher-bridge.md)、[C ABI](bridge-library.md) |
| 房间、备用节点与重连 | `bridge/session.cpp`、`bridge/route_selector.cpp` | [恢复与兼容](protocol/compatibility.md)、[流程图](launcher-federation-flow.md) |
| 环境信息、JAR 清单和下载 | `bridge/modpack.cpp`、`web/tunnel/directory.py` | [游戏与模组](protocol/game-metadata.md) |
| SSH 权限、转发与限额 | `core/`、`web/tunnel/relay.py`、`web/tunnel/capacity.py` | [目录协议](protocol/directory.md)、[传输协议](protocol/transports.md)、[安全说明](security.md) |
| 成员签名、目录同步和全网在线 | `web/tunnel/federation.py` | [联邦协议](protocol/federation.md)、[网页 API](protocol/web-api.md) |
| 网页、登录与反向代理 | `web/tunnel/portal.py`、`web/tunnel/web/` | [管理权限](protocol/administration.md)、[部署指南](deployment.md) |
| 会话采样和日志回放 | `web/tunnel/diagnostics.py`、`web/tunnel/web/diagnostics.js` | 下面的诊断流程与指标口径 |

服务入口为 `web/main.py`，Python 负责业务控制，C++ 核心负责 SSH/STUN 和原生转发，启动器通过本机桥连接。HTTP 管理 API 不是启动器建房接口；本机桥不提供公网 RPC。整体入口见[协议索引](../PROTOCOL_STANDARD.md)。

## 本轮实现变化

- 网页按账号/管理员会话渲染首屏；新增页面须正确处理登录表单和受保护面板，不能先显示登录再靠异步请求纠正。管理导航仅在管理员区域提供。
- `game` 同步版本、协议、加载器和模组摘要，资源包/光影使用可选的资源清单。版本与加载器由启动器提供，桥扫描内容；公开预览和完整清单是不同接口。整合包与房间同步约定、下载源优先和 P2P 下载限制见[游戏环境与内容同步](protocol/game-metadata.md)。
- 在线总数为有效联邦公告中的房间桥会话数，包含私密房间；详情为认证后的有界查询。总数、列表长度、实际游戏人数不是同一指标。
- Cloudflare 网段启动后异步获取，之后每 24 小时刷新；失败保留缓存。手工设置只用于额外可信代理。
- 活动会话在 SSH 故障后按创建时的候选尝试重建，有限次失败后停止；没有一致性选主或 TCP 状态复制。
- 房主桥检查 Minecraft 首个握手，不能据此认证“官方二进制”或保证所有后续流量安全。

## 诊断按连接阶段查看

在实际承载房间的节点登录 `/diagnostics`。全网在线列表可跨节点聚合，诊断日志只属于本节点；从另一入口看到玩家，不代表本机有其日志。

1. 没有认证会话：检查接入码、固定 SSH 公钥、TLS 证书、节点端口和容量。网页可访问不代表桥已连接。
2. 房间等待：核对房主是否开放世界、`room.retarget` 的端口与 `availability`。桥在线不等于世界可用。
3. 有会话无游戏流：确认 Minecraft 连接桥返回的回环地址，检查游戏启动和本地监听。
4. 有流但失败：结合 `stream_rejected`、`host_connect_failed`、EOF、背压、断开事件与房主桥 stderr。原生桥拒绝的握手不一定记录在 Python 节点日志中。
5. P2P 故障：对照 `peer_heartbeat_timeout`、`peer_fallback`、`peer_retry_started` 和实际路径。P2P 游戏流不经节点，节点转发为零可以正常。
6. SSH/节点失联：观察 `reconnecting`、备用入口与最终 `backup-unavailable`；确认房主、访客到达同一房间归属节点，并提供明确的重新探测入口。

在线详情的路径/RTT 超过 20 秒未更新时显示 unknown/空值，不能显示成 0 ms。日志可能截断或轮转，应保留 `truncated`、丢弃事件数和写盘失败数；公开导出前清理敏感信息。字段口径见[网页 API](protocol/web-api.md)。

## 本地验证

Python 3.11+，依赖和原生编译见[构建指南](build.md)。安装根目录 `requirements.txt` 后，在仓库根运行无需生产凭据的回归：

```bash
python -m unittest tests.test_profile_exchange tests.test_network_presence tests.test_relay_handshake tests.test_federation_scale tests.test_federation_budget -v
```

需要原生程序的测试使用实际构建产物；跨平台路径和环境变量参照 [CI](../.github/workflows/core.yml)。跳过的测试不能记作通过。

安装 pytest 后可用 `python -m pytest` 执行当前 `tests/` 完整套件。`pytest.ini` 固定默认收集目录，避免 `compatibility/tests` 的同名 `tests` 包互相遮蔽。协议兼容套件另用 `python scripts/check_compatibility.py` 在独立进程运行，仍调用实际 C++ 核心；不能因为默认不收集兼容目录就跳过兼容验收。

```bash
cmake --build build --config Release --parallel
ctest --test-dir build -C Release --output-on-failure
```

转发或恢复修改还需验证实际房主/访客数据交换、P2P、仅中继、单节点失联、全部备用失效和错误凭据。不要对生产节点运行破坏性故障测试。

## 文档与发布同步

字段变更应同时更新对应桥接/目录/联邦文档；HTTP 字段注明认证、范围、空值、缓存与部分失败。新字段优先可选，旧节点不支持时显式降级或拒绝。发布同步入口：

1. [README](../README.md)：能力、开发入口、部署与边界。
2. [协议索引](../PROTOCOL_STANDARD.md)与[分层目录](protocol/README.md)：字段、示例和兼容说明。
3. `frontend/src/pages/developers.vue`：实际网页开发文档正文；`web/tunnel/web/developers.html` 为部署外壳，`vue-page-developers.js` 为构建后的正文。修改 Vue 后在 `frontend/` 运行 `npm run build` 与 `npm test`，发布时携带构建资源；修改 Markdown 不会自动更新网页说明与示例。
4. [部署指南](deployment.md)、[安全说明](security.md)与[流程图](launcher-federation-flow.md)：配置和恢复限制。

文档日期不代表已部署。网页静态文件仍须走发布流程；分层 Markdown 应随源码提交。历史验收记录不能证明后续修改已经测试或上线。
