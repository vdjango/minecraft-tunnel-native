# 性能与验证

本页索引项目公开的性能证据。数据来自项目自测，未经过独立第三方认证；报告分别记录测量日期、环境、负载、口径及可用的源码或二进制摘要。历史结果不能自动归属于后续提交。

## 节点转发对比

环境：Ubuntu 24.04 LTS x86_64、4 vCPU、约 3.7 GiB RAM，Release 构建；libssh 0.12.2、OpenSSL 3.0.13、Python 3.12.3、AsyncSSH 2.24.1、cryptography 50.0.2。测量环境使用 psutil 7.2.2，复现时应记录实际依赖版本。

方法：在同机回环网络运行独立节点和负载发生器，32 条流分布到 4 个房间，每流发送 32 MiB 并接收回显。两种实现固定使用 AES-128-GCM，关闭 SSH 压缩和游戏流 Zstandard，逐流校验长度、SHA-256 和 EOF。原生节点采用 4 个工作线程、每方向 64 KiB 环形缓冲。

服务器 CPU/RSS 包含 Python 管理进程和原生子进程，不包含发生器；出口吞吐为应用载荷，上传及回显均经过节点出口，共 2 GiB。三轮结果：

| 指标 | Python 中位数 | 原生中位数 |
| --- | ---: | ---: |
| 完成时间 | 23.6938 s | 11.7077 s |
| 累计 CPU 时间 | 11.81 s | 8.19 s |
| 应用载荷出口吞吐 | 86.44 MiB/s | 174.93 MiB/s |
| 峰值 RSS | 150.27 MiB | 122.49 MiB |
| 空载回声 RTT 中位数 | 0.604 ms | 0.333 ms |
| 各轮 RTT p95 的中位数 | 0.817 ms | 0.407 ms |

Python 三轮吞吐为 65.33–105.57 MiB/s，原生为 172.28–175.87 MiB/s。不能把回环测量当作物理网卡线速、公网延迟或核心极限吞吐。单流时原生总 RSS 为 51.27 MiB，Python 为 45.01 MiB，不能宣称所有负载下都更省内存。

证据：[方法与环境](validation-20261023.md#ubuntu-性能对比)、[首轮 1/8/32 流](measurements/ubuntu-aes-gcm.json)、[复测 1](measurements/ubuntu-repeat1.json)、[复测 2](measurements/ubuntu-repeat2.json)、[三轮统计](measurements/ubuntu-32-stream-median.json)。

## 持续负载与真实游戏

| 验证 | 环境与负载 | 结论及证据 |
| --- | --- | --- |
| 30 分钟持续中转 | Ubuntu 回环，1 个节点、1 个房主桥、3 个访客桥，9 条大流和 3 条 RTT 流，3306 轮短连接及控制请求 | 1800.217 s，186.314 GiB 已校验回显，平均 105.992 MiB/s；[报告](validation/bridge-soak-30min-20261023.md)、[JSON](measurements/bridge-soak-30min-20261023.json) |
| 4 个原版 GUI 客户端 | Windows Mojang 1.21.8，经独立 Ubuntu 中转，移动、挖掘、放置、重启访客 | 操作同步与重进通过；[报告](validation-20261023.md#真实原版多人游戏)、[JSON](measurements/vanilla-1.21.8.json) |
| A 建房 / B 加入 | 两个原版 GUI 客户端，经 Ubuntu 中转；含 3 次死亡复活 | 操作与延迟分项记录；[报告](validation/player-pair-performance-20261023.md) |
| 联邦中间节点失联 | 17 节点邀请长链，完成发现后停止中间 H 节点 | 存活节点目录与直查验证；[报告](validation/federation-chain-20261023.md) |
| 联邦移除成员上限后的故障回归 | 65 个真实原生节点共用 4 vCPU 主机，强杀中间 H | 既有传输、新房间和备用入口通过；控制延迟明显增加，不能作为容量承诺；[报告](validation/federation-install-20261024.md) |

30 分钟测试校验了每次回显与结束 EOF，负载阶段未出现意外断流或探测超时；30 秒窗口吞吐为 101.85–108.58 MiB/s，最大探测 RTT 为 26.208 ms，最大大流数据块完成间隔为 30.574 ms。吞吐按收到并校验的回显字节计一次，双向应用负载为 372.629 GiB。

该测试没有运行 Minecraft GUI，不能与短时 GUI 实测合并描述为“30 分钟原版游戏实测”。其日志中存在 `clean_eof=false` 记录，外部完整性与 EOF 断言通过，报告保留了这一关闭时序问题；不能声称所有关闭状态均正常。

配对基准中的同机 P2P 吞吐低于中转，详见对应报告。因此也不能宣称 P2P 在所有环境下都更快，或据回环候选连接成功推算公网 NAT 穿透率。

## 压缩与带宽

四个原版客户端短测在 Minecraft 默认 `network-compression-threshold=256` 下，应用字节从 515,041 降至 483,468，包含 BCZ1 帧头，节省约 6.13%。场景为平坦世界、进服及少量交互，统计还包含观察端与状态探测连接。

这是应用流编码口径，不能当作 SSH/TCP 密文、网卡总流量或账单节省比例。已有压缩、加密、活动类型和数据分块都会影响收益。[压缩实现与限制](relay-compression.md)

## 复现

按 [构建指南](build.md) 安装原生运行目录及 `requirements.txt`，在仓库根目录运行。Ubuntu 示例：

```bash
export BUGGCRAFT_NATIVE_CORE="$PWD/runtime/bin/buggcraft-core"
export BUGGCRAFT_BRIDGE_EXECUTABLE="$PWD/runtime/bin/buggcraft-bridge"

# 对比转发；先检查脚本参数，根据目标环境设置负载并保留三轮结果
.venv/bin/python scripts/benchmark.py --help

# 30 分钟持续负载，9 条不限速大流
.venv/bin/python scripts/bridge_soak.py --seconds 1800 \
  --bulk-streams 3 --bulk-rate 0 --output validation-artifacts/bridge-soak.json
```

运行前确认测试机器有足够资源与带宽，使用独立端口和目录。GUI 实测另需合法原版游戏资源、匹配版本的 Java 和图形环境，不属于上述回显压测。

比较性能时应固定源码与构建参数、加密算法、压缩策略、流数、数据量和游戏活动，至少报告多轮范围。保留程序摘要、逐玩家日志、延迟分位数、吞吐时间窗口和资源采样，避免只展示平均吞吐而掩盖瞬时卡顿。
