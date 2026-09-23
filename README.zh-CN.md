# Team AI Booking · 团队 AI 预约平台

[English](README.md) | 简体中文

为团队共享的模型服务提供时段预约和个人 API key 管理。成员使用自己的客户端，代理检查预约权限后转发请求，支持普通响应和 SSE 流式输出。当前网页界面为简体中文。

```text
客户端 → Team AI Booking（个人 key + 预约检查）→ 模型服务
浏览器 → 预约页面 / 管理后台
```

## 功能与规则

- 用户名、密码登录；管理员创建成员，管理个人 key 和每日额度。
- 北京时间每天 09:30–18:30，每段一小时，可预约今天、明天、后天，含周末和节假日。
- 按模型分别预约，每个模型独立设置每时段人数上限（默认 1 人），管理员可在后台调整。
- 每人每天共用正整数预约额度，由管理员设置；同时预约两个模型占用两次额度，同一模型、同一时段不可重复预约。
- 未开始的时段允许提前预约，开始前可以取消并返还额度；已开始但有空位的时段可直接立即使用且不消耗个人每日额度，时段开始后不可自行取消。
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

编辑 `config.json`：将 `backend_url` 设为模型服务地址，`model_name` 设为后端接受的模型名，`public_base_url` 设为成员访问本平台的地址。模型服务需要鉴权时，通过环境变量 `LLAMA_BACKEND_KEY` 提供后端 key，例如在 Bash 中交互输入：

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

## 多模型与预约人数

单模型场景可直接配置顶层 `model_name`（`booking_models` 保持省略或为 `null`），作为便捷配置使用，默认每时段 1 人。需要配置多个模型或自定义模型 ID 时，在 `config.json` 中配置 `booking_models` 并重启：

```json
{
  "booking_models": [
    {"id": "model-a", "model_name": "model-a"},
    {"id": "model-b", "model_name": "model-b"}
  ]
}
```

`model_name` 必须填写后端网关接受的实际模型名。保持 `id` 稳定。任何模型 ID 均可按需移除，但模型列表中仍须至少保留一个模型。成员在预约页面选择模型，在客户端请求的 `model` 字段填写对应模型名称。代理按该模型检查预约，预约 Model A 不能调用 Model B。配置多个模型后，所有支持的推理接口均须填写 `model`；只有一个模型时可省略并自动使用该模型，明确填写未知模型名仍会被拒绝。

在 `/app/admin` 分别设置每个模型的每时段人数，例如 Model A 2 人、Model B 1 人。人数持久化到 SQLite，保存后立即生效；调低人数不取消已有预约，人数达到或超过上限的时段不能新增预约。所有模型共用 `backend_url` 和后端凭据，由后端网关（例如 New API）按模型名路由、在多个模型实例之间分配请求。预约人数不负责分配 GPU、选择实例或限制推理请求并发数。

本版本首次启动时创建新数据库，后续重启沿用该数据库，不支持原地升级旧数据库。重新部署或全新上线时，请使用新的空数据目录（或自行备份移走旧目录数据），并重新创建管理员账号；程序不会自动删除旧数据库。正常运行中，若将某个模型从配置中移除并重启，程序会在启动事务中停用该模型，停止接受其新预约和 API 调用，并自动取消该模型所有未结束（含进行中与未来）的有效预约，返还对应每日额度并记录审计日志。已结束的预约保留，仍计入当天额度。重新加入相同模型 ID 会保留原时段容量，但不恢复已取消的预约。

## 部署

采用 Python、SQLite 和简单网页，按单实例部署，数据默认保存在当前目录的 `data/`。本地配置、凭据、数据库和测试文件不包含在此发布目录中。

HTTPS、网络访问限制由部署者配置。生产配置与数据备份说明见 [DEPLOYMENT.md](DEPLOYMENT.md)。

## License

[MIT](LICENSE) © 2026 frondesce.
