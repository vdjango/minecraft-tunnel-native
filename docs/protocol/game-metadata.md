# 游戏环境与内容同步

## 统一内容约定（2026-10-28）

Minecraft 版本、加载器类型和完整模组集合用于联机匹配。加载器构建版本不同显示警告，优先安装房主版本；房主版本不可用时选择同游戏版本的同类加载器。资源包、光影均为可选外观内容，缺少、拒绝分享或下载失败不会使必需模组的准备失败。

桥同时扫描实例的 `mods`、`resourcepacks`、`shaderpacks`。必需模组保持原有 `mods_count/mods_hash/mods`；新增 `resources_count/resources_hash/resources`，资源预览当前最多 8 项，节点校验兼容最多 24 项，完整清单最多 2000 项。数量、摘要在无预览的房间目录中也保留。

`mods.manifest` 与 `mods.fetch` 支持 `kind:"resources"`；省略时仍为 `mods`。资源清单项含 `name,path,size,sha256,sha1`，路径只允许 `resourcepacks/<name>`、`shaderpacks/<name>`；两目录可有同名文件，同目录大小写重名拒绝。分页每页 50 项、大小上限 2 GiB、并发 4、临时写入与 SHA-256 校验保持一致。文件内容只走已授权 P2P 通道；目录元数据可走认证控制通道。旧桥/旧节点没有资源字段或不支持 kind 时跳过可选同步。

```json
{"jsonrpc":"2.0","id":30,"method":"mods.manifest","params":{"session":"<访客session>","kind":"resources"}}
{"jsonrpc":"2.0","id":31,"method":"mods.fetch","params":{"session":"<访客session>","kind":"resources","item":{"name":"textures.zip","path":"resourcepacks/textures.zip","size":1234,"sha256":"<完整清单中的SHA-256>"},"target":"<独立实例>/resourcepacks/textures.zip"}}
```

启动器 `.buggpack` 与房间同步共用相对路径、大小、SHA-256/SHA-1 和游戏环境语义。文件包以 `buggcraft.pack.json`、`format:"buggcraft"`、`formatVersion:1` 标识，增加 `embedded` 和 `downloads`，内置文件位于 `files/<path>`。精简导出只引用已知来源且与本地内容相符的文件，修改或无来源文件内置；常规导出全部内置实例内容。导入始终校验 SHA-256，先安装游戏和加载器，再原子发布独立实例。核心 JAR、加载器安装器不内置，因此常规包仍可能需要联网。导出包可包含存档与配置，房间同步不自动复制这些文件，也不会创建服务器磁盘上的整合包副本。

对应 `bridge/rpc.cpp`、`bridge/modpack.cpp`、`web/tunnel/directory.py` 和 `web/tunnel/federation.py`。环境信息属于房间目录和本机桥协议，不是独立 HTTP 模组上传接口。

## 字段来源

房主通过 `room.host.params.settings.game` 或 `room.retarget.params.game` 发布环境。桥不通过 JAR 自动识别版本与加载器。

| `game` 字段 | 来源 | 含义与限制 |
|---|---|---|
| `version` | 启动器实例配置 | 最多 64 字符，未知可为空 |
| `protocol` | 启动器或游戏状态探测 | 0–2147483647 整数，未知为 `null` |
| `loader` | 启动器实例配置 | 最多 32 字符，例如 `fabric`、`forge` |
| `loader_version` | 启动器实例配置 | 最多 64 字符，未知可为空 |
| `mods_count` | 桥扫描 `mods` | 完整数量；节点接收 0–10000，当前桥最多扫描 1000 个 JAR |
| `mods_hash` | 桥计算完整清单 SHA-256 | 空清单/未扫描时可能为空或缺失，不要自行改用另一种 JSON 序列化重算 |
| `mods` | 桥生成预览 | 最多 24 项，每项为 `name,size,sha256`；不是完整清单 |

节点清理文本中的不可打印字符和方括号并截断。预览文件名最多 64 字符、不能含路径且以 `.jar` 结尾；大小为 0–2147483647 整数，SHA-256 为 64 位小写十六进制。完整清单的名称限制不同，部分长文件名/大文件可能不出现在预览。

## 房主流程

