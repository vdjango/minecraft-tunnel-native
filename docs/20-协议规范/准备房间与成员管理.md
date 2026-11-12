# 准备房间、成员管理与日志授权

节点 `ping`、`describe` 声明能力 `room-lobby-v1`。桥继续使用现有 JSON-RPC `room.request`：`params` 为 `{session,request:{op,...}}`。缺少能力时客户端不展示可执行的成员管理操作。游戏数据传输与文件通道仍由 C++ 实现；`web/tunnel/room_lobby.py` 是节点的身份和授权控制层。

## 生命周期

`room.host` 支持 `game_port:0`，创建准备房间并发布实例清单。访客无需等待世界开放即可加入、聊天、读取清单和安装。世界开放后由启动器发现并调用 `room.retarget`；准备任务继续运行。只有一个候选世界时自动连接，多个候选时请玩家选择，版本不同不自动连接。

房间区分 `manager_id`（房间管理者）与 `provider_id`（世界和原始文件提供者）。转让管理权不会迁移存档或重启游戏通道。原提供者离开后端口关闭，仍在线的管理者保留准备房间。原管理者断线不自动选举他人。游戏 TCP 断线依旧需要游戏重新连接，不声明透明迁移。

公开准备房间即使还没有游戏端口，也会进入本节点及联邦的房间目录，查询结果标记为 waiting；访客可先按邀请码加入、聊天和安装。要求邀请的房间不会在 resolve 响应中泄露入房密钥，私密房间不出现在公开目录。

## 成员与准备状态

| `op` | 字段与结果 |
| --- | --- |
| `members` | `offset` 默认 0，`limit` 默认/最多 32；返回 `revision,self_id,manager_id,provider_id,config_id,config_revision,members,offset,next,total,online,ready,transfer,logs,blocked`。`blocked` 仅管理者可见。 |
| `member_update` | 当前 `config_id`，单调递增 `seq`，`task`（最多 64 个字母数字/下划线/短横线），`name`（最多 32 字符），`state,stage,done,total,speed,files_done,files_total,code`；发送者由节点绑定，不接受代他人上报。 |
| `member_path` | `path:p2p/relay/unknown/local`，`rtt_ms`（未知为 null），`rtt_kind:game/node/unknown/local`。`members` 可携带这些字段一起更新。 |

`state`：`idle/checking/downloading/installing/verifying/ready/failed/cancelled/starting/playing`。
`stage`：`game/java/libraries/assets/loader/mods/resources/configuration/verify/waiting`。
`done/total` 为当前阶段的单位，不把文件数量冒充字节速度；`speed` 是字节/秒。空闲、未知大小时使用阶段状态，不伪造百分比。

配置指纹涵盖 Minecraft、加载器及模组/可选资源摘要。更新配置将增加配置版本并重置准备状态；旧指纹会被拒绝。重复/倒序 `seq` 和已经退役任务的更新返回 `accepted:false`。桥约每 4 秒刷新成员快照，启动器合并约 1.6 秒内的进度上报；成员 20 秒没有心跳标为陈旧。成员变化事件只广播 `{type:"members",revision}`，客户端取授权快照。

SSH 凭据中的私有 `member_token`（32–128 字符）用于当前会话恢复，节点对房间和凭据进行散列得到公开成员 ID。公开 ID 和邀请码不是恢复凭据。未绑定账号的玩家重新创建身份不能被识别为同一账号，房间移出不等于全局永久封禁。

## 管理权限

| `op` | 授权和效果 |
| --- | --- |
| `member_kick` | 仅管理者，`member` 为在线目标；不能移出自己或世界提供者。断开目标 SSH、游戏及文件通道，记录该房间的身份/账号限制。 |
| `member_unblock` | 仅管理者，解除指定 `member` 的限制。 |
| `owner_offer` | 仅管理者，指定在线 `member`；返回含 `id,from,to,expires` 的转让请求，30 秒过期。 |
| `owner_answer` | 仅目标本人，`offer` 与显式布尔 `accept`；接受后管理权转移，拒绝保持原样。 |
| `owner_cancel` | 仅管理者，取消未完成请求；任一方离线也取消。 |
| `configure` | 管理者可改标题/公开策略；世界提供者可发布游戏清单。新管理者不能通过改游戏字段冒充原提供者。 |
| `leave` / `close` | 管理者退出可关闭房间；普通成员退出只关闭自己。已转让的原提供者退出保留准备房间。 |

## 安装日志：必须由玩家授权

进度和结果不包含完整安装日志。所有日志接口都需要当前房间的真实成员连接。

| `op` | 字段与授权 |
| --- | --- |
| `logs_request` | 管理者请求另一在线 `member`；目标快照 `logs.request` 含 `id,from,name,expires`，90 秒过期。 |
| `logs_answer` | 目标本人提交精确 `request` ID 和显式布尔 `allow`。缺少允许不能默认授权。 |
| `logs_revoke` | 玩家停止共享，立即清空节点缓存和授权。 |
| `logs_append` | 仅已授权玩家上传自己的 `entries`，每批最多 10 行，每行 `seq,text,category,level`。节点只保留最近 200 行，不落盘。 |
| `logs_read` | 仅当前管理者读取已授权 `member`，按 `offset` 序号读取，每批最多 16 行；返回 `entries,next,first`。 |

日志分类为 `game/java/assets/libraries/loader/mods/resources/configuration/verify`，级别 `info/warning/error`。客户端在发送前过滤凭据、下载地址和本地路径，节点再次过滤，限制文本长度；返回的是纯文本。转让管理者、玩家/管理者断线、退出或踢出都会清空相应授权与缓存，新管理者必须重新申请。撤回后房主已经自行保存的副本无法远程删除。

与成员权限相关的实际 C++ 进程集成用例见 `tests/test_room_lobby.py`；客户端点击用例见启动器 `tests/test_room_lobby_ui.py`。真实游戏与隔离 HTTP 测试的范围在各仓库验收报告中分别记录。
