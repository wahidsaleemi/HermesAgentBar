# Running Hermes AgentBar on Windows

Hermes AgentBar is a Windows-only system tray app. It talks to a small HTTP
bridge that runs on your **Linux host** (a separate machine or VM). The bridge
serves Command Code token usage over HTTP; the Windows app also talks directly
to OpenRouter and Command Code's billing API.

> No build step is required to develop — just run `python hermes_agentbar.py`.
> A standalone `HermesAgentBar.exe` can be produced with `build.ps1` (Windows only).

## 1. Linux host — start the bridge

The bridge is pure stdlib (`http.server`), so no extra deps are needed on Linux.

```bash
cd /path/to/HermesAgentBar
python3 host/commandbar_bridge.py
# Listens on http://0.0.0.0:8766. Optional shared token via env:
#   HERMES_AGENTBAR_TOKEN=your-shared-secret python3 host/commandbar_bridge.py
# The Windows app sends this token as `Authorization: Bearer` via the
# `bridge_token` field in its config.json.
```

## 2. Windows — install

```powershell
# Python 3.10+ required (from python.org or winget)
pip install -r requirements.txt
```

## 3. First run

```
python hermes_agentbar.py
```

The app creates `%APPDATA%\HermesAgentBar\config.json` with defaults.
Edit it to set `bridge_url` (LAN IP of your Linux host + port 8766).

## 4. Keeping the prompt free

Use `pythonw` instead of `python`:

```powershell
pythonw hermes_agentbar.py
```

`pythonw.exe` launches Python without a console window. Your command prompt
returns immediately. The tray icon appears as normal.

## 5. Configuration

See **README.md** for the full config reference. Key fields:

| Key | What |
|---|---|
| `bridge_url` | HTTP URL of the bridge (e.g. `http://192.168.100.52:8766`) |
| `bridge_token` | Shared secret if the bridge has auth |
| `openrouter_key` | `sk-or-v1-...` (optional — leave empty to hide) |
| `weekly_token_budget` | Token cap for the progress bar (0 = hidden) |
| `cc_session_cookie` | Full Cookie header from commandcode.ai browser session — enables Budget Status |
| `cc_5h_budget` / `cc_weekly_budget` | Your plan's USD caps (default 45 / 90) |

Config is re-read on every refresh — no restart needed.

## 6. Getting the `cc_session_cookie`

1. Log into commandcode.ai
2. F12 → Network → refresh → find `/internal/billing/credits`
3. Right-click → Copy → Copy as cURL (bash)
4. The `-b '...'` parameter is your cookie — paste the entire value into config.json

Cookie expires after ~7 days (Command Code session TTL).

## 7. Building a standalone .exe

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

Output: `dist/HermesAgentBar.exe` — single file, no Python install needed.

## 8. Auto-start with Windows

Press `Win+R`, type `shell:startup`, and drop a shortcut to `HermesAgentBar.exe`
or `pythonw.exe hermes_agentbar.py` into the folder.
