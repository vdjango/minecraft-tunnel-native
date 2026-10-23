# Minecraft Tunnel Native

Buggcraft 的 C++20 联机核心与启动器桥。服务器主要面向 Ubuntu；桥面向 Windows、macOS 和 Linux。原版 Minecraft 无需安装 Mod。

启动器默认通过 C ABI 调用 `buggcraft_bridge.dll` / `libbuggcraft_bridge.so` / `libbuggcraft_bridge.dylib`；也可使用独立 `buggcraft-bridge` 进程。两种入口共用同一套 C++ 实现。节点接入、房间控制、SSH、P2P、加密中转、压缩、游戏状态探测、聊天和整合包传输都由桥处理，Qt/Python 层只负责界面和控制参数。

> 服务端的游戏数据面由 C++ 核心处理；`web/` 仍包含 Python 网页、账号、房间授权和联邦目录业务。这些服务端控制业务尚未全部移植为 C++。`compatibility/` 保留旧协议测试契约，回归时连接当前原生核心，不是运行入口。

## 架构与目录

```text
启动器界面 ── C ABI 或 stdio JSON-RPC ── C++ bridge
                                       ├─ WebRTC DTLS/SCTP ── 对端 bridge ── Minecraft
                                       └─ SSH ── C++ core ── 对端 bridge ── Minecraft
                                                   │ 私有 IPC
                                              Python web/业务
```

| 目录 | 用途 |
| --- | --- |
| `core/` | SSH/STUN、房间转发、流控背压、心跳、容量限制和业务 IPC |
| `bridge/` | 独立启动器桥、选路、P2P/中转、压缩、聊天、游戏探测及整合包传输 |
| `include/buggcraft/bridge.h`、`bindings/` | 稳定 C ABI 头文件和 Python ctypes 绑定，供其他语言调用 |
| `web/`、`site/` | Python Web 与节点业务、静态页面 |
| `tests/cpp/` | C++ 编解码、边界、协议、心跳和日志单元测试 |
| `tests/` | 真实核心/桥进程、故障、选路、联邦和原版服务器集成测试 |
| `scripts/` | 固定依赖下载、构建验证、压力测试与部署工具 |
| `compatibility/` | 旧协议契约及兼容性测试；通过 `scripts/check_compatibility.py` 对当前原生核心执行 |
| `docs/` | 验证范围、测量结果、日志、压缩协议和部署记录 |

## 构建

依赖 CMake 3.24+、C++20 编译器、OpenSSL 3.x 和 Python。Python 用于依赖准备、服务端 Web 和测试；独立桥的运行不需要 Python。依赖下载脚本固定版本并校验 SHA-256；Release 在工具链支持时启用 LTO，不使用 `-march=native`。

### Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y build-essential cmake pkg-config libssl-dev python3-venv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/fetch_deps.py
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
cmake --install build --prefix "$PWD/runtime"
export BUGGCRAFT_NATIVE_CORE="$PWD/runtime/bin/buggcraft-core"
export BUGGCRAFT_BRIDGE_EXECUTABLE="$PWD/runtime/bin/buggcraft-bridge"
```

### Windows

支持 MinGW-w64 和 MSVC。已在本机使用 MinGW-w64 GCC 15.2 构建；CI 使用 MSVC/vcpkg。

```powershell
python scripts/fetch_deps.py
cmake -S . -B build -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release -DOPENSSL_ROOT_DIR=C:/OpenSSL
cmake --build build --parallel 4
cmake --install build --prefix runtime
$env:BUGGCRAFT_NATIVE_CORE = "$PWD/runtime/bin/buggcraft-core.exe"
$env:BUGGCRAFT_BRIDGE_EXECUTABLE = "$PWD/runtime/bin/buggcraft-bridge.exe"
$env:PATH = "$PWD/runtime/bin;" + $env:PATH
```

使用 MSVC 时先执行 `vcpkg install openssl:x64-windows`，配置 CMake 的 vcpkg toolchain，并在构建、安装和 CTest 命令中加 `--config Release` / `--build-config Release`。具体命令见 [跨平台 CI](.github/workflows/core.yml)。

### macOS

```bash
brew install cmake openssl@3 python
python3 scripts/fetch_deps.py
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DOPENSSL_ROOT_DIR="$(brew --prefix openssl@3)"
cmake --build build --parallel 2
cmake --install build --prefix "$PWD/runtime"
```

CI 配置包含 macOS ARM64 和 Intel；本次工作环境没有完成 macOS 实机验证。当前产物没有受信任代码签名或 Apple 公证。

**发行时保留完整安装目录。** Windows 的桥库和依赖 DLL 放在 `bin/`，Linux/macOS 的桥库和原生依赖放在 `lib/`；Linux 还需要发行版的 OpenSSL 3 系统运行库，macOS 构建使用静态 OpenSSL。`cmake --install` 同时安装 C 头文件、Python 绑定及许可。可从 [Actions](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml) 获取对应平台构建产物。

## 启动节点服务

根据 [config.example.ini](config.example.ini) 和 [部署记录](docs/production-deployment-20261023.md) 生成独立配置、SSH 主机密钥和随机玩家凭据，然后启动：

```bash
.venv/bin/python web/main.py --config /path/to/config.ini
```

Web 进程负责启动和管理原生核心。部署前用 `runtime/bin/buggcraft-core --version` 检查动态库，用 `runtime/bin/buggcraft-bridge --version` 检查桥；只有版本命令成功不代表联机验收通过。运行数据、管理员密码、接入码、玩家日志和私钥不得提交仓库。

## 启动器接入

将 CMake 安装目录打包为启动器的 `native/`，保持 `bin/`、`lib/`、`share/` 的相对位置。Buggcraft 默认优先加载动态库，找不到库时使用独立进程；已经找到库但加载失败时明确报错。`BUGGCRAFT_BRIDGE_LIBRARY` 可指定库的绝对路径；`BUGGCRAFT_BRIDGE_MODE=library|process|auto` 选择入口。显式设置 `BUGGCRAFT_BRIDGE_EXECUTABLE` 时，auto 使用指定进程。源码开发时额外查找相邻原生仓库的 `runtime/` 和 `build/`。

Python 调用示例（绑定只用标准库，游戏数据不经过 Python）：

```python
from buggcraft_bridge import Bridge  # 来自安装目录 share/buggcraft-native/python

with Bridge('/absolute/path/native/lib/libbuggcraft_bridge.so') as bridge:
    response = bridge.request('bridge.initialize', {'protocol': 1})
    print(response['result']['build'])
