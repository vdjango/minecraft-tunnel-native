# 性能测试

游戏转发由 C++ 节点核心和原生桥执行。性能取决于 CPU、加密算法、网络、同时连接数和 Minecraft 活动；发布包通过测试不等于某台服务器可以承载固定数量的玩家。

## 测量方式

比较实现或配置时，应固定源码版本、构建参数、加密算法、压缩策略、连接数和数据量，至少运行三轮，记录中位数和范围。

| 测试 | 关注指标 | 入口 |
| --- | --- | --- |
| 节点中转 | 已校验应用载荷吞吐、CPU 时间、峰值 RSS、回声 RTT | `scripts/benchmark.py` |
| 持续负载 | 吞吐时间窗口、RTT p50/p99/最大值、数据块最大间隔、断流与 EOF | `scripts/bridge_soak.py` |
| A 建房 / B 加入 | 建房、加入、首次收发及重连耗时 | `scripts/vanilla_player_pair.py` |
| 多玩家原版游戏 | 进服、移动、方块同步、死亡复活、断线后重进 | `scripts/vanilla_multiplayer.py`、`scripts/vanilla_player_pair.py` |
| 联邦故障 | 中间节点退出后目录查询、新房间加入及既有连接 | `tests/test_federation_chain.py`、`tests/test_federation_resilience.py` |

回环吞吐不是物理网卡线速；同机 RTT 不能代表公网延迟。应用载荷字节也不同于包含 SSH、TCP、UDP 和重传的网卡流量。压缩收益应同时记录原始字节、编码字节和 CPU；不能用重复字符串压测推算 Minecraft 的实际节省比例。

## 持续负载

按[构建指南](build.md)安装完整原生运行目录及测试依赖，在 Ubuntu 的仓库根目录运行：

```bash
export BUGGCRAFT_NATIVE_CORE="$PWD/runtime/bin/buggcraft-core"
export BUGGCRAFT_BRIDGE_EXECUTABLE="$PWD/runtime/bin/buggcraft-bridge"

# 30 分钟，3 位访客，每位 3 条不限速大流，另有延迟探测和短连接
.venv/bin/python scripts/bridge_soak.py --seconds 1800 \
  --bulk-streams 3 --bulk-rate 0 --output validation-artifacts/bridge-soak.json
```

该负载不是 Minecraft GUI 游戏。原版游戏验证还需要合法游戏资源、匹配的 Java 和图形环境；应单独检查死亡复活、房间重进、连续游玩和多人同时操作。

运行前检查脚本的 `--help`，使用独立端口和目录，并确认机器有足够资源与带宽。回归必须同时校验数据内容与结束 EOF，不能仅统计平均吞吐；短暂停顿应通过最大延迟、分位数和时间窗口暴露。

## 结果留存

测试报告、测量 JSON、截图、逐玩家日志和排查记录保存在本地 `validation-artifacts/`，不提交到源代码仓库。分享结果时附源码提交、构建参数、机器与依赖版本、测试命令和统计口径，并清理凭据及玩家隐私。

[GitHub Actions](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml)提供 Windows、Ubuntu、macOS ARM64/Intel 的构建与自动回归结果。CI 的短时多连接测试用于发现回归，不能替代公网实测或 30 分钟连续负载。压缩策略与限制见[中转压缩](relay-compression.md)。
