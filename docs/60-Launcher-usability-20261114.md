# 启动器诊断与网络参考对齐

2026-11-14。`feedback.submit` 仍为唯一桥接口，输入 `node,archive,metadata,consent:true`；托管服务还必须给出 `origin`，与当前 ControlClient 的 origin 完全一致。metadata 包含 `id,message,expected,contact`。该接口在 RPC worker 执行，不在 Qt GUI 收集或上传文件；拒绝同意时不读附件。

托管账号路径复用 ControlClient 授权及按需续期，读取本地常规非符号链接 ZIP（最多 8 MiB），计算 SHA-256 和 Base64，通过认证的 `/v1/feedback/submit` 提交。仅该认证接口允许 12 MiB HTTP 请求体及 30 秒超时，其余 HTTPS 请求限制不变。核对 `accepted,id,sha256` 后才报告成功，异常沿用桥 Failure。旧 SSH 节点继续使用原来的 20 MiB 流式上传，未复制节点上传实现。

`network.inspect` 复用已有 STUN 探测与 5 分钟缓存。同一套接字至少取得两个不同服务器 IP 的有效观测且公网 IP 一致后，才比较映射端口；多个服务器共享 IP 或出口不同，不把这些结果当作稳定/依赖映射证据。只能证明 UDP 往返时保留较弱的可达状态。它是直连条件参考，不能推断所有 NAT 过滤行为或保证连接成功。直连/中转计数来自实际成功会话。

启动器指定 `BUGGCRAFT_BRIDGE_LOG_DIR`，桥继续使用现有有界日志所有者。新启动的桥进入当前启动器缓存的 `logs/bridge`；缓存迁移后下一桥进程使用新位置。日志附件通过启动器主动反馈并脱敏，不自动上传。

Windows MinGW 原生构建及安装证据为 `validation-artifacts/usability-20261114-build.log`、`usability-20261114-install.log`。真实跨网络直连效果需另行实机验收，不以分类或回环冒充成功。

`usability-20261114-network-test.log` 验证同一服务器 IP 的多个端口只返回 UDP 可达观测，快照不保留公网地址；`usability-20261114-relay-test.log` 使用当前原生桥、真实本地 SSH/TCP 节点验证转发、完整数据、半关闭及成功计数。反馈端到端证据在控制服务的 `validation-artifacts/usability-20261114-real-bridge.log`，验证当前 Windows 桥经 HTTPS 正式身份提交且附件字节一致。
