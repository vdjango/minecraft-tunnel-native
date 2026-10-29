# 协议文档

先从[协议索引](../../PROTOCOL_STANDARD.md)选择需要接入的层。多数启动器只需要本机 JSON-RPC 桥；SSH 目录、节点联邦和游戏传输由原生组件处理。

| 阅读目标 | 文档 |
|---|---|
| 了解组件、连接边界和凭据 | [协议概览](overview.md) |
| 集成第三方启动器 | [启动器桥接协议](launcher-bridge.md) |
| 同步游戏版本、加载器与模组 | [游戏环境与模组同步](game-metadata.md) |
| 查询/实现节点房间目录 | [房间目录协议](directory.md) |
| 理解节点联邦与心跳 | [联邦与存活状态](federation.md) |
| 理解 P2P、中继和心跳 | [游戏传输协议](transports.md) |
| 了解断线恢复和旧版本边界 | [连接恢复与兼容](compatibility.md) |
| 调用账号网页登录接口 | [网页授权 API](web-api.md) |
| 实施节点本地管理操作 | [管理权限与流程](administration.md) |
| 选择语言并查看桥接示例 | [语言示例](examples.md) |

## 接入路径

```text
Java / C / C++ / C# / Python 启动器
                 │
                 ├── JSON-RPC 2.0 over stdio
                 └── C ABI 动态库（C、C++、C#、Python 等）
                              │
                       本机原生桥
                ┌─────────────┴─────────────┐
          SSH 控制/中继                 WebRTC P2P
                │                         DTLS/SCTP
                └──── Minecraft TCP 流 ────┘
```

跨语言可移植的边界是 JSON-RPC 方法和参数。不要让客户端依赖节点内部 JSON、SSH 用户名、心跳帧或 DataChannel 编号；这些属于组件实现细节，可能随协议版本演进。

节点开发者另读[开发指南](../development.md)、[安全边界](../security.md)和[联机流程图](../launcher-federation-flow.md)。HTTP 状态、全网人数与部分失败见[网页 API](web-api.md)；代理自动刷新见[部署指南](../deployment.md)。仓库文档更新不会自动替换已部署节点的 `/developers` 页面。
