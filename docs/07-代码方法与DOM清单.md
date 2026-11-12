# 代码方法与 DOM 清单

2026-11-11 受管大厅：ControlClient::publish 缓存成功描述，rooms(query)/resolve 调用唯一控制接口；Rpc::publish_listing 复用 Session describe 结果，在建房、原轮询、配置和关联世界后同步。原 rooms 只有容量记录、resolve 只查邀请，缺失真实描述和公开策略，因此扩展原适配器。CLI/SDK 同一实现，没有新线程、计时器或 DOM；实测见相邻控制项目 docs/13。

本清单是复用入口；机器清单由源码语法树生成，不手改 JSON。先按下表定位唯一所有者，再检索具体符号及调用方。增加代码前搜索职责与行为，不仅搜索拟用的新名字。

当前服务精简的唯一所有者补充：identity.AccountIssuer/canonical、HttpSurface、storage.atomic_json、Accounts.get/resolve、ServicePortal 两种角色与 service_package，详见[13-复用决策和验收](13-服务精简与复用验收.md)。不得恢复副本策略或在节点另建管理、登录、网页社交。

| 能力 | 唯一实现/入口 | 调用与约束 |
| --- | --- | --- |
| SSH 监听、工作线程、控制 RPC | `core/engine.cpp`、`worker.cpp`、`protocol.cpp` | `web/tunnel/native_relay.py` 通过有界 IPC 管理，游戏载荷不进 Python |
| 节点压缩策略 | `native_relay.DEFAULT_COMPRESSION`、`RelayServer.compression_capability/_event` | 默认 off；main 读取现有 INI，首次初始化显式生成 off；节点决定是否宣告和允许压缩，不在启动器再复制配置解析 |
| 游戏数据转发与背压 | `core/relay.cpp`、`core/net.hpp::Ring` | 桥也使用 Ring；不另做移位数组队列 |
| Base64/URL 编码、字母表检查、十六进制 | `core/encoding.hpp` / `.cpp` | IPC、接入码、委托签名和随机 ID 复用；编解码复用固定版本 aklomp/base64，`selected_codec` 管只读分派，域层保留大小限制和错误码 |
| SSE2/AVX2/AVX512BW/NEON 字节扫描 | `core/encoding_simd.cpp`、`cmake/NativeCpu.cmake` | 单一源码按 ISA 编译；`selected_backend` 负责选择，调用方不写 CPUID |
| 流式摘要与文件单遍哈希 | `core/digest.hpp` / `.cpp` | `Digest::update/final_hex`、`stream_digests`；双摘要按需开启，调用方不再复制 EVP 生命周期 |
| CLI/C ABI 共用 RPC | `bridge/rpc.cpp`、`bridge/c_api.cpp` | `include/buggcraft/bridge.h` 是 ABI；`bindings/python/buggcraft_bridge.py` 只是绑定 |
| Windows 单文件桥链接与许可 | `cmake/StandaloneBridge.cmake`、`VerifyStandaloneBridge.cmake`、`bridge/main.cpp --licenses` | 默认关闭，静态链接相同引擎和依赖；内嵌许可，构建强制检查非系统 DLL，不另造加载器或转发规则；实测见报告 05 |
| 原生库运行期初始化/释放 | `bridge/runtime.cpp::acquire_runtime/release_runtime` | 多句柄引用计数，禁止每个调用者自己初始化 Winsock/libssh/RTC |
| 房间连接、流、恢复 | `bridge/session*.cpp`、`stream.cpp`、`peer*.cpp` | `Session` 是本机会话所有者，`route_selector.*` 负责候选策略 |
| 中继压缩 | `bridge/codec.hpp` / `.cpp` | BCZ1 边界、窗口和降级；复用 zstd，不在 SDK 另做编解码 |
| SSH 载荷加密选择 | `bridge/cipher_policy.hpp` / `.cpp` | 桥与 SDK 只在 Ssh 入口应用同一进程级只读策略；复用 OpenSSL CPU/OS 分派和 libssh 协商，不复制 AES/ChaCha/CPUID |
| 内容清单、下载与校验 | `bridge/modpack.cpp` | ModTransfers 管理有界任务、临时文件、提交与取消；`catalog_hash` 统一模组/资源清单摘要规则 |
| 文件流批次、上传节奏与唤醒 | `ModTransfers::tick/pump_peer/set_wakeup/poll_timeout_ms` | 复用现有 Session Wakeup、RTC 通道和速率桶；游戏先行，每流每轮最多 4 条接收消息/64 KiB 发送，累计信用窗口仍为 256 KiB |
| 遗留节点权限、房间与目录 | `web/tunnel/relay.py`、`directory.py`、`federation.py` | 这是待原生迁移的现状；不得作为最终 C++ 运行包的依赖 |
| 控制中心与自动入网 | `web/tunnel/control_plane.py::ControlPlane` | 复用 Portal 管理员/CSRF、原子状态及 Federation 固定公钥；mode 为 mesh/center/edge，票据只绑定一个节点；操作工具为 `scripts/control_admin.py` |
| 社交投递、重试与房间校验路由 | `web/tunnel/social_delivery.py::submit/deliver/route/retry_pending` | 网页、桥和 SDK 共用；既有 Social 拥有签名验证与持久事件/发送队列；重试有界，中心不承载游戏字节 |
| 多设备在线与签名社交状态 | `web/tunnel/social.py::Social` | 短期邀请/申请和队列落盘；在线按设备公钥聚合且不落盘；unknown 不等于明确离线，协议新增字段保留旧 online 布尔 |
| 页面挂载、异步取消与资源释放 | `frontend/src/runtime.ts::createScope/mountPage` | 页面控制器复用作用域，离页清理定时器、监听和请求 |
| 账号页头与导航 | `frontend/src/NodeHeader.vue`、`pages/account.vue` | 仅账号核心功能；已删除节点管理页和旧联邦导航 |
| 静态资源引用与清理 | `frontend/build/page-assets.ts` | 两套构建共用内容哈希引用、预加载及过期 JS/CSS 清理 |
| 多轮 CPU 对照 | `scripts/benchmark_cpu.py`、`benchmarks/measure.hpp` | 模式只影响新子进程，不设置全局环境或生产服务 |
| 编号文档门禁 | `scripts/check_docs.py` | 校验本地链接、导航引用、编号、生成清单与发布说明路径；不访问公网，不读取历史实测归档 |
| 多玩家转发与加密对照 | `scripts/benchmark_forwarding.py`、`scripts/bridge_soak.py` | 复用三玩家真实桥/节点夹具、字节校验/EOF 和 CPU 掩码；区分旧版、新字节 SIMD 与 OpenSSL 加速，记录实际协商 cipher |
| 游戏/文件混合负载 | `scripts/bridge_soak.py --scenario mixed` | 复用 BridgeServicesTests 的真实节点/桥/P2P 夹具；文件 SHA-256、游戏回声/EOF、RTT 分位数及进程资源，默认四个 16 MiB 文件 |

