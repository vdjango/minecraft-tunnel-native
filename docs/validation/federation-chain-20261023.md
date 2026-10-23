# 长链成员发现与中间节点故障验证（2026-10-23）

## 行为与边界

测试拓扑严格使用以下邀请方向，H 是中间初始入口，A 与 Q 起初互相没有配置：

```text
A->B->C->D->E->F->G->H<-I<-Z<-K<-M<-L<-N<-O<-P<-Q
```

箭头表示加入时使用的邀请入口。成员发现后，每个节点会独立保存已验证的其他成员地址、公钥和签名目录，后台同步直接选择这些成员。H 不是 A 与 Q 的通信必经节点。

玩家在 Q 查询房间，Q 通过本地目录返回所属 A 的入口；C++ 桥直接与 A 建立并校验 SSH 连接。游戏流量经 A 到房主，或协商玩家之间 P2P。B、C、H 等邀请中间节点不转发这次查询和游戏数据。

目前明确限制为 32 个联邦成员；本轮不承诺无限规模。健康节点之间需要实际网络可达，并且已完成成员发现。如果 H 是唯一的物理通信通道，或者 H 消失时两侧尚未学习对方入口，两侧无法凭缓存恢复一条不存在的网络路径。房间本身依赖的所属节点失效时，也不能无缝迁移其活动中继流。

## 修改

- `bridge/routing.cpp`：加入房间先取归属，再直连所属节点。目录故障时最多使用 3 个已缓存入口，不递归跟随转发。只读目录请求可切换入口，成功的健康入口保留给后续操作；写操作不自动重放。
- `web/tunnel/federation_lookup.py`：好友查询优先读取已验证的公开目录，命中不发节点间 RPC。未命中最多并发直查 2 个成员；同一节点最多一批、启动间隔至少 1 秒，相同请求合并，未命中缓存 2 秒。完整账号 ID 优先直查 home。远端只从本地回答，不继续转发。
- `web/tunnel/federation.py`：账号 RPC 复用同一个固定公钥连接池，与心跳共用最多 3 条出站连接；补查只保存双签名公开头信息，未增加账号密文副本。
- `bridge/stream.cpp`：SSH CLOSE 到达后，继续送完已解压且尚未写入游戏 socket 的尾部，不能立即销毁流。旧二进制的慢读压缩回归实际截断了 4,200,000 字节中的尾部；回归断言要求全部数据与 EOF 完整。

成员同步仍采用 0.8–1.2 秒随机调度、最多 3 个在途交换、2–60 秒失败退避和 30 秒签名公告租约。故障节点过期不删除其他成员，也不触发级联退出。好友公开目录最终一致；刚创建但尚未传播的账号可能需要稍后重试，同名项仍返回各自的完整账号 ID。

日志包括 `room_owner_resolved`、`directory_unavailable`、`directory_failover`、`room_directory_unavailable`，不输出地址凭据或房间密钥。解析发生在会话建立前，写入桥的 `bridge/runtime.jsonl`；游戏流量、EOF、背压等继续按玩家和会话保存，可通过 room 字段关联。

## 验证方法

`tests/test_federation_chain.py` 启动 17 个真实 C++ SSH 节点及 C++ 桥，创建 A/Q 的用户签名身份，并执行：

1. 按图逐个配置邀请，等待全员发现和公开账号目录传播。
2. Q 通过自己的入口发现并加入 A 的房间，验证 Q 不创建额外中转房间。
3. 强杀 H 的 C++ 核心并停止其管理心跳，持续校验 64 KiB 双向数据块超过 30 秒租约。
4. 要求 16 个幸存节点互相可见、后台任务存活、每节点连接缓存和在途交换不超过 3。
5. A、Q 各自通过真实 SSH 查询对方好友，要求远程账号补查次数为 0。
6. H 失联后在 A 创建新房间，等待公开信息传播，要求 Q 直接加入并传输完整数据。
7. 使用失联前缓存的 H 玩家入口查询房间，切换至健康备用目录，再加入 A 的房间。后续操作不能重新等待 H。

`tests/test_federation_lookup.py` 覆盖 32 个已知节点、本地命中、home 直查、签名拒绝、同名处理、封禁、200 个不同未命中请求限流、40 个同名请求合并、调用者取消、退出联邦清理和失败补查。`tests/test_bridge.py` 的压缩尾部回归覆盖连续短连接、跨多个解压块的尾部及慢读 EOF。

