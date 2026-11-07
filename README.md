<div align="center">

# Buggcraft 联机节点

### 让不同网络里的朋友，加入同一个 Minecraft 世界

房主开世界，好友进房间。多个节点互联，房间和好友信息互通。

[![Build](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml/badge.svg)](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml)
[![Release](https://img.shields.io/github/v/release/vdjango/minecraft-tunnel-native?label=最新版本)](https://github.com/vdjango/minecraft-tunnel-native/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/vdjango/minecraft-tunnel-native/total?label=下载)](https://github.com/vdjango/minecraft-tunnel-native/releases)
[![Stars](https://img.shields.io/github/stars/vdjango/minecraft-tunnel-native?style=flat&label=Star)](https://github.com/vdjango/minecraft-tunnel-native/stargazers)
[![Platforms](https://img.shields.io/badge/启动器支持-Windows%20%7C%20macOS%20%7C%20Linux-6750A4)](https://download.bugg.icu)
[![C++20](https://img.shields.io/badge/网络核心-C%2B%2B20-00599C)](CMakeLists.txt)
[![License](https://img.shields.io/badge/许可证-AGPL--3.0--or--later-6554c0)](LICENSE)

[官网](https://bugg.icu) · [下载启动器](https://download.bugg.icu) · [联机入口](https://node.bugg.icu) · [玩家教程](https://bugg.icu/docs) · [反馈问题](https://github.com/vdjango/minecraft-tunnel-native/issues)

</div>

---

## 我能用它做什么？

和朋友玩 Minecraft Java 版：创建房间、邀请好友、查找公开房间，并同步联机需要的游戏配置。

世界和存档保存在房主电脑上。房主需要运行游戏并开放世界；节点负责发现房间、协商连接，以及在无法直连时转发游戏数据。无需给每个世界另租一台 Minecraft 服务器。

- **好友联机**：通过房间列表或邀请进入好友的世界。
- **优先直连**：网络允许时使用 P2P；无法直连时由节点中转。
- **跨节点找房间**：加入同一联邦的节点可以互通房间和好友信息。
- **网页管理**：设置节点名称、地区、注册权限、人数预算和网络连接。
- **文件加速接入**：可接入 Buggcraft 源站，帮助补齐联机需要的资源文件。

## 我只是想和朋友玩

1. 到[下载站](https://download.bugg.icu)下载 Buggcraft 启动器，解压后运行。
2. 打开联机大厅，连接可信的节点，按浏览器提示登录并确认设备。
3. 房主选择游戏实例、创建房间，进入世界并开放到局域网。
4. 好友从房间列表或邀请进入，按启动器提示补齐游戏版本和模组，再连接游戏。

如果节点支持注册，可以按页面指引创建账号；未开放注册时联系节点管理员。**普通玩家不需要安装本仓库的节点程序，也不需要接触联邦管理凭据。**

操作截图、整合包导入和常见问题见[玩家教程](https://bugg.icu/docs)。找队友或交流问题，可添加微信 **junsi223** 联系入群，或加入 QQ 群 **849362477**。

## 联邦网络是什么？

可以把它理解为：**多位管理员各自运行节点，再把这些节点连接成一张共同的联机网络。**

例如，上海节点和武汉节点加入了同一联邦。房主连接上海创建房间，好友连接武汉，也能找到这个房间并加入，不用为了找房间反复切换节点。

| 联邦能做什么 | 对玩家有什么用 |
| --- | --- |
| 同步房间目录 | 在一个入口看到其他在线节点的公开房间 |
| 同步账号和好友相关信息 | 在同一联邦内使用账号、查找好友和接收联机邀请 |
| 提供房间所属节点的信息 | 启动器找到实际承接房间的节点，再建立连接 |
| 节点各自独立运行 | 一台节点停机时，其他节点仍能提供各自的服务 |

联邦不会把多台服务器的带宽合并，也不会代替房主运行世界。实际游戏流量优先尝试玩家之间直连；需要中转时，使用承接该房间的节点。房主关掉游戏，世界就会停止；承载连接的节点故障时，相关玩家可能需要重新连接。

**节点可以单独运行，加入联邦是可选项。** 联邦成员共享信任范围，管理员应只接入自己认可的网络。

## 我想提供一个节点

推荐 Ubuntu 24.04 amd64。从[最新发行版](https://github.com/vdjango/minecraft-tunnel-native/releases/latest)下载 `buggcraft-node_…_amd64.deb`，在服务器执行：

```bash
sudo apt install ./buggcraft-node_*_amd64.deb
sudo buggcraft-node setup
```

按照终端给出的地址打开网页，填写节点名称、公网地址和管理员信息，完成配置即可。安装包会配置后台服务、开机启动和自动校时；网络校时在后台进行。

联机默认需要开放 **TCP / UDP 3333**。网页可以通过 **HTTPS 443** 访问，节点内部的 **18443** 可由反向代理转接，不必直接暴露公网；NAT 服务器使用实际映射端口。具体 HTTPS 配置见[节点部署指南](docs/节点部署指南.md)。

### 如何加入已有联邦？

1. 向已有联邦的管理员申请入网邀请。
2. 登录自己节点的管理后台，打开“连接网络”，导入邀请。
3. 等待节点心跳和房间目录同步，在“节点网络”查看在线成员。

邀请包含管理凭据，只在管理员之间传递。普通玩家仍使用节点地址和浏览器登录，不用填写联邦邀请。

### 日常管理

```bash
sudo buggcraft-node status       # 查看服务状态
sudo buggcraft-node logs         # 查看最近日志
sudo systemctl restart buggcraft-node
```

升级前备份 `/var/lib/buggcraft-node`，再安装新版 `.deb`。升级会重启本节点，安排在无人联机时操作。安装、升级、证书、端口、自动校时和备份说明见[节点部署指南](docs/节点部署指南.md)。

### 怎么填写带宽和人数？

后台直接填写服务器的**上传带宽 Mbps**，例如 6、20、200；不用自己换算 MB/s。节点按每人流量预算和预留带宽估算可接纳访客数，房间人数还受单独上限限制。

页面的“预计中转余量”是预算值，不是性能承诺。CPU、内存、玩家视距、模组和区块加载都会影响实际体验，应结合后台实时流量调整。

## 开发、接入与贡献

想修改节点或把网络核心接入自己的启动器，请从[节点开发指南](docs/节点开发指南.md)开始；[构建指南](docs/构建指南.md)介绍环境和打包，[协议索引](docs/protocol/协议索引.md)与[原生桥接口](docs/原生桥动态库.md)介绍程序接入。完整资料见[文档索引](docs/文档索引.md)。

欢迎提交问题与改进。反馈时请附系统、版本、操作步骤和相关日志，并移除密码、私钥和个人凭据。

本项目使用 [AGPL-3.0-or-later](LICENSE)，第三方组件许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。Buggcraft 与 Mojang / Microsoft 无隶属关系。
