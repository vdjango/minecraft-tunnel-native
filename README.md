<div align="center">

# Minecraft Tunnel Native

**搭建自己的 Minecraft 联机节点，和朋友一起玩原版游戏。**

[![CI](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml/badge.svg)](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml)
[![C++20](https://img.shields.io/badge/C%2B%2B-20-00599C)](CMakeLists.txt)
[![AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

[安装节点](#安装节点) · [玩家怎么用](#玩家怎么用) · [性能](#性能) · [开发文档](#开发文档)

</div>

为 Minecraft Java 版提供房间联机、P2P 直连和加密中转，**游戏无需安装 Mod**。节点主要运行在 Ubuntu，客户端原生桥支持 Windows、macOS 和 Linux。

- **自动连接**：优先尝试玩家直连，失败时使用节点中转。
- **异常恢复**：检测失效连接，保留健康链路；网络恢复后尝试重新直连。
- **节省带宽**：中转使用自适应压缩，收益不足时自动停止压缩。
- **节点联邦**：多个节点共享房间目录，直接找到房主节点；不设成员数量上限。
- **网页管理**：管理房间、用户、节点网络和问题反馈，保留逐玩家诊断日志。

## 安装节点

**Ubuntu 24.04，amd64**：从 [Actions 的成功构建](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml) 下载 `buggcraft-node-ubuntu24.04-amd64`，解压后将 `.deb` 文件放到服务器，执行：

```bash
sudo apt install ./buggcraft-node_*_amd64.deb
sudo buggcraft-node setup
```

打开命令显示的 HTTPS 链接，把 `SERVER_IP` 换成服务器地址，核对证书指纹，然后在网页设置节点名称、地址和管理员密码。安装包自动设置服务和开机自启，后续直接使用网页管理。

默认需要放行 **TCP/UDP 3333** 和 **TCP 18443**。升级、备份及其他部署方式见 [部署指南](docs/deployment.md)。当前通过安装包分发，尚未提供可直接执行 `apt install buggcraft-node` 的在线 APT 软件源。

## 玩家怎么用

1. 在节点网页领取自己的 **启动器接入码**，添加到支持本项目的启动器。
2. 房主打开 Minecraft 世界并开放到局域网，在启动器创建房间。
3. 朋友在启动器加入房间，再用原版游戏连接启动器显示的本地地址。

启动器接入码用于添加节点，房间邀请码用于加入房间。请保管好个人接入码。

## 性能

游戏传输由 **C++20 原生核心与原生桥**处理；Python 负责网页、账号和房间目录业务。

| 已完成的项目自测 | 结果 |
| --- | --- |
| Ubuntu 回环、32 条中转流，三轮中位数 | 174.93 MiB/s，为原 Python 转发的 2.02 倍 |
| 30 分钟连续回显压力测试 | 校验 186.314 GiB，平均 105.992 MiB/s |
| 4 个原版客户端短测 | 完成进服、移动、交互和重进；该场景应用字节节省 6.13% |

以上为对应版本与负载下的自测，附原始数据；不是第三方认证，也不能换算为公网玩家容量。详见 [测试环境、方法与测量记录](docs/performance.md)。联邦成员没有固定数量限制，实际规模仍取决于资源和目录传播速度。

## 开发文档

[跨平台构建](docs/build.md) · [启动器动态库接入](docs/bridge-library.md) · [协议](PROTOCOL_STANDARD.md) · [压缩](docs/relay-compression.md) · [心跳与重连验证](docs/validation/peer-recovery-20261024.md) · [联邦与安装验证](docs/validation/federation-install-20261024.md)

原生桥提供 DLL / so / dylib 的 C ABI，也保留独立进程接口，启动器无需实现游戏转发。分发时应保留完整原生运行目录；平台应用控制与代码签名情况见构建文档。

本项目采用 [AGPL-3.0-or-later](LICENSE)，第三方声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
