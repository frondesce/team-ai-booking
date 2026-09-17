# Team AI Booking

English | [简体中文](README.zh-CN.md)

A lightweight booking and API access proxy for teams sharing a model server. Members use their preferred clients with personal API keys; the proxy checks their reservations before forwarding requests. Supports regular responses and SSE streaming.

**The web interface is currently available in Simplified Chinese only.**

```text
API client → Team AI Booking (personal key + reservation check) → Model server
Browser    → Booking page / Admin dashboard
```

## Features and booking rules

- Username/password login, with administrator-managed accounts, personal API keys, and daily quotas.
- One-hour slots from 09:30 to 18:30 in `Asia/Shanghai`, available today and the next two calendar days, including weekends and holidays.
- One member per slot; administrators assign each member a daily limit of 1, 2, or 3 slots.
- Only slots that have not started can be booked. Cancel before a slot starts to restore the quota.
- New requests are rejected when the reservation expires; requests already admitted can finish.
- Administrators can enable maintenance immediately and end it manually. Maintenance blocks new bookings and inference requests, and invalidates affected reservations.

## Quick start

Install the Python dependencies from the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
```

Edit `config.json`: set `backend_url` to your model server address, `model_alias` to a model name accepted by the backend, and `public_base_url` to the address members will use to access this platform. If the backend requires authentication, supply its key through `LLAMA_BACKEND_KEY`. For example, enter it interactively in Bash:

```bash
read -r -s -p 'Backend API key: ' LLAMA_BACKEND_KEY
export LLAMA_BACKEND_KEY
```

Create the administrator and start the service. Both commands automatically read `config.json` from the current directory:

```bash
python -m app.cli create-admin
python run.py
```

Open `http://127.0.0.1:8000/app/` locally. The administrator creates member accounts; members then sign in, change their initial passwords, generate personal keys, and book slots.

Configure clients with `<public_base_url>/v1`, a personal API key, and the model name. Supported endpoints:

- `POST /v1/chat/completions`
- `POST /v1/completions`
- `POST /completion`

`/v1/models` is not provided, so clients must support entering the model name manually. The backend must support the endpoint being used. This project does not translate API protocols, start models, or manage GPUs.

## Deployment

Built with Python, SQLite, and a simple web interface. Run a single instance; data is stored in `data/` under the working directory by default. Local configuration, credentials, databases, and test files are excluded from this distribution.

Configure HTTPS and network access restrictions for your deployment. See [DEPLOYMENT.md](DEPLOYMENT.md) (Chinese) for production configuration and database backup instructions.

## License

[MIT](LICENSE) © 2026 frondesce.
