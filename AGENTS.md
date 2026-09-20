# Repository Guidelines

## Project Structure & Module Organization

- `run.py` starts the threaded HTTP server; `app/server.py` routes requests.
- `app/web.py` handles browser workflows, and `app/proxy.py` forwards inference requests, including SSE streams.
- `app/reservation_engine.py` implements booking rules; `app/models.py` and `app/database.py` manage SQLite persistence. Authentication, configuration, and time helpers live in their corresponding `app/` modules.
- `app/templates/` contains Jinja2 HTML templates and embedded presentation assets. The interface uses Simplified Chinese.
- `README.md` and `README.zh-CN.md` document setup; `DEPLOYMENT.md` covers operations. Runtime data defaults to `data/`.

## Build, Test, and Development Commands

Run commands from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
```

Edit the local configuration with your backend URL and model name. Copy the example only when creating a new configuration.

- `python -m app.cli create-admin`: interactively create the initial administrator.
- `python run.py --host 127.0.0.1 --port 8000`: start locally; open `/app/`.
- `curl http://127.0.0.1:8000/health`: check that the running server responds.
- `python -m compileall -q app run.py`: check Python syntax; this does not validate behavior.

There is no separate build step.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions and variables, `PascalCase` classes, and uppercase constants. Follow neighboring code and retain type annotations on public helpers. Keep booking decisions in the reservation engine and database access in persistence modules. Preserve Chinese UI wording and update both READMEs when changing documented behavior. No formatter or linter is configured.

## Testing Guidelines

Tests are maintained separately; `.gitignore` excludes `tests/`, `test_*.py`, and `run_tests.sh`. This checkout defines no test framework, automated test command, or coverage threshold. Document manual validation in each PR: exercise affected login, booking, quota, maintenance, or proxy flows using disposable data. For proxy changes, check ordinary responses and SSE streaming. Check booking boundaries in `Asia/Shanghai`.

## Commit & Pull Request Guidelines

Recent commits use scoped subjects such as `feat(web): anonymize schedule reservations`; older commits use short imperative descriptions. Prefer concise, scoped subjects. PRs should explain the behavior change, link relevant issues, list validation performed, and include screenshots for UI changes.

## Security & Configuration

Keep credentials, local configuration, and databases untracked. Supply backend credentials through `LLAMA_BACKEND_KEY`. Run a single instance and follow `DEPLOYMENT.md` for HTTPS and SQLite backups.
