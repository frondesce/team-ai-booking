# 部署与维护

先按 [README](README.md) 安装依赖、配置后端并创建管理员。程序按单实例运行；以下配置中的目录和系统用户均为示例。

## 配置

默认读取当前工作目录的 `config.json`，也可以使用 `python run.py --config /path/to/config.json`。CLI 指定配置的示例：`python -m app.cli create-admin --config /path/to/config.json`。

| 配置项 | 用途 |
|---|---|
| `host` / `port` | 监听地址和端口；前置代理部署时可设为 `127.0.0.1:8000` |
| `data_dir` | 数据目录，默认 `./data`，相对路径以启动工作目录为基准 |
| `backend_url` | 模型服务根地址，例如 `http://127.0.0.1:8080` |
| `backend_key` | 后端凭据，建议使用环境变量 `LLAMA_BACKEND_KEY` 提供 |
| `public_base_url` | 成员访问地址，例如 `https://ai.example.com`，不包含 `/v1` |
| `model_alias` | 后端接受的模型名，供客户端填写 |
| `session_lifetime_hours` | 网页会话有效期，默认 12 小时 |
| `cookie_secure` | HTTPS 部署设为 `true`；本机 HTTP 调试使用 `false` |
| `connect_timeout` / `read_timeout` | 后端连接/读取超时；长流式响应默认 `read_timeout: null` |

也支持 `LLAMA_PROXY_HOST`、`LLAMA_PROXY_PORT`、`LLAMA_PROXY_DATA_DIR`、`LLAMA_PROXY_DB_PATH`、`LLAMA_BACKEND_URL`、`LLAMA_PUBLIC_BASE_URL`、`LLAMA_MODEL_ALIAS`、`LLAMA_SESSION_LIFETIME_HOURS` 和 `LLAMA_COOKIE_SECURE`，环境变量优先于配置。`LLAMA_*` 命名保留用于配置兼容。

配置文件和数据目录只允许运行用户及必要的管理员访问，不提交到版本控制。

## 常驻服务示例

假设代码位于 `/opt/team-ai-booking`，虚拟环境为 `.venv`，运行用户为 `ai-booking`。先准备对应用户、目录权限和本地配置，再使用以下 systemd unit：

```ini
[Unit]
Description=Team AI Booking
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ai-booking
WorkingDirectory=/opt/team-ai-booking
EnvironmentFile=/etc/team-ai-booking.env
ExecStart=/opt/team-ai-booking/.venv/bin/python run.py --config config.json
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=multi-user.target
```

环境文件由部署者创建并限制读取权限，提供需要的 `LLAMA_*` 配置。后端无需 key 时也应创建此文件，可以为空。启动前通过相同配置和数据目录创建管理员。

前置 HTTPS 代理应关闭响应缓冲与缓存，支持 HTTP/1.1 和长连接，并按实际生成耗时设置读取超时。网络层限制后端直连，保证成员的调用经过预约检查。

## 日常操作

- 重置管理员密码：在项目目录运行 `python -m app.cli reset-admin`。
- 管理成员、每日额度、预约和系统维护：登录后访问 `/app/admin`。
- 维护状态会持久化；关闭维护后，不恢复已经作废的预约。
- 额度调低不会取消已有预约，只限制后续新增。

## 数据备份

SQLite 使用 WAL 模式。可用 SQLite CLI 的在线备份命令，避免只复制正在运行的主数据库文件：

```bash
sqlite3 data/reservation.db ".backup '/path/to/backups/reservation.db'"
```

备份包含账号、会话及预约数据，应按私有数据保管。恢复时先停止程序，将原数据库及同名 `-wal`、`-shm` 文件整体移到备份目录，再把备份放回配置的数据库路径，确认权限后启动。不要将旧 WAL 文件与恢复后的数据库混用。
