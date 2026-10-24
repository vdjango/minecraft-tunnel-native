<div align="center">

# Minecraft Tunnel Native

**让朋友更容易一起玩 Minecraft Java 版。**

[![CI](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml/badge.svg)](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml)
[![Release](https://img.shields.io/github/v/release/vdjango/minecraft-tunnel-native)](https://github.com/vdjango/minecraft-tunnel-native/releases/latest)
[![C++20](https://img.shields.io/badge/C%2B%2B-20-00599C)](CMakeLists.txt)
[![AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

[快速了解](#这是什么) · [安装节点](#安装一个节点) · [玩家联机](#玩家如何联机) · [联邦网络](#节点联邦网络) · [性能和测试](#性能和测试)

</div>

## 这是什么

Minecraft Tunnel Native 是 Minecraft Java 版的联机节点。它把玩家的启动器、节点和 Minecraft 世界连接起来，让朋友可以加入房主开放的世界。

游戏本身不需要安装联机 Mod。玩家使用支持 Buggcraft 的启动器，房主在游戏中把世界开放到局域网，朋友从房间进入后，用启动器显示的地址加入 Minecraft。世界、存档和游戏规则仍由房主掌握。

**玩家只需要启动器；愿意提供联机服务的服主，再安装节点。** 节点网页提供账号、房间、好友和管理功能，Minecraft 数据转发由 C++ 原生核心和原生桥完成。

## 能解决什么问题

- **不在同一个家庭网络也能联机**：房主不需要把 Minecraft 端口直接暴露到公网。
- **优先 P2P 直连**：网络允许时，玩家之间直接传输；无法穿透 NAT 或防火墙时，自动使用加密中转。
- **断线后更容易恢复**：桥会检查 P2P 是否仍然可用。某一位玩家的直连失效时，不会主动关闭房间里其他健康连接；网络恢复后会再次尝试直连。
- **支持原版 Minecraft**：节点不修改世界、存档或权限，游戏规则仍由 Minecraft 和房主决定。
- **节省中转带宽**：中转可以对适合压缩的数据进行自适应压缩；已经压缩或不适合压缩的数据会跳过，避免浪费 CPU。
- **保留玩家日志**：节点按玩家保存连接、重连、流量和错误事件，方便处理“某个人掉线”这类问题。

P2P 是否成功取决于双方网络环境，不能保证所有运营商和路由器都能直连。中转是正常的备用方式，不代表房间不可用。

## 安装一个节点

节点适合安装在 Ubuntu 服务器上。普通服主只需要下载一个 `.deb` 安装包，安装包会创建服务账号、配置目录和开机启动服务。

### 最简单的安装方式

1. 打开 [Release 下载页面](https://github.com/vdjango/minecraft-tunnel-native/releases/latest)，下载 `buggcraft-node_版本号_amd64.deb`。
2. 把安装包放到 Ubuntu 24.04 amd64 服务器，在该目录执行：

```bash
sudo apt install ./buggcraft-node_*_amd64.deb
sudo buggcraft-node setup
```

`setup` 会显示 HTTPS 初始化链接和证书指纹。把链接中的 `SERVER_IP` 换成服务器地址，核对证书后用浏览器完成设置：填写节点名称、服务器地址和管理员密码。以后直接在网页管理，服务会随系统启动。

常用命令：

```bash
sudo buggcraft-node status   # 查看节点是否运行
sudo buggcraft-node logs     # 查看最近日志
sudo systemctl restart buggcraft-node
```

服务器防火墙通常需要放行 **TCP/UDP 3333**（联机与控制、UDP 打洞）和 **TCP 18443**（节点网页）。如果修改过端口，以实际配置为准。

这是使用 `apt` 安装的本地软件包，目前还没有在线 APT 软件源。Release 同时提供校验文件和 Windows、macOS、Linux 原生运行包；其他系统和启动器集成请看[构建文档](docs/build.md)。

### 升级和回退

下载新 `.deb`，使用相同的 `apt install` 命令即可升级。升级会重启节点，请安排在没有玩家在线时操作，并先备份 `/var/lib/buggcraft-node`，保留旧安装包。校验、数据备份和回退步骤见[部署指南](docs/deployment.md)。

## 玩家如何联机

### 房主

1. 在节点网页登录，领取自己的**启动器接入码**。
2. 在支持 Buggcraft 的启动器中添加接入码。
3. 启动 Minecraft，打开要一起玩的世界，并选择“开放到局域网”。
4. 在启动器中创建房间，把**房间邀请码**发给朋友。

### 朋友

1. 使用自己的启动器接入码添加同一个节点，或选择朋友使用的联邦节点。
2. 在房间列表中输入邀请码或选择房间。
3. 启动 Minecraft，连接启动器显示的本地地址。

**启动器接入码**用于添加节点，属于你的个人凭据；**房间邀请码**用于邀请朋友进入某个 Minecraft 世界。大家需要准备兼容的游戏版本；如果使用整合包，还需要自行准备对应的 Mod 和配置。

如果直连失败，原生桥会自动使用节点中转，玩家照常加入房主的世界。中转链路使用加密传输，但节点是中转链路的终点之一，因此应使用你信任的节点。

## 节点联邦网络

多个节点可以组成一个联邦网络。联邦的作用是共享“哪个房间在哪个节点”和“哪个好友在哪个节点”的目录，让玩家不必手动记住每一台服务器的地址。

例如，你使用节点 1，朋友使用节点 2。两台节点加入同一联邦、同步目录后，你仍然可以找到朋友的房间。加入时，原生桥直接联系房间所属节点，Minecraft 游戏数据不会沿着每一台联邦节点逐个转发。

联邦网络的特点：

- **成员数量不设固定上限**。目录分页同步，心跳分批调度，限制同步并发和重试频率，控制节点增多后的同步开销。
- **自动选择和备用入口**。原生桥综合延迟、负载、剩余容量和维护状态选择节点，保留备用入口，减少反复切换。
- **中间节点失联不会自动切断所有节点**。已同步的目录仍可用于直接查找房主；如果正在承载某个房间数据的节点本身宕机，那个正在进行的 Minecraft 连接仍可能需要重新加入。
- **联邦邀请需要管理员互相确认**。只加入信任的节点，邀请内容不要公开发布。

联邦不会把所有服务器带宽合并成一条“无限带宽”。实际可承载的玩家数仍取决于服务器 CPU、内存、出口带宽、玩家距离和 Minecraft 世界活动。

## 性能和测试

传输路径使用 C++20 原生核心、libssh、ICE/P2P 和动态库桥；启动器可以通过 DLL、so 或 dylib 的 C ABI 调用。Python 目前负责网页、账号和目录控制业务，不负责游戏数据转发。

项目自测结果（Ubuntu 24.04、4 vCPU、约 3.7 GiB 内存，Release 构建）：

| 测试 | 结果 |
| --- | --- |
| 32 条中转流，三轮中位数 | 174.93 MiB/s 应用载荷吞吐 |
| 30 分钟连续回显压力测试 | 校验 186.314 GiB，平均 105.992 MiB/s |
| 4 个原版客户端短测 | 进服、移动、交互和重进通过；测试场景节省约 6.13% 应用字节 |
| 65 个联邦节点故障测试 | 强制停止中间节点后，幸存节点、好友查找、新房间和备用入口通过 |

这些数字是固定环境下的项目自测，不是第三方认证，也不能直接换算成公网玩家容量。65 个节点放在同一台 4 vCPU 机器上时，控制面延迟会明显增加。测试方法和边界见[性能说明](docs/performance.md)。

## 常见问题

### 为什么我看到的是“中转”，不是 P2P？

双方路由器、运营商 NAT 或防火墙可能不允许 UDP 打洞。启动器会自动使用加密中转，优先保证能玩；这不需要玩家手动切换网卡。

### 掉线后为什么有时要重新连接？

已经断开的 Minecraft TCP 连接无法在游戏内部无缝复活。桥会保留房间和其他健康连接，为新的游戏连接重新建立通道；如果 Minecraft 已显示断开，需要在游戏中重新连接启动器提供的地址。

### 存档会搬到节点上吗？

不会。Minecraft 世界和存档仍在房主电脑上，节点不托管存档。房主退出世界或关闭游戏后，朋友也就无法继续在这个世界游玩。

### 我应该提交什么反馈？

在节点网页的“问题反馈”入口提交描述，并附上启动器导出的诊断压缩包。请不要把管理员密码、启动器接入码、房间密钥或 SSH 私钥放进反馈内容。

## 开发者入口

- [跨平台构建](docs/build.md)
- [启动器动态库和 C ABI](docs/bridge-library.md)
- [协议说明](PROTOCOL_STANDARD.md)
- [中转压缩](docs/relay-compression.md)
- [部署、升级和回退](docs/deployment.md)

本项目使用 [AGPL-3.0-or-later](LICENSE)。第三方组件和许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
