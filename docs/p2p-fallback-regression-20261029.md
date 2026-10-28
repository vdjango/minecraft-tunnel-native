# 2026-10-29 P2P 回退与账号路由回归

分支：`fix/p2p-fallback-auth-20261029`。现场分析和 UI 恢复流程见启动器仓库同分支的 `docs/technical/p2p-fallback-investigation-20261029.md`。

## 范围

1. 心跳不再因下一次发送覆盖记录而丢弃迟到回复。16 个固定槽位；拒绝重放和未知编号，过期不能重新续活。超时由 RTT 适应，最低 20、最高 60 秒。
2. 信令短断不关闭健康的 P2P 游戏流；非阻塞重建 rendezvous 通道，仍保留丢包失活回退与自动恢复。
3. 节点切换保留个人凭据和签名身份，不被联邦拓扑里的匿名路由身份覆盖。SSH 发出的 token 不含本地委托签名私钥。
4. 大厅节点延迟取独立目录探测样本；完整查询耗时仍单独保存在 `connect_ms`。

## 测试

| 组 | 检查内容 |
| --- | --- |
| `buggcraft-native-tests` | 98 项通过；6 秒延迟连续回复、跨探测编号回复、超时上限、单向失活、重放、旧端兼容 |
| `tests.test_route_selection` | 9 项；真实 SSH 两节点路由、容量/维护切换、个人凭据跨匿名拓扑、慢 list 不污染 ping、启动器适配 |
| `tests.test_peer_heartbeat` | 进程/共享库各 3 项：空闲 P2P、单向 IPv4 UDP 丢包回退恢复、信令关闭保持现有游戏流与其他玩家 |
| `BridgeServicesTests.test_personal_code_and_native_social_signature` | 真实个人账号委托签名与好友请求 |
| `BridgeTests.test_two_native_processes_mixed_p2p_relay` | 同一房间直连及中转字节流、关闭清理 |

共 17 项集成用例通过。首轮仅启动器适配器因旧模块导入路径报错，修正为 `hookup.profiles` 后单项通过；完整路由组重新运行 9/9 通过；故障注入两种入口共 6 项均通过。启动器对应 UI/控制器回归 83/83 通过。测试入口二进制从独立的 `build-p2p-auth-mingw` 构建，未覆盖发布目录。

## 运行方式

指定 `BUGGCRAFT_NATIVE_CORE`、`BUGGCRAFT_BRIDGE_EXECUTABLE`、`BUGGCRAFT_BRIDGE_LIBRARY` 指向本分支构建产物，`BUGGCRAFT_LAUNCHER_SDK` 指向对应启动器分支的 `src/buggcraft`，使用包含 aiohttp/asyncssh/cryptography 的测试 Python。执行：

```powershell
python -m unittest tests.test_route_selection tests.test_peer_heartbeat tests.test_bridge_services.BridgeServicesTests.test_personal_code_and_native_social_signature tests.test_bridge.BridgeTests.test_two_native_processes_mixed_p2p_relay -v
```

故障注入代理会将目标会话的候选替换为 IPv4 UDP 代理，因此单向丢包恢复用例验证的是 IPv4 数据通道；没有把公网 STUN/NAT 穿透成功混同为本机结果。现场归档已记录双方匹配的 IPv4 srflx 选中地址对，但没有逐条被拒绝的心跳日志，不能归因所有历史超时。

不修改在线节点、不上传用户日志、不改变用户网络/防火墙设置。两位原玩家的公网升级复测仍待新构建。
