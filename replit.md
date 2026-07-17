# VPS Admin Panel

Mini-PaaS / private cPanel built with Flask for managing a Debian VPS (systemd services, Nginx, deployments, system monitoring).

## How to run

The app starts automatically via the **"Start application"** workflow.

```
.venv/bin/python app.py
```

Runs on **port 5000**. Login at `/login`.

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `PANEL_USERNAME` | Login username | `admin` |
| `PANEL_PASSWORD` | Login password | `admin` |
| `SESSION_SECRET` | Flask secret key (set as a Replit Secret) | random |
| `PANEL_BASE_PATH` | URL prefix, e.g. `/panel_admin` | _(empty)_ |
| `PORT` | Listening port | `5000` |

## Dependencies

Installed in `.venv/` via `uv`:

```
flask>=3.0.0
psutil>=5.9.0
gunicorn>=21.0.0
```

To reinstall: `uv pip install --python .venv/bin/python -r requirements.txt`

## Notes

- Full functionality (service management, Nginx config, systemd) requires a real Debian/Ubuntu VPS with root access.
- On Replit, the UI and monitoring features work; system-level features will show errors since systemd/Nginx are not available.
- The production deployment uses gunicorn behind Nginx with a `/panel_admin` prefix.

## User preferences

- French-language project; keep comments and UI strings in French.
