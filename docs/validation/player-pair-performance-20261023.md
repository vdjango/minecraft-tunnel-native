# A 建房、B 加入：性能与延迟验证（2026-10-23）

已实际运行两名原版 Minecraft 1.21.8 GUI 玩家，A 创建 C++ 房间，B 经 Windows C++ 桥加入，通过隔离的 Ubuntu C++ 节点中转。移动、破坏/放置方块、相互接收操作消息，以及 B 连续三次死亡复活均通过，测试期间两名玩家没有意外断线。另在 Ubuntu 上完成 P2P、中转的独立进程满载测量，并修复本轮发现的拆包卡死问题。

## 原版双人游戏

| 项目 | 实测 |
| --- | ---: |
| A 创建房间 API 耗时 | 314.328 ms |
| B 加入房间 API 耗时 | 551.238 ms |
| B 在游戏期间的状态 ping RTT，中位 / P99 / 最大 | 49.650 / 67.261 / 102.739 ms |
| A 本地直连状态 ping RTT，中位 / P99 | 0.189 / 0.273 ms |
| 游戏服务端上报 B 的玩家 ping，三个更新值 | 24 / 45 / 60 ms |
| 每名玩家状态 ping 样本 | 145 |
| B 死亡后成功复活 | 3 / 3，服务端生命值恢复至 20 |
| 原版游戏已压缩数据上，桥压缩额外减少的载荷 | 4.26% |

这里的建房/加入时间是原生桥 API 的耗时，不包含启动 Java、加载资源和进入世界的总时间。状态 ping 使用 Minecraft 状态协议；游戏玩家 ping 来自服务端 keepalive 统计，两者不能当作同一种指标。

网络路径为 Windows → Ubuntu → Windows 中转。临时节点公网端口探测未能连通，因此本次使用已有 SSH 管理连接转发到测试节点，数字包含额外的转发开销，不能作为生产节点直接公网连接的延迟承诺。

两名玩家均使用未修改的 Mojang GUI 客户端，没有用协议机器人代替玩家。另有一个本地观察器核对移动和 `stone → air → dirt` 更新，并收集服务端玩家 ping。A 机器运行独立原版服务端，A 客户端本地进入世界；本次没有操作内置服务端的“对局域网开放”菜单。观察持续约 100 秒，其间还持续创建短连接进行状态探测。复活自动操作约 2 秒，包含按键、鼠标和健康值轮询等待，不能解释为网络卡顿时间。

压缩值为 C++ 桥关闭流时上报的原始载荷 197,616 字节、编码后 189,199 字节，不含 SSH/TCP/IP 头部。原版游戏本身启用了 256 字节压缩阈值，二次压缩收益有限；本轮结果不支持宣称游戏带宽可以减少几倍。

原始报告和逐玩家日志：

- [游戏测量摘要](../measurements/vanilla-player-pair-20261023.json)
- [本次完整报告](../../validation-artifacts/vanilla-pair-20261023-123125/report.json)
- [A 客户端日志](../../validation-artifacts/vanilla-pair-20261023-123125/PairHostA/logs/latest.log)
- [B 客户端日志](../../validation-artifacts/vanilla-pair-20261023-123125/PairGuestB/logs/latest.log)
- A、B 的桥日志分别位于同次目录的 `PairHostA-bridge/players/<player>/*.jsonl`、`PairGuestB-bridge/players/<player>/*.jsonl`，节点日志位于 `node-players/<player>/*.jsonl`。

游戏验证使用修复前 Windows 可执行文件；其 SHA-256 与当时 Windows 配对基准一致，已写入测量摘要。下面的 Ubuntu 数据使用修复后的原生桥。

## Ubuntu 原生桥配对基准

两个独立 C++ 桥进程模拟 A 和 B，连接真实 C++ 节点和 TCP 回声应用。每种模式反复建房/加入 5 次；空闲采样 150 次；满载持续 30 秒，B 同时运行 4 条不限制速率的 64 KiB 数据流，每流最多 16 块在途，另设一条约 130 字节的小包探测连接。逐块检查内容和序号，结束时检查 EOF。

| 项目 | P2P | 中转 |
| --- | ---: | ---: |
| A 建房耗时，中位 | 45.040 ms | 44.711 ms |
| B 加入耗时，中位 | 95.228 ms | 88.607 ms |
| 首条新连接收到首个回包，中位 | 1.048 ms | 1.314 ms |
| 空闲小包 RTT，中位 | 0.607 ms | 0.410 ms |
| 满载小包 RTT，P99 | 39.785 ms | 28.018 ms |
| 满载小包 RTT，最大 | 47.307 ms | 32.553 ms |
| 单方向有效载荷吞吐 | 26.957 MiB/s | 173.348 MiB/s |
| 超过 100 / 250 / 1000 ms 的数据接收间隔 | 0 / 0 / 0 | 0 / 0 / 0 |

