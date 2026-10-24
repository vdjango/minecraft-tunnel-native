# 原生桥动态库接口

库与独立桥进程链接同一个 C++ 引擎，功能由 `bridge.initialize` 返回的 methods 列表定义。调用者只传递控制 JSON，C++ 线程直接处理 SSH、P2P、中转、压缩、游戏流、聊天和整合包下载。

## 文件与加载路径

完整 `cmake --install` 目录包含：

```text
native/
  bin/buggcraft-bridge[.exe]       # 可选的独立进程入口
  bin/buggcraft_bridge.dll        # Windows，依赖 DLL 同目录
  lib/libbuggcraft_bridge.so      # Linux；macOS 为 .dylib
  lib/                           # Unix 的 libssh、libdatachannel
  include/buggcraft/bridge.h
  share/buggcraft-native/python/buggcraft_bridge.py
  share/buggcraft-native/licenses/
```

始终基于应用安装目录定位，传入绝对路径，不能依赖工作目录。Windows 绑定使用 `LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32`，不修改 PATH、Qt 插件路径或工作目录。已加载的依赖仍受 Windows 模块复用规则约束；DLL 不能保证绕过系统的应用控制策略。Linux 库使用 `$ORIGIN`，macOS 库使用 `@loader_path`；可执行文件使用其 `../lib`。macOS 使用静态 OpenSSL，Linux 依赖发行版的 OpenSSL 3。库和调用进程的 CPU 架构必须一致。

Buggcraft 优先加载动态库；设置 `BUGGCRAFT_BRIDGE_MODE=library` 可禁止进程入口，`process` 可要求进程隔离。显式 `BUGGCRAFT_BRIDGE_EXECUTABLE` 在 auto 模式下优先；已有动态库加载失败会显示运行库/系统策略错误，不会静默切回 Python 联机实现。

## C ABI v1

公共头文件：[bridge.h](../include/buggcraft/bridge.h)。导出符号使用 C 链接；Windows 调用约定为 `cdecl`。C# 可用 P/Invoke，Rust 可用 `extern "C"`，Python 可用 ctypes，无需链接 Python 或 Qt。

1. 检查 `buggcraft_bridge_abi_version() == 1`。
2. `buggcraft_bridge_create(&handle)` 创建独立上下文。
3. `buggcraft_bridge_call(handle, request, request_size, &response, &response_size)` 调用。
4. 将 response 的精确长度复制到调用方，再调用 `buggcraft_bridge_free(response)`。
5. `buggcraft_bridge_destroy(handle)` 关闭该上下文及其全部会话。

请求为 UTF-8 JSON-RPC，最大 8192 字节，不要求换行或 NUL 结尾。响应长度不含附带的 NUL。JSON-RPC 错误返回正常 ABI 状态 0 和 JSON error 对象；ABI 非零状态表示调用参数、初始化或内部错误。通知返回空指针及长度 0，`free(NULL)`、`destroy(NULL)` 安全。禁止使用调用语言自身的 free 释放返回缓冲。

```c
#include <buggcraft/bridge.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    buggcraft_bridge *bridge = NULL;
    const char *request = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"bridge.initialize\",\"params\":{}}";
    char *response = NULL;
    size_t size = 0;
    if (buggcraft_bridge_abi_version() != 1 || buggcraft_bridge_create(&bridge)) return 1;
    int result = buggcraft_bridge_call(bridge, request, strlen(request), &response, &size);
    if (!result && response) fwrite(response, 1, size, stdout);
    buggcraft_bridge_free(response);
    buggcraft_bridge_destroy(bridge);
    return result;
}
```

同一 handle 的调用由 C++ 互斥锁串行化；不同 handle 可并行。初始化/销毁通过引用计数管理共享网络运行时，销毁一个 handle 不结束其他玩家。不得让 destroy 与该 handle 的调用并发，也不得在任何 handle 或返回缓冲尚未释放时卸载库。不要在 Windows DllMain 中调用这些接口。

部分控制调用需要联网，应放在 UI 之外的线程。Python 绑定使用 `ctypes.CDLL`，原生调用期间释放 GIL；Buggcraft 使用 `asyncio.to_thread`。C++ 的游戏转发线程独立运行，不等待 UI 的 JSON 轮询。

## 验证

`ctest` 包含纯 C 消费者，覆盖参数错误、JSON 错误、通知、多 handle 与重复创建销毁。`tests.test_bridge_library` 覆盖库并发调用、完整安装搬迁、中文/空格目录、与 exe 双向混合联机、P2P/中转、聊天、个人接入码、Mod 校验/取消及高频控制与游戏数据并行。CI 对 Windows、Ubuntu、macOS ARM64/Intel 运行同一套测试；各次构建的实际结果和失败日志见 [GitHub Actions](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml)。