```json
{"jsonrpc":"2.0","id":10,"method":"room.host","params":{"node":"<node>","game_port":0,"game_directory":"<Minecraft实例根目录>","transport":"auto","settings":{"public":true,"require_invite":true,"game":{"version":"<Minecraft版本>","protocol":null,"loader":"fabric","loader_version":"<加载器版本>"}}}}
```

桥在 `game_directory` 下追加 `mods`，不要传成 `实例/mods`。扫描生成每个 JAR 的 SHA-256，并把摘要合并到房间信息。`game_port:0` 创建等待房间；未提供目录时，可在等待阶段补充：

```json
{"jsonrpc":"2.0","id":11,"method":"mods.configure","params":{"session":"<房主session>","directory":"<Minecraft实例根目录>"}}
{"jsonrpc":"2.0","id":12,"method":"room.retarget","params":{"session":"<房主session>","port":25565,"game":{"version":"<Minecraft版本>","protocol":null,"loader":"fabric","loader_version":"<加载器版本>"}}}
```

游戏端口启用后不能再用 `mods.configure` 更换目录。扫描不是持续文件监控，运行中修改模组不能当作已经重新发布；环境改变后应重新准备房间。

## 访客流程

1. `room.list` 读摘要。本地房间列表省略 `game.mods`，联邦公开目录可以带预览；客户端必须兼容预览缺失。
2. 邀请房间通过 `room.join` 加入；允许直接加入的公开房间先 `room.resolve` 再加入，不能通过公开解析绕过邀请保护。
3. `mods.manifest`：房主本地直接返回分页；访客先得到异步 `transfer`，再轮询 `mods.status`。
4. 完成后读取 `items`、`offset`、`total`；用 `offset + items.length` 请求下一页，直到达到 `total`。每页最多 50 项，不能用 24 项预览代表全量。
5. 用户确认后，对每项调用 `mods.fetch`，传完整 `item` 和本机 `.jar` 目标路径 `target`；轮询状态，结束后用 `mods.cancel` 释放保留任务。

```json
{"jsonrpc":"2.0","id":20,"method":"mods.manifest","params":{"session":"<访客session>"}}
{"jsonrpc":"2.0","id":21,"method":"mods.status","params":{"session":"<访客session>","transfer":"<返回的transfer>","offset":0}}
{"jsonrpc":"2.0","id":22,"method":"mods.fetch","params":{"session":"<访客session>","item":{"name":"example.jar","size":1234,"sha256":"<清单中的64位小写SHA-256>"},"target":"<本机实例>/mods/example.jar"}}
{"jsonrpc":"2.0","id":23,"method":"mods.cancel","params":{"session":"<访客session>","transfer":"<已完成或待取消的transfer>"}}
```

占位符不能原样发送。最多 4 个活动传输、16 条保留任务，单文件上限 2 GiB。下载先写临时文件，校验大小与 SHA-256 后提交到目标。哈希一致不证明 JAR 安全；启动器负责来源提示、下载确认、覆盖决策、加载器安装和游戏重启。

## 下载源优先与 P2P 兜底

启动器先按「模组与整合包下载源」查找并下载完全相符的内容，验证大小、SHA-1 和最终 SHA-256。该设置与 Minecraft / 加载器下载源独立。来源不可用时，仅已经建立 P2P 游戏直连的访客会话才可调用文件下载；中继或协商中禁止向房主请求文件。文件 PeerConnection 独立协商，若暂未就绪可有界重试；P2P 游戏连接丢失时停止文件传输。房主仍须授权共享，文件数据不能回退 SSH。清单元数据可经已认证控制通道读取。

## 联邦与缺失字段

公开房间签名公告包含环境摘要和预览；私密房间跨节点只公布随机房间 ID，不公开标题、环境或邀请密钥。签名证明公告来源，不能证明房主环境与游戏进程完全一致。

缺少 `game`、空版本、`null` 协议或缺少摘要/预览均表示未知，不能据此判定原版、无模组或完全匹配。同步不包含存档、配置目录或加载器安装包；资源包和光影按上面的可选内容规则处理。其他方法见[桥接协议](launcher-bridge.md)，风险见[安全说明](../security.md)。