```

Windows 路径为 `native/bin/buggcraft_bridge.dll`，macOS 为 `native/lib/libbuggcraft_bridge.dylib`。Windows 使用绝对路径和限定 DLL 搜索标志，只搜索该 DLL 目录及 System32，不从当前目录、PATH 或 Qt 目录寻找桥依赖。Linux/macOS 使用库自身的相对 RPATH。其他语言接入和内存管理见 [C ABI 文档](docs/bridge-library.md)。动态库同样受系统代码签名及应用控制策略约束。

动态库和进程入口接收相同的 UTF-8 JSON-RPC。使用 `buggcraft-bridge` 子进程时，通过 stdin/stdout 交换 JSON Lines，并持续读取 stderr。先初始化，再添加节点：

```json
{"jsonrpc":"2.0","id":1,"method":"bridge.initialize","params":{"protocol":1}}
{"jsonrpc":"2.0","id":2,"method":"node.add","params":{"code":"buggnode1-<用户复制的启动器接入码>"}}
{"jsonrpc":"2.0","id":3,"method":"room.host","params":{"node":"<返回的node句柄>","game_port":25565,"transport":"auto"}}
{"jsonrpc":"2.0","id":4,"method":"room.join","params":{"node":"<返回的node句柄>","invite":"bugg1-<房间邀请码>","transport":"auto"}}
{"jsonrpc":"2.0","id":5,"method":"room.close","params":{"session":"<返回的session句柄>"}}
```

建房与加入是两个玩家各自的操作。加入后把返回的 `127.0.0.1:端口` 交给 Minecraft；启动器无需转发游戏数据。关闭 stdin 会清理会话并退出。

- **启动器接入码**以 `buggnode1-` 开头，用于添加节点。匿名接入码可联机，用户中心生成的个人接入码额外提供账号/好友能力。
- **房间邀请码**以 `bugg1-` 开头，用于加入房间，不能填入添加节点的输入框。
- 个人接入码含授权凭据和本地签名材料。必须完整传入桥，SSH 认证由桥自行取对应凭据段；不得打印、截断、放入 URL 或命令行。
- 缺少桥、依赖库或能力不匹配会明确报错。更新启动器时必须同时更新完整原生运行目录。

请求上限 8 KiB；最多 16 个节点句柄、8 个房间会话。完整能力以 `bridge.initialize.result.methods` 为准，接口与安全边界见 [PROTOCOL_STANDARD.md](PROTOCOL_STANDARD.md)。

## 性能、带宽与故障处理

节点将同一房间的流归属同一工作线程，访客 channel 直接转发到房主 channel，使用有界环形缓冲、SSH 窗口和背压，不经 Python 搬运游戏字节。不同房间分布到多个工作线程。桥的网络会话、游戏状态探测和整合包下载分别调度，避免控制探测长时间阻塞数据转发。

中转可协商原生 Zstandard level 1 压缩，节点直接转发压缩流；原版游戏无需改变。仅压缩确有收益的数据，连续不可压缩数据转为原始流，避免长期额外开销；压缩块、解压窗口和输出量都有上限。Minecraft 已压缩或加密的流量不保证进一步节省带宽。协议与测量口径见 [压缩说明](docs/relay-compression.md)。

`auto` 优先尝试 P2P，失败时使用加密中转；同一房间可混合两种路径。桥按延迟、抖动、负载和剩余容量选择节点并保留备用入口，通过事件提示界面。已建立的 TCP 流不能无缝迁移到另一节点；所属节点失联时需要重新加入，其他健康节点上的房间不受邀请链断开影响。

心跳识别异常退出，联邦同步采用有界并发、退避、抖动和签名目录增量刷新。当前联邦上限 32 个成员；找房/好友查询利用本地已验证目录及受限直查，不沿邀请链逐级转发。详见 [心跳故障验证](docs/validation/heartbeat-federation-20261023.md) 和 [长链验证](docs/validation/federation-chain-20261023.md)。

核心参数包括 `workers`、`buffer_kib`、`max_connections`、`max_total_streams`、`max_streams_per_room`、`max_streams_per_client` 和 `compression`，见示例配置。调大缓冲不能增加物理出口带宽；容量需要按实际机器和游戏负载测量。

## 测试与记录

以下集成测试启动真实 C++ 核心，并通过桥进程及动态库联机。先设置上面的两个可执行文件环境变量，并安装 `requirements.txt`。动态库测试设置 `BUGGCRAFT_BRIDGE_LIBRARY` 为安装的库，`BUGGCRAFT_BRIDGE_PACKAGE` 为完整安装目录，以验证中文、空格路径搬迁：

```bash
ctest --test-dir build --output-on-failure
python -m unittest tests.test_native tests.test_bridge tests.test_bridge_services tests.test_bridge_library tests.test_route_selection tests.test_node_health tests.test_player_logs -v
python -m unittest tests.test_federation_budget tests.test_federation_lookup tests.test_federation_resilience tests.test_federation_chain -v
python scripts/check_compatibility.py
```

使用虚拟环境时将 `python` 换成 `.venv/bin/python`。C++ 单元覆盖压缩损坏帧/解压上限、碎片化、半关闭、环形缓冲、心跳、日志和游戏协议解析；集成覆盖多玩家并行、高吞吐延迟、黑洞断网、强杀、回退、个人接入码和好友签名、整合包分页/校验/取消及并行游戏传输。

```bash
# 30 分钟压力测试；保留结果和逐玩家日志
python scripts/bridge_soak.py --seconds 1800 --output validation-artifacts/bridge-soak.json

# 已有合法原版服务端 jar 时运行多版本聊天/状态/人数上报验证
BUGGCRAFT_CHAT_SERVER_ROOT=/path/to/chat-servers python -m unittest tests.test_native_game_chat -v
```

原版服务器测试默认覆盖 1.6.4、1.8.9、1.19、1.19.2、1.20.4、1.21.8、26.3；jar 按 `<版本>/server.jar` 放置，缺少资产则跳过对应项。Java 版本需匹配服务端要求。它使用真实原版服务器和两个原生协议客户端；GUI 多玩家/死亡复活实测另见 [既有验证记录](docs/validation-20261023.md) 与 [A 建房/B 加入](docs/validation/player-pair-performance-20261023.md)。这些不同测试的结果不能互相替代。

日志按玩家/会话保留连接路径、心跳、流量、压缩、关闭原因及性能采样；使用 `BUGGCRAFT_BRIDGE_LOG_DIR` 指定目录。不要上传原始接入码、token、私钥或玩家隐私；保留策略和诊断导出见 [日志说明](docs/validation/player-log-retention.md)。最新源码验证与限制见 [原生桥接管记录](docs/validation/native-cutover-20261023.md)。历史性能测量只描述对应源码、机器和负载，不代表后续所有版本的性能承诺。

## 许可

项目使用 [AGPL-3.0-or-later](LICENSE)。第三方许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 `licenses/`；发行包须同时包含这些文件。
