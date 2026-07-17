# Hermes AgentBar

A Windows system-tray app that shows **Command Code** token usage, live budget status, and **OpenRouter** credits at a glance.

<img width="373" height="578" alt="image" src="https://github.com/user-attachments/assets/79000977-4be1-4a0b-a846-db0e75e76ee6" />
<img width="374" height="574" alt="image" src="https://github.com/user-attachments/assets/5b56bac4-6cc9-49b5-862a-0cb40ef81952" />

Left-click the tray icon → a borderless popup opens with:
- **Budget Status** — live countdown to your 5‑hour and weekly USD caps (from commandcode.ai)
- **Token Usage** — collapsible section: 5 h / 7 d token cards, per‑model list, and a 7‑day purple bar chart
- **Weekly token budget** — user‑set token cap with progress bar (optional)
- **OpenRouter** tab — credit balance in USD with a lime‑green progress bar

Auto‑refreshes every 5 minutes (configurable). No dashboard tab to keep open, no CLI to poll.

---

## Requirements

| What | Why |
|---|---|
| **Command Code subscription** (paid plan) | Budget‑window data only exists for paid plans. The dashboard shows `$45`/5 h and `$90`/week caps. |
| **Hermes Agent** already set up on a Linux host | The bridge reads `~/.hermes/state.db` for token counts. |
| **Windows 10 / 11** with Python 3.10+ | The tray app is Windows‑only (pystray + pywin32). |
| **OpenRouter account** (optional) | If you want the OpenRouter tab. Leave `openrouter_key` empty to hide it. |
| **Network access** from Windows to the Linux host | The bridge binds `0.0.0.0:8766` by default; expose it on your LAN or use Tailscale. |

---

## Installation

### 1. Linux host — start the bridge

```bash
cd /path/to/HermesAgentBar

# Default: loopback only, no auth
python3 host/commandbar_bridge.py

# Recommended: shared token + bind all interfaces (so Windows can reach it)
HERMES_AGENTBAR_HOST=0.0.0.0 \
HERMES_AGENTBAR_TOKEN=your-shared-secret \
  python3 host/commandbar_bridge.py
```

The bridge has **zero dependencies** — pure stdlib `http.server`. It opens the DB read‑only and never writes.

**systemd user service** (optional — survives reboots):

`~/.config/systemd/user/hermes-agentbar.service`:
```ini
[Unit]
Description=Hermes AgentBar read-only bridge
After=network.target

[Service]
ExecStart=/usr/bin/python3 /path/to/HermesAgentBar/host/commandbar_bridge.py
Restart=on-failure
Environment=HERMES_AGENTBAR_HOST=0.0.0.0
Environment=HERMES_AGENTBAR_TOKEN=your-shared-secret

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now hermes-agentbar
```

### 2. Windows — install and run

```powershell
# Clone or unzip into a folder
cd C:\Users\you\HermesAgentBar

# Install runtime dependencies
pip install -r requirements.txt

# Run
python hermes_agentbar.py
```

The app creates `%APPDATA%\HermesAgentBar\config.json` on first run with sensible defaults.

---

## Configuration

Edit `%APPDATA%\HermesAgentBar\config.json` (right‑click the tray icon → **Open config**, or navigate there manually):

```json
{
  "bridge_url": "http://192.168.100.52:8766",
  "bridge_token": "your-shared-secret",
  "openrouter_key": "sk-or-v1-...",
  "refresh_minutes": 5,
  "weekly_token_budget": 70000000,
  "cc_session_cookie": "__stripe_mid=...; __Secure-commandcode_prod_.session_token=...",
  "cc_5h_budget": 45,
  "cc_weekly_budget": 90
}
```

| Key | Required? | What it does |
|---|---|---|
| `bridge_url` | **yes** | HTTP URL of the Linux bridge (LAN IP, Tailscale IP, or `127.0.0.1` if tunnelled) |
| `bridge_token` | if bridge has auth | `Authorization: Bearer` token matching the bridge's `HERMES_AGENTBAR_TOKEN` |
| `openrouter_key` | no | OpenRouter API key. Empty = OpenRouter tab shows "no key configured" |
| `refresh_minutes` | no | Polling interval (default 5). Minimum 1 |
| `weekly_token_budget` | no | User‑set weekly token cap. 0 = hidden. Progress bar appears on the CC tab |
| `cc_session_cookie` | no | Full Cookie header from a logged‑in browser session on commandcode.ai. Empty = budget‑status section hidden |
| `cc_5h_budget` | no | Your plan's 5‑hour USD cap (default 45) |
| `cc_weekly_budget` | no | Your plan's weekly USD cap (default 90) |

Changes take effect on the next refresh — no restart required (the app re‑reads `config.json` every cycle).

---

## Getting the `cc_session_cookie`

This is the most fiddly step. The budget‑status pane queries Command Code's internal billing API, which requires your browser session cookies (not your API key).

