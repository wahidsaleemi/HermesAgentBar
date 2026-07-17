# Hermes AgentBar — host bridge (`commandbar_bridge.py`)

A minimal, **read-only** HTTP bridge that serves Command Code token usage from
Hermes' `state.db` to the Windows tray app over plain HTTP.

* stdlib only (`http.server`) — no Flask or third-party dependencies.
* Opens the DB with `file:...?mode=ro`; **never writes**.
* Binds loopback (`127.0.0.1`) by default.
* Optional Bearer-token auth; graceful 503 if the DB is busy/locked.

## Run it

```bash
cd /home/wahid/HermesAgentBar
python3 host/commandbar_bridge.py
# Hermes AgentBar bridge listening on http://127.0.0.1:8766 (no auth, loopback)
```

## Environment variables

| Variable                   | Default                          | Purpose                                   |
|----------------------------|----------------------------------|-------------------------------------------|
| `HERMES_AGENTBAR_HOST`     | `127.0.0.1`                      | Bind address (keep loopback for safety).  |
| `HERMES_AGENTBAR_PORT`     | `8766`                           | Bind port.                                |
| `HERMES_AGENTBAR_DB`       | `/home/wahid/.hermes/state.db`   | DB path (opened read-only).               |
| `HERMES_AGENTBAR_TOKEN`    | _(empty = no auth)_              | If set, require `Authorization: Bearer`.  |

Auth example:

```bash
HERMES_AGENTBAR_TOKEN=change-me python3 host/commandbar_bridge.py
# then: curl -H "Authorization: Bearer change-me" http://127.0.0.1:8766/api/usage
```

## Endpoint

`GET /api/usage` (also accepts `?windows=1`) → JSON:

```json
{
  "updated": "13:58:03",
  "windows": {
    "5h": {"input": N, "output": N, "cache_read": N, "cache_write": N, "reasoning": N, "sessions": N},
    "7d": { "same shape" }
  },
  "by_model": [{"model": "tencent/Hy3", "tokens": N}],
  "daily_7d": [{"day": "2026-07-17", "input": N, "output": N}]
}
```

* `windows.5h` / `windows.7d`: token sums for the last 5 hours / 7 days
  (computed from `last_seen >= now - (5*3600)` / `now - (7*86400)`).
  `sessions` = `COUNT(DISTINCT session_id)`.
* `by_model`: top 10 models, `tokens = input + output + cache_read` over 7d.
* `daily_7d`: last 7 calendar days, input/output per day.
* **No dollar figures** — token counts + percentages only (per project rule).

## Errors

* `404` — unknown path.
* `401` — auth enabled and token missing/wrong.
* `503` — `state.db` locked/busy (brief 50 ms busy_timeout first).

## systemd user service (optional)

`~/.config/systemd/user/hermes-agentbar.service`:

```ini
[Unit]
Description=Hermes AgentBar read-only bridge
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/wahid/HermesAgentBar/host/commandbar_bridge.py
Restart=on-failure
Environment=HERMES_AGENTBAR_TOKEN=change-me

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now hermes-agentbar
```
