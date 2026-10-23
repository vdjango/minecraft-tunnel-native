# 节点部署

Ubuntu 24.04 amd64 推荐使用安装包。安装、升级都使用同一个命令，配置与身份保存在 `/var/lib/buggcraft-node`：

```bash
sudo apt install ./buggcraft-node_*_amd64.deb
sudo buggcraft-node setup
```

安装包来自 [Actions 成功构建](https://github.com/vdjango/minecraft-tunnel-native/actions/workflows/core.yml) 的 `buggcraft-node-ubuntu24.04-amd64` 产物，包含 `.deb` 和 SHA-256 文件。下载并解压后可用 `sha256sum -c 文件名.deb.sha256` 核对完整性；这不替代下载来源的可信性。当前没有在线 APT 软件源。

软件安装到 `/usr/lib/buggcraft-node`，以专用 `buggcraft-node` 用户运行。C++、Web 和私有 Python 依赖一起提供，安装时不运行 pip。首次设置链接仅在初始化期间存在，数据目录为 `0700`，链接文件为 `0600`。已有配置不会重新进入初始化。

| 操作 | 命令 |
| --- | --- |
| 查看初始化链接或管理页面地址 | `sudo buggcraft-node setup` |
| 查看服务状态 | `sudo buggcraft-node status` |
| 查看最近日志 | `sudo buggcraft-node logs` |
| 重启服务 | `sudo systemctl restart buggcraft-node` |
| 停止服务 | `sudo systemctl stop buggcraft-node` |

升级会重启服务并中断该节点现有连接，应安排维护时间。升级前停止服务并备份整个 `/var/lib/buggcraft-node`，保留旧安装包；回退可用 `sudo apt install --allow-downgrades ./旧版本.deb`，状态格式变化时还需恢复对应备份。卸载及 purge 均保留节点数据，避免误删身份、账号和日志。

安装包支持范围目前为 Ubuntu 24.04 / Python 3.12；其他平台使用 [源码构建](build.md)。下面的手动 systemd 方式供源码部署使用，不需要和安装包同时执行。

## 配置与数据

通过 `--directory` 指定持久化目录。不存在 `server.conf` 时进入 HTTPS 初始化，完成后同一个命令直接启动节点。初始化生成配置、主机密钥、节点身份及 Web 状态；整个数据目录应仅允许运行账号访问，并在升级前备份。

如已有完整配置，可使用：

```bash
export BUGGCRAFT_NATIVE_CORE=/opt/minecraft-tunnel-native/runtime/bin/buggcraft-core
/opt/minecraft-tunnel-native/.venv/bin/python \
  /opt/minecraft-tunnel-native/web/main.py --config /var/lib/buggcraft-node/server.conf
```

`config.example.ini` 是核心参数示例，不能替代完整 Web 初始化。Web 管理中填写的节点入口应是玩家可访问的地址；`0.0.0.0` 只表示监听所有网卡。

默认开放 TCP/UDP `3333` 和 TCP `18443`，修改配置后以实际端口为准。首次设置的自签名证书应核对终端指纹，生产 Web 使用受信任证书或 HTTPS 反向代理。设置链接与个人接入码包含授权信息，不应公开。

## systemd

以下示例约定源码位于 `/opt/minecraft-tunnel-native`，虚拟环境为其中的 `.venv`，原生安装目录为 `runtime`。运行账号需要读取这些文件；持久化数据由 systemd 创建。

新安装时创建服务账号：

```bash
sudo useradd --system --user-group --home-dir /var/lib/buggcraft-node \
  --no-create-home --shell /usr/sbin/nologin buggcraft-node
```

将以下内容保存为 `/etc/systemd/system/buggcraft-node.service`：

```ini
[Unit]
Description=Buggcraft native Minecraft node
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=buggcraft-node
Group=buggcraft-node
WorkingDirectory=/opt/minecraft-tunnel-native
Environment=BUGGCRAFT_NATIVE_CORE=/opt/minecraft-tunnel-native/runtime/bin/buggcraft-core
ExecStart=/opt/minecraft-tunnel-native/.venv/bin/python -u /opt/minecraft-tunnel-native/web/main.py --directory /var/lib/buggcraft-node --no-browser
Restart=on-failure
RestartSec=3
TimeoutStopSec=20
StateDirectory=buggcraft-node
StateDirectoryMode=0700
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
LimitNOFILE=8192

[Install]
WantedBy=multi-user.target
```

启用服务并查看初始化链接或启动结果：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now buggcraft-node
sudo journalctl -u buggcraft-node -n 80 --no-pager
```

已有数据应迁移到 `/var/lib/buggcraft-node` 并归属该服务账号。使用其他路径时，需要同时调整 unit 的访问权限和写入范围。

## 容量与升级

| `[relay]` 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `workers` | CPU 数，最多 8 | 原生工作线程，允许 1–64 |
| `buffer_kib` | `64` | 每方向缓冲容量，允许 32–4096 KiB |
| `compression` | `auto` | 自适应压缩；`off` 关闭 |
| `max_connections` | `512` | SSH 连接数上限 |
| `max_rooms` | `128` | 房间数上限 |
| `max_total_streams` | `512` | 全局转发流数上限 |
| `max_streams_per_room` | `128` | 单房间流数上限 |
| `max_streams_per_client` | `8` | 单客户端流数上限 |

以上为入口未显式配置时的默认值，向导或现有配置可覆盖它们。玩家、SSH 连接与游戏流不是一一对应，不能用这些上限推算玩家容量。根据实际 CPU、出口带宽、延迟及背压记录逐步调整；增大缓冲会增加内存需求。

升级前备份持久化目录并保留上一版代码、依赖和原生运行目录。先在独立目录完成构建与测试，确认完整依赖可加载，再停止服务、切换版本并重启。重启会中断该节点上的现有连接，应安排维护时间。回退时切回保留的完整版本，状态格式有变化时同时恢复对应备份。

上线验收至少包含外部玩家的 A 建房/B 加入、实际游戏数据交换、断开重连及逐玩家日志检查。进程存活或 `--version` 返回成功不能代替这些验收。
