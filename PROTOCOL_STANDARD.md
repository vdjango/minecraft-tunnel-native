# Buggcraft 协议索引

这份索引按连接层说明当前实现中的协议。启动器集成应优先使用本机原生桥的 JSON-RPC 接口；节点目录 SSH、联邦同步和游戏数据通道属于桥的内部实现，不需要在启动器里重写。

文档于 2026-10-25 对照当前源码整理。节点代码职责和本地验证见[开发指南](docs/development.md)；线上或旧版桥应按运行时能力判断，不应只按本文日期判断。
准备房间、玩家进度、管理权和安装日志授权见[房间控制协议](docs/room-lobby.md)。模组、可选资源包和光影与 `.buggpack` 的统一字段、下载源优先及 P2P 文件兜底规则见[游戏环境与内容同步](docs/game-metadata.md)。网页 `/developers` 同步提供这些流程的接入示例。

## 用户主动提交诊断

`feedback.submit` 接受 `node,archive,metadata,consent:true`。房主和房客使用相同接口，但都必须先登录个人账号，并由用户明确确认本次上传。桥读取最多 20 MiB 的本地 ZIP，走独立的 `buggcraft-feedback-upload-v1` SSH 子系统；节点保存私有附件并返回问题编号、字节数及 SHA-256，桥验证回执后才报告成功。匿名访问不能提交，附件不能通过公开 URL 下载；管理员或有授权的联邦节点读取时仍要验证身份。客户端取消确认不会收集或发送日志。

诊断上传与游戏流量使用独立通道。节点 C++ 控制通道支持读取暂停与恢复；磁盘写入较慢时暂停单个上传通道，不阻塞同一连接的房间控制和其他玩家游戏。故障回归覆盖超限、截断、无授权、真实桥上传、附件校验及慢磁盘并行控制请求，见 `compatibility/tests/test_feedback_native.py`。

## 传输与身份

## 按层阅读

| 层 | 协议 | 适用场景 | 文档 |
|---|---|---|---|
| 启动器接口 | JSON-RPC 2.0 over stdio；也可经 C ABI 调用同一组方法 | 第三方启动器添加节点、创建或加入房间、读取状态 | [启动器桥接协议](docs/protocol/launcher-bridge.md) |
| 游戏环境 | 房间 `game` 与独立模组文件通道 | 版本/加载器元数据、模组预览、完整清单分页与下载 | [游戏环境与模组同步](docs/protocol/game-metadata.md) |
| 节点目录 | SSH 子系统 `buggcraft-rooms-v1`，UTF-8 JSON Lines | 实现节点服务端、排查目录接口 | [房间目录协议](docs/protocol/directory.md) |
| 联邦控制 | SSH 上的签名公告、目录同步与存活探测 | 节点互联、联邦扩展和故障排查 | [联邦与存活状态](docs/protocol/federation.md) |
| 游戏数据 | WebRTC DTLS/SCTP 直连；无法直连时可走 SSH 中继 | 理解心跳、路径回退与恢复边界 | [游戏传输协议](docs/protocol/transports.md) |
| 版本兼容 | 备用入口、故障恢复和旧版本限制 | 设计断线与重连体验 | [连接恢复与兼容](docs/protocol/compatibility.md) |
| 网页 API | HTTPS JSON | 账号注册、登录、备份和节点授权 | [网页授权接口](docs/protocol/web-api.md) |
| 管理 API | HTTPS 管理会话与 CSRF 校验 | 节点管理员操作和隔离策略 | [管理权限与流程](docs/protocol/administration.md) |

协议的背景和信任边界见[概览](docs/protocol/overview.md)。Java、C、C++、C# 与 Python 的启动器接入示例见[语言示例](docs/protocol/examples.md)，浏览器版示例也可在 [`/developers#examples`](https://node.bugg.icu/developers#examples) 查看。

## 推荐接入顺序

1. 下载并随应用分发对应平台的原生桥完整目录。
2. 通过子进程管道发送 JSON-RPC，或直接加载动态库的 C ABI；不要自行实现 SSH、ICE 或 P2P 传输。
3. 调用 `bridge.initialize` 并检查运行时返回的 `methods` 和 `limits`。
4. 调用 `node.add`，保存进程内返回的节点句柄，再创建或加入房间。
5. 将房间返回的 `127.0.0.1:端口` 交给 Minecraft；退出时调用 `room.close`。

JSON-RPC 的完整能力以运行时 `bridge.initialize` 响应为准。接入码、邀请码和账号材料都属于凭据，不能放在命令行、URL、日志或公开配置中。

## 本轮易误用的边界

- 公开房间目录预览不是完整模组清单；本节点列表可能省略预览，版本与加载器须由启动器提供。
- 在线总数覆盖存活联邦节点的桥会话，`game_players`、容量和路径分布仍属本节点；详情允许部分失败。
- 缓存备用入口不等于房间状态复制。全部候选失败会停止重试，现有 Minecraft TCP 不能跨节点迁移。
- 后台入口隐藏不替代鉴权，Minecraft 握手检查不构成完整包过滤或官方二进制认证。
