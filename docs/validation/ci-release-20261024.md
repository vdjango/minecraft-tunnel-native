# Windows 构建修复与自动发布（2026-10-24）

## 故障原因

失败构建：[39996443809](https://github.com/vdjango/minecraft-tunnel-native/actions/runs/39996443809)。下载该次 Windows 诊断产物并核对 GitHub 提供的 SHA-256 后，完整日志显示真正的错误是生成协议头的 `C2026: string too big`，不是日志末尾的 `C4996` 弃用警告。

原生成器把 44,784 字节的聊天协议表与 19,782 字节的旧协议表分别放进单个原始字符串字面量。MSVC 单个字面量的限制为 16,380 个单字节字符；参考 [Microsoft C2026 文档](https://learn.microsoft.com/en-us/cpp/error-messages/compiler-errors-1/compiler-error-c2026?view=msvc-170)。本地 MSVC 14.44.35207 已复现同一错误。

## 变更

- 协议表按不超过 8 KiB 的字符串源码片段生成，编译器在编译期拼接为原有的 `constexpr char[]`，不引入运行时读文件或额外初始化分配。按字节转义，保留 UTF-8、换行和其他字节。
- 新增原生测试，将编译后的两个完整数组与源 JSON 逐字节比对。
- 构建摘要优先保留最早的实际编译错误，再附日志尾部；完整日志仍上传为诊断产物。
- 现有四平台 Actions 构建复用于发布。版本标签必须匹配 CMake 项目版本；全部构建、测试和 Ubuntu 安装验证通过后，检查五个发行包及校验值，再发布 Release。
- Windows 使用 ZIP；Linux/macOS 在构建 runner 上生成 tar.gz，保留权限及软链接。发行记录包含源提交和构建地址。

## 本地验证

- MSVC：修复后的头文件编译成功，执行原生比对测试，44,784 / 19,782 字节全部一致。
- MinGW Release：完整核心、桥 EXE、桥 DLL、原生测试构建完成。
- 构建/发布工具：11 个测试，Windows 本机通过 10 个；Unix 权限及软链接测试留给 Unix runner 执行。
- Actions 配置通过 actionlint 1.7.12；README 与文档通过 `git diff --check`。
- 本机 CTest 的 `native_units` 通过；新编译的 MinGW C ABI 和协议比对程序被系统应用控制拦截（WinError 4551）。未修改系统策略，完整运行验证交由官方 Windows runner；同一协议比对代码使用 MSVC 编译后已在本机执行通过。

云端构建及首个 Release 链接待本次推送后的实际结果补充。