## 生成内容与覆盖边界

2026-11-12：ManagedControl::renew_allocations 仍是预留续约的唯一适配入口，renew_loop 与原 run 分别持有续约/心跳，复用原 ControlConnection 的 CA 校验、有界连接及 close。批量规则唯一归独立 C++ 控制的 placement_lease；节点只轮转 ID、适配返回结果，旧控制回退仍调用同一个 call。原 Session、桥、SDK 和游戏字节路径不新增一套租期规则。

受管模式新增所有者：`managed_control.py::ControlConnection` 统一有界 HTTPS、严格 CA 与关闭；`IdentityAuthority` 仅控制主机批准/撤销，`ManagedControl` 仅节点入网/proof/验票/租期。复用 Federation 已加载的主机密钥，不能假设 RelayServer.host_key 已经是密钥对象。`ControlClient` 归桥，CLI/C ABI 同用，调度规则只在中心；其 resolve/social/allocate/reconnect/release 调用中心，Session 回调只在所属恢复线程更新同一分配。`profile_exchange::https_json` 是接入码与受管接口唯一 HTTPS 机制，设备签名复用 `social::sign_delegated_message`，hex 解码复用 `encoding::unhex`。接口/失败/资源契约见 [12](12-统一控制与稳定性.md)。

- [methods.json](90-代码清单/methods.json)：`core/`、`bridge/`、`include/`、`bindings/`、`web/`、`scripts/` 的 Python/C/C++ 方法、类/声明/实现/匿名回调，以及 `frontend/src/` 的 JS/TS/Vue 函数。
- [dom.json](90-代码清单/dom.json)：Vue 模板元素、属性、指令，以及 `web/tunnel/web/*.html` 服务端壳；附前端 AST 中的 ID/选择器/事件引用。
- [sources.json](90-代码清单/sources.json)：上述唯一源码的 SHA-256（统一换行），方法体、删除、DOM 和指令变化均使检查过期。

不收集依赖、构建产物、`site/` 预渲染副本、测试夹具及临时产物。C++ 使用 tree-sitter 语法解析而非预处理后的符号表；宏生成符号按源码宏名记录，声明与定义分别保留。动态 DOM/选择器表达式保留源码表达式，不猜测运行时展开结果。它不是编译器/浏览器完整行为模型，也不检测语义重复。

```bash
python -m pip install -r requirements-dev.txt
npm --prefix frontend ci
python scripts/update_code_inventory.py
python scripts/update_code_inventory.py --check
rg -n 'base64|stream_digests|createScope|network-rows' core bridge frontend/src docs/90-代码清单
```

CI 的 Web 作业执行过期检查，新增/删除/改名和方法体变更均须同步生成。清单脚本报错时修复解析或输入，不接受以空清单替代。业务规则复用仍需评审：同名不是相同语义，参数和认证域不同不能盲目合并。

## 完整启动器单文件复用（2026-11-11）

`bridge/cli.cpp::bridge::run_cli` 是独立桥 main 和完整 Qt 启动器共用的入口，归 `buggcraft-bridge-cli` 静态目标；复用 Rpc、runtime acquire/release 和既有转发线程，不新增第二份引擎。可选 `--native-bridge` 标记只选择模式，后续版本/许可/stdio RPC 语义相同。`buggcraft_embed_bridge_notices` 将许可装入该 CLI，`buggcraft_finish_standalone_bridge` 仅检查最终 EXE 的系统依赖。SDK C ABI 继续直接复用 engine，不依赖 CLI。Qt 客户端仍使用异步子进程，不在 GUI 中同步调用 run_cli。
