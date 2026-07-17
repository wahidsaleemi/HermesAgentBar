"""Configuration handling for Hermes AgentBar.

Pure stdlib (os, json, pathlib) — no GUI or network imports, so this module
can be imported and unit-tested in a headless environment.
"""

import json
import os
from pathlib import Path


DEFAULT_CONFIG = {
    "bridge_url": "http://127.0.0.1:8766",
    "bridge_token": "",
    "openrouter_key": "",
    "refresh_minutes": 5,
    "weekly_token_budget": 0,
    # Command Code session cookie for the internal usage API.  Set this to
    # the FULL "Cookie" request header from a logged-in browser session on
    # commandcode.ai (e.g. "session_data=...; session_token=...").
    # Optional — when unset, the forecast row is hidden.
    "cc_session_cookie": "",
    # Command Code budget caps (USD).  These are fixed per-plan and only
    # change when the user upgrades.
    "cc_5h_budget": 45,
    "cc_weekly_budget": 90,
}


def config_path() -> Path:
    """Return the platform-appropriate config file path.

    On Windows: %APPDATA%/HermesAgentBar/config.json
    Elsewhere:  ~/.config/HermesAgentBar/config.json
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata) / "HermesAgentBar"
    else:
        base = Path.home() / ".config" / "HermesAgentBar"
    return base / "config.json"


def _as_path(p) -> Path:
    """Coerce a config path (str or Path) into a Path."""
    return p if isinstance(p, Path) else Path(p)


def load_config() -> dict:
    """Return the merged configuration dict.

    Defaults are always the base. If a config file exists, its values override
    the defaults. If it does not exist, the directory and file are created with
    the default values (without overwriting an existing file).
    """
    path = _as_path(config_path())
    cfg = dict(DEFAULT_CONFIG)

    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
        except (json.JSONDecodeError, OSError) as e:
            # Corrupt/unreadable config — fall back to defaults but don't clobber.
            print(f"[HermesAgentBar] warning: could not read config at {path}: {e}")
            return cfg
    else:
        save_config(cfg)  # create with defaults; never overwrites existing
    return cfg


def save_config(cfg: dict) -> None:
    """Write the configuration to disk as pretty-printed JSON.

    Only the known keys are persisted (others are ignored to avoid drift).
    """
    path = _as_path(config_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(clean, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    print("Config path:", config_path())
    c = load_config()
    print("Loaded config:", c)