本轮网络数据是回声校验载荷，未启动新的原版 Minecraft 客户端。两平台均在单机随机端口运行多进程；测得的延迟不能当作跨公网 RTT。既有 32 节点同时失去 4 个成员的验证另见 [心跳与联邦验证](heartbeat-federation-20261023.md)。

## 已完成的测量

以下为最终长链复测，原始值保存在 [双平台测量 JSON](../measurements/federation-chain-20261023.json)。

| 指标 | Ubuntu | Windows |
| --- | --- | --- |
| 全 17 节点及账号目录发现 | 5.123 秒 | 5.422 秒 |
| H 强杀后观察 | 38.583 秒 | 41.079 秒 |
| 幸存且互相可见 | 16 / 16 | 16 / 16 |
| 连续数据块校验 | 1,516 块，94.75 MiB | 1,336 块，83.5 MiB |
| 64 KiB 回声 RTT p99 / 最大值 | 8.851 / 41.570 ms | 14.686 / 32.138 ms |
| 好友 SSH 请求最大耗时 | 48.728 ms | 11.963 ms |
| 已同步好友查询触发远程补查 | 0 | 0 |
| H 故障后创建并加入 A 新房间 | 通过 | 通过 |
| 已缓存的 H 入口故障切换 | 通过 | 通过 |
| 首次故障入口切换耗时 | 0.053 秒 | 4.012 秒 |
| 已切换后后续新加入耗时 | 0.098 秒 | 0.015 秒 |

Windows 首次访问失效 H 需要约 4 秒确认 SSH 连接失败，成功切换后缓存健康入口；后续加入没有重复等待 H。好友请求耗时包含 C++ 桥到目录的 SSH 建连与读取，不是本地查表 CPU 时间。`min_live_during_lease=16` 表明本轮仅 H 的租约过期，没有健康成员同时过期。

最终证据与早期失败均保留：

- Ubuntu 长链、账号补查和压缩 EOF：`validation-artifacts/resilience-20261023T120512-325350800/`，11 项通过，46.201 秒；含构建、控制台、测量和逐玩家日志。
- Ubuntu 最终节点/桥/故障/预算回归：`validation-artifacts/resilience-20261023T120841-314784100/`，28 项通过，150.594 秒；协议兼容 40 项通过，13.437 秒，CTest 通过。
- Windows 最终长链、压缩 EOF 和 CTest：`validation-artifacts/chain-windows-verified.log`，原始 JSON 为 `chain-windows-verified.json`，逐玩家日志位于 `chain-windows-verified-player-logs/`。
- Windows 原生节点与 3 节点强杀/退出回归：`validation-artifacts/chain-windows-final.log`，13 项通过；其中长链为缓存优化和压缩 EOF 修复前的运行，最终值以上一项为准。
- Windows 公开目录及连接池预算：`validation-artifacts/chain-lookup-budget-windows.log`，18 项通过。协议兼容：`chain-windows-compatibility.log`，40 项通过。C++ 单测 70 项通过。
- 压缩尾部问题旧二进制复现：`validation-artifacts/compressed-eof-before-fix.log`，慢读时只收到 3,932,160 / 4,200,000 字节；修复后完整桥回归 `compressed-eof-after-fix.log` 7 项通过。
- 压缩边界载荷按精确长度生成后的 Windows 专项：`validation-artifacts/compressed-eof-exact-boundaries.log` 通过。
- 早期 Windows 公开目录传播等待错误留在 `chain-windows.log`；后续测试改为等待完整公开信息。Ubuntu 在 `resilience-20261023T115431-608780400/` 捕获压缩尾部截断，修复后的结果为最终记录。

## 复现

```bash
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
python -m unittest tests.test_federation_lookup tests.test_federation_budget tests.test_federation_chain -v
python -m unittest tests.test_bridge.BridgeTests.test_compressed_tail_drains_after_remote_channel_close -v
python scripts/check_compatibility.py
```

Windows 需要把构建依赖的 DLL 目录加入 PATH。使用 `BUGGCRAFT_BRIDGE_SOAK_LOG_DIR` 留存逐玩家日志，`BUGGCRAFT_CHAIN_REPORT` 指定测量 JSON。远程隔离验证使用 `python scripts/remote_resilience_validate.py --phase chain`；只操作 `/tmp/buggcraft-native-validation`，没有部署正式节点。