两种模式均完整校验数据，无丢字节、乱序或连接异常。修复后另一次独立复测也通过：P2P 26.885 MiB/s、满载 P99 41.994 ms；中转 173.174 MiB/s、满载 P99 29.095 ms。

这些进程位于同一台 Ubuntu 机器，P2P 使用本地 ICE 候选，不能据此证明异地 NAT 穿透成功率或公网物理带宽。吞吐按通过回声校验的单方向字节计算，回声还会产生等量反向流量；不是节点网卡线速，也不是游戏帧率。该环境中 P2P 满载吞吐明显低于中转，尚不能宣称 P2P 在所有负载下性能更高。两轮配对基准不是一次新的 30 分钟持续压测。

- [最终基准 JSON，含可执行文件 SHA-256](../measurements/player-pair-ubuntu-20261023.json)
- [修复后的另一轮测量](../../validation-artifacts/player-pair-ubuntu-20261023/fixed.json)
- 逐玩家日志：`validation-artifacts/player-pair-ubuntu-20261023/players-final/players-*/bridge-{1,2}/<player>/*.jsonl`，以及同级 `node/<player>/*.jsonl`。

## 本轮修复：完整帧前停止接收

Windows 第一轮中转满载时，一条流发出 1 MiB 后等不到回包，其他三条流持续正常传输。节点和房主日志显示已经处理该流，访客日志始终是接收 0 字节。

原因在 `bridge/stream.cpp`：解码器缓存达到 65,536 字节后便停止继续读 SSH。然而一个不可压缩的最大帧是 65,536 字节载荷加 3 字节长度头。握手单独到达、随后数据拆成 65,536 + 3 字节时，解码器既没有完整帧可输出，也不能继续读最后 3 字节，连接永久等待。

现在按解码器实际剩余容量限制读取，保留原有 131,100 字节接收预算；原始透传的单次解码输出也限制在 64 KiB，保持套接字缓冲预算。协议、压缩协商和回退规则保持兼容。

新增真实节点/原生桥回归用例，强制分开发送握手、65,536 字节和最后 3 字节，并在远端不提前关闭的情况下检查回包。旧程序稳定在 2 秒断言超时；修复后的 Ubuntu 原生桥通过。同轮验证还包含压缩 EOF 尾包、原生 C++ 边界检查和现有桥回归。

- [旧程序的稳定复现](../../validation-artifacts/pair-frame-before.log)
- [修复后的边界和 EOF 回归](../../validation-artifacts/player-pair-ubuntu-20261023/regression-fixed.log)
- [Ubuntu CTest](../../validation-artifacts/player-pair-ubuntu-20261023/ctest-fixed.log)：72 项原生 C++ 用例通过。
- [桥及压缩回归](../../validation-artifacts/player-pair-ubuntu-20261023/bridge-sdk-complete.log)：14 项通过，包括三玩家连续负载、非正常退出、心跳、压缩协商回退和 EOF。
- [协议兼容回归](../../validation-artifacts/player-pair-ubuntu-20261023/compatibility-final.log)：40 项通过。

Windows Release 编译成功，但新 `buggcraft-bridge.exe` 和单元测试程序执行被本机 Windows Application Control 拦截（WinError 4551；CodeIntegrity 事件 3077，企业签名策略要求）。因此修复后 Windows 的运行复验未完成，不能用编译成功代替运行通过。未修改系统应用控制策略。证据和新二进制散列保存在 [Windows 复验状态](../measurements/player-pair-windows-fixed-20261023.json)。macOS 本轮未执行。

## 复现

构建原生核心和桥，安装节点测试依赖后：

```bash
ctest --test-dir build --output-on-failure
python -m unittest tests.test_bridge.BridgeTests.test_fragmented_maximum_raw_frame_keeps_receiving -v
BUGGCRAFT_PAIR_SECONDS=30 BUGGCRAFT_PAIR_RESULT=pair.json \
BUGGCRAFT_BRIDGE_SOAK_LOG_DIR=pair-players \
python -m unittest tests.test_player_pair.PlayerPairTests.test_pair_performance -v
```

原版 GUI 验证需要本工作区已有的 Mojang 1.21.8、Java 21、Minecraft 服务端、`minecraft_launcher_lib`、Node/mineflayer 和 SSH 测试连接器。在 Windows 上执行 `python scripts/vanilla_player_pair.py`。脚本通过原生桥 JSON-RPC 编排通讯，游戏传输由 C++ 桥/节点处理；所有新世界、客户端目录和日志独立创建，只关闭本次启动的进程。默认不截取桌面，避免把 IDE 中的内容录入验证材料。
