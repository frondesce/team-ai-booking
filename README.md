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
- Book by model. Each model has its own capacity per time slot (default 1), editable in the admin dashboard.
- Administrators assign each member a shared positive-integer daily quota across all models. Booking two models at the same time consumes two bookings; duplicate bookings for the same model and time are rejected.
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

Edit `config.json`: set `backend_url` to your model server address, `model_name` to a model name accepted by the backend, and `public_base_url` to the address members will use to access this platform. If the backend requires authentication, supply its key through `LLAMA_BACKEND_KEY`. For example, enter it interactively in Bash:

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

## Multiple models and booking capacity

For single-model setups, setting `model_name` (with `booking_models` omitted or `null`) serves as a convenient shorthand configuration with capacity 1. To offer multiple models or custom model IDs, add `booking_models` to `config.json` and restart:

```json
{
  "booking_models": [
    {"id": "model-a", "model_name": "model-a"},
    {"id": "model-b", "model_name": "model-b"}
  ]
}
```

Use model names accepted by your backend gateway. Keep IDs stable across restarts. Any model ID can be removed, provided the list still contains at least one model. Members select a model on the booking page and send its exact name in the API request's `model` field. The proxy requires an active reservation for that model; a Model A booking cannot authorize Model B. When multiple models are configured, `model` is required on all supported inference endpoints. With one model, an omitted `model` defaults to that model; an explicit unknown model name is rejected.

Set each model's capacity in `/app/admin`, for example Model A 2 and Model B 1. Capacities are stored in SQLite and take effect immediately. Lowering a capacity preserves existing bookings and blocks new ones while the slot is full. All models share the same `backend_url` and backend credentials; configure your gateway (such as New API) to route model names and distribute requests across model replicas. Booking capacity does not allocate a GPU, choose a replica, or limit concurrent inference requests.

This version creates the database on first startup and reuses it on subsequent restarts. It does not support in-place upgrades of legacy databases. For new deployments or redeployments, use a new empty data directory (or move the old directory aside) and recreate the administrator account; the application does not automatically delete old databases. During normal operation, removing a model from the inventory and restarting deactivates the model: disables its new bookings and API access, cancels its running and future confirmed reservations in the startup transaction, restores the corresponding daily quota, and records an audit trail. Ended reservations remain unchanged and still count toward that day's quota. Re-enabling the same model ID preserves its capacity but does not restore cancelled reservations.

## Deployment

Built with Python, SQLite, and a simple web interface. Run a single instance; data is stored in `data/` under the working directory by default. Local configuration, credentials, databases, and test files are excluded from this distribution.

Configure HTTPS and network access restrictions for your deployment. See [DEPLOYMENT.md](DEPLOYMENT.md) (Chinese) for production configuration and database backup instructions.

## License

[MIT](LICENSE) © 2026 frondesce.