1. Open **commandcode.ai** in Chrome/Edge — make sure you're logged in
2. Press `F12` → **Network** tab
3. Refresh the page (the `/internal/usage` or `/internal/billing/credits` request will appear)
4. Right‑click that request → **Copy** → **Copy as cURL (bash)**
5. Paste the cURL into a text editor. Find the `-b '...'` parameter — that's your Cookie header
6. Copy the **entire value** inside `-b '...'` (it starts with `__stripe_mid=...` and ends with `...session_data=...`)
7. Paste it into `config.json` as the `cc_session_cookie` value

**Example cookie string** (truncated — yours is much longer):
```
__stripe_mid=42e8b3ef...; __stripe_sid=b6cf6d8b...; __Secure-commandcode_prod_.session_token=d4oOWk...; __Secure-commandcode_prod_.session_data=eyJzZXNza...
```

The cookie expires after ~7 days. When the budget‑status section goes blank, re‑extract it from your browser.

---

## Usage

| Action | How |
|---|---|
| **Open the popup** | Left‑click the tray icon |
| **Close the popup** | Left‑click the icon again (toggles open/closed) |
| **Manual refresh** | Right‑click → **Refresh now**, or the refresh button in the popup footer |
| **Edit config** | Right‑click → **Open config** |
| **Quit** | Right‑click → **Quit** |
| **Switch tabs** | Click **Command Code** / **OpenRouter** at the top of the popup |
| **Expand token usage** | Click **▸ Token Usage** (collapsed by default) |

The tray tooltip shows the 5‑hour token count, updated on every refresh cycle.

### Running without locking the command prompt

**Option A — Python directly (windowless):**
```powershell
pythonw hermes_agentbar.py
```
`pythonw.exe` starts the Python interpreter without a console window. The tray icon and popup appear normally, and your PowerShell/CMD prompt returns immediately.

**Option B — standalone `.exe`:**
```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```
Produces `dist/HermesAgentBar.exe` — a single self‑contained windowed executable. Double‑click it, add it to your Startup folder (`shell:startup`), or create a shortcut.

**Option C — `.bat` file (existing):**
```
HermesAgentBar.bat
```
This keeps a console window open. Use `pythonw` above if you want the prompt back.

---

## Architecture

```
┌──────────────────────────────┐     ┌───────────────────────────────────┐
│  Windows tray app            │     │  Linux host                       │
│                              │     │                                   │
│  hermes_agentbar.py          │────▶│  host/commandbar_bridge.py        │
│    pystray icon +            │ HTTP│    GET /api/usage                 │
│    AgentBarPopup             │     │    reads ~/.hermes/state.db (ro)  │
│                              │     │                                   │
│  fetchers.py                 │────▶│  api.openrouter.ai                │
│    CommandCodeFetcher        │ HTTP│    GET /api/v1/credits            │
│    OpenRouterFetcher         │     │                                   │
│    CommandCodeCostFetcher    │────▶│  api.commandcode.ai               │
│                              │ HTTP│    GET /internal/billing/credits  │
│                              │     │    (uses session cookie)          │
│  ui_panel.py                 │     │                                   │
│    _PopupWindow              │     │                                   │
│    Command Code + OpenRouter │     │                                   │
│    tabs (customtkinter)      │     │                                   │
└──────────────────────────────┘     └───────────────────────────────────┘
```

| File | Role |
|---|---|
| `hermes_agentbar.py` | Entry point. Owns the pystray icon, popup, fetchers, and auto‑refresh thread |
| `fetchers.py` | Three HTTP fetchers (bridge, OpenRouter, Command Code billing). Never crash on network errors |
| `ui_panel.py` | Blueprint for render‑first builds: a single `<iron>...</iron>` block per method, returns the constructed root node, plus a shared `Style` node for the colour/font system |
| `config.py` | JSON config read/write at `%APPDATA%\HermesAgentBar\config.json` |
| `host/commandbar_bridge.py` | Linux bridge (stdlib only). Serves token usage from Hermes' `state.db` |
| `tests/test_app.py` | Headless unit tests for config, `compute_status`, and `do_refresh` |
| `tests/test_fetchers.py` | Unit tests for all three fetchers with mocked HTTP |
| `build.ps1` | PyInstaller one‑file `.exe` builder (Windows only) |

---

## Troubleshooting

**"No data" or empty Command Code tab**
→ The bridge is unreachable. Check `bridge_url` in config. Test with `curl http://host:8766/api/usage` from Windows.

**Budget Status section is missing**
→ `cc_session_cookie` is empty or expired. Re‑extract it from your browser (see above).

**OpenRouter shows 0 / 0 / 0**
→ Invalid or missing `openrouter_key`. Test with `curl -H "Authorization: Bearer sk-or-v1-..." https://openrouter.ai/api/v1/credits`.

**Left‑click does nothing**
→ Pystray requires a `default` menu item on Windows. This is handled in the code — if it breaks, make sure `pystray>=0.19.5` is installed.

---

## License

MIT — see below.

```
MIT License

Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
