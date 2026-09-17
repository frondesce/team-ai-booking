# Team AI Booking · 团队 AI 预约平台

为团队共享的模型服务提供时段预约和个人 API key 管理。成员使用自己的客户端，代理检查预约权限后转发请求，支持普通响应和 SSE 流式输出。

```text
客户端 → Team AI Booking（个人 key + 预约检查）→ 模型服务
浏览器 → 预约页面 / 管理后台
```

## 功能与规则

- 用户名、密码登录；管理员创建成员，管理个人 key 和每日额度。
- 北京时间每天 09:30–18:30，每段一小时，可预约今天、明天、后天，含周末和节假日。
- 同一时段只属于一人；每人每天可预约 1、2 或 3 段，由管理员设置。
- 只允许预约尚未开始的时段，开始前可以取消并返还额度。
- 到期后拒绝新请求，已经放行的请求继续完成。
- 管理员可立即开启维护、手动恢复；维护期间暂停新预约和新推理请求，受影响的预约作废。

## 快速开始

在项目目录中安装 Python 依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
```

编辑 `config.json`：将 `backend_url` 设为模型服务地址，`model_alias` 设为后端接受的模型名，`public_base_url` 设为成员访问本平台的地址。模型服务需要鉴权时，通过环境变量 `LLAMA_BACKEND_KEY` 提供后端 key，例如在 Bash 中交互输入：

```bash
read -r -s -p 'Backend API key: ' LLAMA_BACKEND_KEY
export LLAMA_BACKEND_KEY
```

创建管理员并启动，以下命令自动读取当前目录的 `config.json`：

```bash
python -m app.cli create-admin
python run.py
```

本机访问 `http://127.0.0.1:8000/app/`。管理员创建成员后，成员登录、修改初始密码、生成个人 key 并预约时段。

客户端填写本平台的 `<public_base_url>/v1`、个人 key 和模型名。支持：

- `POST /v1/chat/completions`
- `POST /v1/completions`
- `POST /completion`

不提供 `/v1/models`，客户端需支持手动填写模型名。后端必须支持所使用的接口；项目不负责协议转换、启动模型或管理 GPU。

## 部署

采用 Python、SQLite 和简单网页，按单实例部署，数据默认保存在当前目录的 `data/`。本地配置、凭据、数据库和测试文件不包含在此发布目录中。

HTTPS、网络访问限制由部署者配置。生产配置与数据备份说明见 [DEPLOYMENT.md](DEPLOYMENT.md)。
