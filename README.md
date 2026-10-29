<div align="center">

# Minecraft Tunnel Native

### Minecraft Java 版联机节点与原生网络核心

把启动器、联机节点和 Minecraft 世界连接起来，让朋友加入房主开放的世界。

[![Build](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml/badge.svg)](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml)
[![Release](https://img.shields.io/github/v/release/vdjango/minecraft-tunnel-native)](https://github.com/vdjango/minecraft-tunnel-native/releases/latest)
[![C++20](https://img.shields.io/badge/C%2B%2B-20-00599C.svg)](CMakeLists.txt)
[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-6554c0.svg)](LICENSE)

[概览](#项目概览) · [快速开始](#快速开始) · [节点部署](#节点部署) · [工作方式](#工作方式) · [开发与测试](#开发与测试)

</div>

---

Minecraft Tunnel Native 是 Minecraft Java 版的联机节点。它把玩家的启动器、节点和 Minecraft 世界连接起来，让朋友可以加入房主开放的世界。

房主在自己的电脑上运行 Minecraft 并管理世界与存档。节点负责房间和好友发现、连接协商及必要的数据中继，不托管 Minecraft 世界。网络条件允许时，玩家之间优先尝试 P2P；无法直连时，可以使用节点中继。客户端入口由 [Buggcraft](https://github.com/vdjango/buggcraft) 提供。

## 项目概览

本仓库包含两部分：用于部署联机服务的节点程序，以及供桌面启动器调用的跨平台 C++ 原生核心和桥接库。服务端提供节点管理网页、房间与好友目录、账号和节点联邦功能；原生组件负责连接建立、P2P 路由选择、加密传输与中继数据通道。

- **房间目录**：帮助玩家查找节点上开放的房间，并支持邀请码加入。
- **P2P 优先**：通过 ICE/STUN 探测直连条件；直连不通时，可根据连接策略改走中继。
- **加密连接**：原生连接使用 DTLS 加密；应用控制通道由 SSH 隧道承载。
- **节点联邦**：多个受信任的节点可共享房间与好友目录，让玩家跨节点查找房间。
- **可诊断的会话记录**：按会话保存连接事件、路由、流量和错误信息，帮助定位网络环境相关问题。
- **启动器桥接**：通过稳定的 C ABI 提供动态库接口，支持 Windows、Linux 和 macOS。

P2P 成功率取决于玩家双方的运营商、NAT、路由器和防火墙。部分网络必须通过中继；中继是正常的连接方式，不能保证每一对网络都能直连。

## 快速开始

### 加入已有节点

普通使用者不必单独安装本仓库。使用 [Buggcraft 启动器](https://github.com/vdjango/buggcraft/releases)添加节点提供的启动器接入码，即可浏览房间、添加好友、接受邀请并连接。房主仍需在 Minecraft 中开放自己的世界；加入者需要准备兼容的 Minecraft 版本和模组。

启动器接入码包含访问凭据，只应从可信的节点管理页面获取和分享。诊断包也可能含有本机路径、网络探测结果及连接信息，公开前请检查并清理私密内容。

### 部署 Ubuntu 节点

节点安装包适用于 Ubuntu 24.04 amd64。下载最新 [GitHub Release](https://github.com/vdjango/minecraft-tunnel-native/releases/latest) 中的 `.deb` 文件及校验文件，然后在服务器执行：

```bash
sudo apt install ./buggcraft-node_*_amd64.deb
sudo buggcraft-node setup
```

`setup` 提供首次网页配置所需的 HTTPS 地址和证书指纹。完成节点名称、服务器地址和管理员设置后，服务会由 systemd 管理。常用运维命令：

```bash
sudo buggcraft-node status
sudo buggcraft-node logs
sudo systemctl restart buggcraft-node
```

通常需要按节点配置开放 TCP/UDP `3333`（控制和联机传输）及 TCP `18443`（管理网页）；如更改端口，以实际配置为准。升级会重启节点并中断该节点上的连接，操作前请安排维护时间并备份 `/var/lib/buggcraft-node`。详细说明见[节点部署指南](docs/deployment.md)。

## 工作方式

1. 节点管理端为启动器和玩家提供房间、好友与节点目录。
2. 房主在 Minecraft 中将世界开放到局域网，并在启动器中创建房间。
3. 好友通过房间列表或邀请加入；启动器和原生核心协商可用的连接路径。
4. 原生核心优先尝试 P2P。若直连不可用，连接可经房间所属节点中继；玩家可在启动器中选择只使用中继。

Minecraft 世界、存档和游戏规则留在房主电脑上。节点故障会影响由该节点承载的服务或中继会话；已断开的 Minecraft TCP 游戏连接无法在游戏内无缝恢复，通常需要重新连接。

多个节点加入同一联邦后，可以同步房间与好友目录。联邦用于发现和路由，不会自动聚合服务器带宽；游戏数据的实际路径由房间所属节点、连接策略和双方网络决定。联邦成员应由管理员相互确认，仅连接并信任已知节点。

## 下载与发布

- [最新发行版](https://github.com/vdjango/minecraft-tunnel-native/releases/latest)：原生运行包、Ubuntu `.deb` 安装包及校验文件。
- [持续集成](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml)：Windows、Linux 和 macOS 的构建、测试及运行包产物。
- [提交问题](https://github.com/vdjango/minecraft-tunnel-native/issues)：报告节点部署、连接或原生桥接问题。

各系统的产物包含原生核心、桥接库和运行所需文件。启动器集成时应保留完整运行目录，不要只复制一个 DLL、`.so` 或 `.dylib`。发行包未包含节点持久化数据或管理员凭据。

## 开发与测试

### 环境

- CMake 3.24 或更新版本及 C++20 编译器。
- OpenSSL 3.x 开发文件；Windows 构建还需与目标工具链匹配的 vcpkg OpenSSL。
- Python 3.11 或更新版本，用于依赖准备、节点服务和测试。
- GitHub Actions 在 Ubuntu 24.04、Windows Server、Intel macOS 与 Apple Silicon macOS 上执行构建验证。

### 构建原生代码

```bash
python scripts/fetch_deps.py
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
ctest --test-dir build --output-on-failure
cmake --install build --prefix "$PWD/runtime"
```

跨平台工具链、Windows 与 macOS 配置、节点 `.deb` 构建方式见[构建指南](docs/build.md)。需要运行服务端 Web 时，按根目录的 `requirements.txt` 安装 Python 依赖，并参阅[部署指南](docs/deployment.md)。

### 架构入口

- [`docs/development.md`](docs/development.md)：节点开发入口、模块职责、本轮行为变化、诊断顺序与验证方法。
- [`core/`](core/)：C++ 节点核心、连接状态与传输路径。
- [`bridge/`](bridge/)：启动器调用的原生桥接实现和 C ABI。
- [`web/`](web/)：节点管理、账号、房间目录和联邦服务。
- [`tests/`](tests/)：原生协议、网络桥接、节点服务和联邦测试。
- [`docs/bridge-library.md`](docs/bridge-library.md)：公共桥接 API 与运行时布局。
- [`PROTOCOL_STANDARD.md`](PROTOCOL_STANDARD.md)：分层协议索引；细节、语言示例见 [`docs/protocol/`](docs/protocol/README.md)。
- [`docs/protocol/game-metadata.md`](docs/protocol/game-metadata.md)：Minecraft 版本、加载器、模组摘要、完整清单分页和下载参数。
- [`docs/launcher-federation-flow.md`](docs/launcher-federation-flow.md)：入口缓存、房间归属、P2P/中继与节点故障流程图。
- [`docs/security.md`](docs/security.md)：凭据、后台、代理、握手过滤和官方二进制识别的实际边界。
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)：第三方组件与许可证。

性能测试的环境、方法和边界见[性能说明](docs/performance.md)；各平台构建与回归结果见 [GitHub Actions](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml)。测量结果不等同于公网容量保证。

### 当前行为与接入注意（2026-10-28）

- 房间内容同步支持必需模组以及可选资源包、光影，新增 `resources_*` 摘要与 `kind:"resources"`；外观内容不影响联机匹配。与启动器 `.buggpack` 的内容规则见[游戏环境与内容同步](docs/protocol/game-metadata.md)。

- 公开房间可同步 Minecraft 版本、协议号、加载器/版本和模组摘要。实例信息由启动器提供，桥扫描 `mods`；预览最多 24 项，完整清单通过独立桥接接口分页取得，不通过网页下载接口。
- `/api/status.players` 为存活联邦节点的房主/访客桥会话总数，包含私密房间；容量、路径分布与 `game_players` 仍是本节点指标。全网详情可能部分可用，须处理 `partial`，不能把列表长度当实际游戏人数。
- Cloudflare IPv4/IPv6 代理网段在服务启动后后台获取，之后每 24 小时刷新，失败保留缓存；手工列表只维护额外可信代理。
- 已建立会话在 SSH 故障后按缓存候选重建，连续失败会停止并报告 `backup-unavailable`。联邦没有一致性选主或 TCP 状态复制，已断开的游戏需重新加入；具体缓存和重试边界见[恢复与兼容](docs/protocol/compatibility.md)。
- 后台从公共导航移除，管理路由仍须管理员鉴权。握手过滤不能证明官方桥，也不是完整 Minecraft 防火墙；详见[安全说明](docs/security.md)。

节点网页 `/developers` 是独立 HTML 指南，仓库分层文档是字段契约。修改接口时需要同时维护两处；文档日期不代表对应版本已部署。

## 贡献与安全

欢迎提交可复现的问题和代码改进。问题报告请包含操作步骤、系统与构建版本、相关日志，以及连接使用 P2P 还是中继。提交日志前移除管理员密码、节点接入码、房间密钥、私钥和个人凭据。

本项目使用 [GNU Affero General Public License v3.0 或更高版本](LICENSE) 发布。分发和修改时请同时遵守 `THIRD_PARTY_NOTICES.md` 中列出的第三方许可。
