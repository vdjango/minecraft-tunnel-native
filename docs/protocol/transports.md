# 游戏传输与保活协议

## 游戏连接与故障恢复

### P2P 存活探测

每对玩家的原生桥共用一条 `probe` DataChannel 探活，不为每条游戏流单独创建心跳。先交换文本 `BCH1?` / `BCH1!` 协商能力；未获得响应最多探测 3 次，旧版仍使用 ICE/SCTP 自带的存活检测，不按新协议判超时。支持方每 5 秒发送 `BCP1:<递增序号>`，对端回复 `BCA1:<原序号>`；仅当前探测的有效回复延长 20 秒存活期限，重复、无效或仅单向收到的 ping 均不能续期。每帧最多 32 字节，回复限频。

直连失效仅释放对应玩家的 P2P 流，保留健康 SSH、房间、本地监听地址及已有中转流；新游戏连接走中转。信令仍可用时，以 2/4/8/16/30 秒退避重新协商直连，成功后新连接优先直连，已有中转流继续原路径。首次协商超时也会退避重试。SSH 自身失效仍按房间重连处理。信令子通道故障不等于 SSH 故障，不应关闭健康 SSH。底层 ICE/SCTP 可能早于应用心跳发现故障。

游戏 DataChannel 使用库分配并回收的 SCTP 流编号，不使用会耗尽协商范围的单调递增编号；创建失败时，仅该新游戏连接退回中转。日志包含 `peer_heartbeat_enabled`、30 秒汇总 `peer_heartbeat_sample`、`peer_heartbeat_timeout`、`peer_fallback`、`peer_retry_started`、`peer_recovered` 及游戏流的 `sctp_stream`，按玩家分别落盘。


## 高级目录与聊天协议

### SSH 目录和转发

原生目录与转发协议作为高级扩展保留给不采用此连接组件的实现者：接入码包含 `server_host`、`server_port`、`token`、`server_public_key`。必须固定校验 SSH 主机公钥；目录用户名 `directory:rooms`，密码为 JSON 字符串 `{"token":"…"}`。房主用户名为 `host:ROOM_ID`、访客为 `client:ROOM_ID`，密码另含 `secret`。SDK 的 SSH 隧道直接支持中转模式；若第三方选择原生实现，必须自行处理安全、拥塞、重连、权限和协议兼容，且不会自动获得 P2P 能力。


### 聊天

聊天子系统 `buggcraft-chat-v1`：先发 `{"op":"hello","name":"昵称"}`，然后 `{"op":"send","text":"你好"}`；文本最多 256 UTF-16 单元，每帧最多 4096 字节，访客每 10 秒最多 10 个请求。服务端发 `ready`、`room`、`message`、`bridge`、`error`；历史消息以 `message` 加 `replay:true` 重放。好友认证使用消息内服务端确定的 `account`，不可相信自报昵称。
