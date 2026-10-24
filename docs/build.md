# 跨平台构建与发行

需要 CMake 3.24+、C++20 编译器和 OpenSSL 3.x。`scripts/fetch_deps.py` 下载固定版本依赖并校验 SHA-256；Python 3.11+ 用于依赖准备、Web 和测试，独立桥运行时不需要 Python。

普通部署使用 [README](../README.md#安装一个节点) 中的安装包。各平台自动构建步骤以 [CI 配置](../.github/workflows/core.yml) 为准。

## Ubuntu 源码构建

```bash
sudo apt-get update
sudo apt-get install -y git build-essential cmake pkg-config libssl-dev python3-venv dpkg-dev
git clone https://github.com/vdjango/minecraft-tunnel-native.git
cd minecraft-tunnel-native
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/fetch_deps.py
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
cmake --install build --prefix "$PWD/runtime"
```

源码运行 Web：设置 `BUGGCRAFT_NATIVE_CORE="$PWD/runtime/bin/buggcraft-core"`，再执行 `.venv/bin/python web/main.py --directory ./data --no-browser`。

Ubuntu 24.04 使用系统 Python 3.12 的环境打包：

```bash
.venv/bin/python scripts/build_deb.py --runtime runtime --version 2026.10.24.1
```

产物在 `build/debian/`，包含 Web、私有 Python 依赖、原生节点与 libssh、systemd 服务和管理命令。安装时不运行 pip。使用实际 ELF 的符号依赖生成系统依赖列表；Python 二进制依赖限制为 3.12。此配方不适用于其他 Ubuntu 版本，也不交叉生成其他架构安装包。安装包校验与升级说明见 [部署指南](deployment.md)。

## Windows

支持 MSVC 与 MinGW-w64，两者应分别使用独立构建目录，并匹配 OpenSSL 的架构及工具链。

MSVC 示例，在 Visual Studio 开发者 PowerShell 中执行，先配置好 vcpkg：

```powershell
vcpkg install openssl:x64-windows
python scripts/fetch_deps.py
cmake -S . -B build-msvc `
  -DCMAKE_TOOLCHAIN_FILE="$env:VCPKG_INSTALLATION_ROOT/scripts/buildsystems/vcpkg.cmake" `
  -DVCPKG_TARGET_TRIPLET=x64-windows
cmake --build build-msvc --config Release --parallel 2
ctest --test-dir build-msvc --build-config Release --output-on-failure
cmake --install build-msvc --config Release --prefix runtime
```

MinGW-w64 示例，将 `C:/OpenSSL` 替换为兼容的 OpenSSL 安装目录：

```powershell
python scripts/fetch_deps.py
cmake -S . -B build-mingw -G "MinGW Makefiles" `
  -DCMAKE_BUILD_TYPE=Release -DOPENSSL_ROOT_DIR=C:/OpenSSL
cmake --build build-mingw --parallel 2
ctest --test-dir build-mingw --output-on-failure
cmake --install build-mingw --prefix runtime
```

## macOS

```bash
brew install cmake openssl@3 python
python3 scripts/fetch_deps.py
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
  -DOPENSSL_ROOT_DIR="$(brew --prefix openssl@3)"
cmake --build build --parallel 2
ctest --test-dir build --output-on-failure
cmake --install build --prefix "$PWD/runtime"
```

在目标架构上构建并验证。CI 分别配置了 ARM64 与 Intel，不能将一个架构的产物当作通用二进制。

## 构建选项

| 选项 | 默认值 | 用途 |
| --- | --- | --- |
| `BUGGCRAFT_BRIDGE` | `ON` | 构建客户端桥及动态库；仅部署节点时可设为 `OFF` |
| `BUGGCRAFT_LTO` | `ON` | 工具链支持时为 Release 启用链接时优化 |
| `BUGGCRAFT_SANITIZERS` | `OFF` | 在支持的非 MSVC 工具链启用地址与未定义行为检查 |
| `BUILD_TESTING` | `ON` | 构建原生测试 |

默认不使用 `-march=native`，避免发行产物绑定构建机器的 CPU 指令集。

## 发行目录

使用 `cmake --install` 生成完整运行目录，或获取 [Actions 构建产物](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml)。保留目录结构：

```text
runtime/
├── bin/       节点核心、桥程序；Windows 桥库及依赖 DLL
├── lib/       Linux / macOS 桥库及原生依赖
├── include/   公共 C ABI 头文件
└── share/     Python FFI 绑定、许可与相关资源
```

Ubuntu 仍需提供系统 OpenSSL 3 运行库；macOS 构建将 OpenSSL 静态链接进原生产物。节点的 Web 源码与 Python 环境需要另外保留，原生安装目录本身不是完整的 Web 部署包。

启动器可将此目录作为自己的 `native/` 子目录，使用绝对路径加载对应平台的桥库。Windows 应限定 DLL 搜索目录；Linux/macOS 使用安装产物的相对 RPATH。不要将桥依赖复制到 Qt 插件目录，也不要只分发单个桥文件。

构建成功、版本命令成功和真实联机通过是不同验证。产物仍受 Windows 应用控制、macOS Gatekeeper 和签名策略约束；项目当前未提供受信任代码签名或 Apple 公证。

## 自动发布到 GitHub Release

构建和发布都使用现有 GitHub Actions，无需单独部署构建服务器。日常提交运行四个平台的构建、C/C++ 单元测试、桥与联邦测试；Ubuntu 额外验证 `.deb` 安装与重装。

准备正式版本时，先修改 `CMakeLists.txt` 的项目版本并提交，再推送对应标签：

```bash
git tag v0.3.4
git push origin v0.3.4
```

标签版本必须与 CMake 一致。也支持 `v0.3.4-rc.1`、`-beta.1`、`-alpha.1`，这些会标记为预发布。普通分支提交只保留构建产物，不创建 Release。

所有平台成功后，发布任务下载本次构建的归档，验证五个包及 SHA-256，再创建 Release 草稿、上传附件并发布。上传中途失败可以重跑发布任务，草稿会继续上传；已经发布的版本不会被覆盖。

| 附件 | 用途 |
| --- | --- |
| `buggcraft-node_版本号_amd64.deb` | Ubuntu 24.04 完整节点安装包，含 Web |
| `buggcraft-native-版本号-windows-x64.zip` | Windows 核心、桥 EXE、DLL 和依赖 |
| `buggcraft-native-版本号-macos-arm64.tar.gz` | Apple Silicon 原生运行包 |
| `buggcraft-native-版本号-macos-x64.tar.gz` | Intel Mac 原生运行包 |
| `buggcraft-native-版本号-linux-x64.tar.gz` | Ubuntu 24.04 原生运行包 |

附件同时包含各包 `.sha256` 和汇总 `SHA256SUMS`。解压原生运行包后保留 `runtime/` 结构；其中 `share/buggcraft-native/build-info.json` 记录版本、提交和构建链接。Unix 归档在上传前打包，保留执行权限和动态库软链接。

发布任务仅在本仓库的版本标签推送、且全部构建成功时获得 `contents: write`；使用 GitHub 自动提供的 `GITHUB_TOKEN`，不需要添加个人令牌或生产服务器凭据。自动发布不等于自动升级生产节点，也不包含启动器自身的打包发布。
