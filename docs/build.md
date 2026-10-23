# 跨平台构建与发行

需要 CMake 3.24+、C++20 编译器和 OpenSSL 3.x。`scripts/fetch_deps.py` 下载固定版本依赖并校验 SHA-256；Python 3.11+ 用于依赖准备、Web 和测试，独立桥运行时不需要 Python。

Ubuntu 节点的完整起步流程见 [README](../README.md#快速开始)。各平台自动构建步骤以 [CI 配置](../.github/workflows/core.yml) 为准。

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
