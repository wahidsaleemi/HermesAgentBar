"""
fetchers.py — Hermes AgentBar
=============================

Network fetchers for the Hermes AgentBar tray app.

Two synchronous fetchers (called from a worker thread by the tray UI):

* CommandCodeFetcher  — polls the local bridge (host-side
  commandbar_bridge.py) for Command Code token usage.
* OpenRouterFetcher   — queries the OpenRouter credits endpoint.
* CommandCodeCostFetcher — queries Command Code's internal billing API for
  real-time budget window stats (5h / weekly USD caps with reset timestamps).

All are deliberately defensive: on any network/HTTP failure they return a
well-shaped fallback dict and log the error. They NEVER raise out of fetch(),
so a transient network hiccup can never crash the tray thread.

No live network calls are made at import time. The only third-party
dependency is `requests`; everything else is stdlib.
"""

import json
import logging
import time

import requests

logger = logging.getLogger("hermes_agentbar.fetchers")

# Fallback shape returned by CommandCodeFetcher.fetch() on any failure.
_ERROR_USAGE = {
    "error": "",
    "windows": {"5h": {}, "7d": {}},
    "by_model": [],
    "daily_7d": [],
}


class CommandCodeFetcher:
    """Fetch Command Code token usage from the local bridge HTTP endpoint."""

    def __init__(self, base_url: str, token: str | None = None, timeout: float = 10.0):
        # Normalise: strip a trailing slash so we never double-slash /api/usage.
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def fetch(self) -> dict:
        """GET {base_url}/api/usage and return the parsed usage dict.

        On a non-200 response or any network error, returns the error-shape
        fallback (see _ERROR_USAGE) and logs the problem. Never raises.
        """
        url = f"{self.base_url}/api/usage"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning("CommandCodeFetcher: request to %s failed: %s", url, exc)
            return {**_ERROR_USAGE, "error": str(exc)}
        except Exception as exc:  # pragma: no cover - defensive catch-all
            logger.exception("CommandCodeFetcher: unexpected error for %s", url)
            return {**_ERROR_USAGE, "error": str(exc)}

        if resp.status_code != 200:
            msg = f"HTTP {resp.status_code}"
            logger.warning("CommandCodeFetcher: %s from %s", msg, url)
            return {**_ERROR_USAGE, "error": msg}

        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("CommandCodeFetcher: invalid JSON from %s: %s", url, exc)
            return {**_ERROR_USAGE, "error": f"invalid JSON: {exc}"}

        # Ensure the documented top-level keys are present even if the bridge
        # returned a partial payload, so downstream code can't KeyError.
        data.setdefault("windows", {"5h": {}, "7d": {}})
        data["windows"].setdefault("5h", {})
        data["windows"].setdefault("7d", {})
        data.setdefault("by_model", [])
        data.setdefault("daily_7d", [])
        return data

    def budget_pct(self, data: dict, weekly_token_budget: int) -> float:
        """Percentage of the weekly TOKEN budget consumed (7d window).

        Computes (7d input + output + cache_read) / weekly_token_budget * 100,
        clamped to a minimum of 0. Can exceed 100 when the budget is overrun.
        Missing keys are treated as zero so partial payloads degrade safely.

        Returns a float (e.g. 42.5 means 42.5%).
        """
        windows = (data or {}).get("windows", {}) or {}
        week = windows.get("7d", {}) or {}
        used = (
            week.get("input", 0)
            + week.get("output", 0)
            + week.get("cache_read", 0)
        )
        if weekly_token_budget <= 0:
            return 0.0
        pct = (used / weekly_token_budget) * 100.0
        return max(0.0, pct)


class OpenRouterFetcher:
    """Fetch OpenRouter credits (total / used / remaining) for a given key."""

    CREDITS_URL = "https://openrouter.ai/api/v1/credits"

    def __init__(self, api_key: str, timeout: float = 10.0):
        self.api_key = api_key
        self.timeout = timeout

    def fetch(self) -> dict:
        """GET the OpenRouter credits endpoint and return a credits dict.

        On success returns:
            {"total": float, "used": float, "remaining": float}
        where remaining = total_credits - total_usage.

        On any failure returns:
            {"error": str, "total": 0, "used": 0, "remaining": 0}
        Never raises.
        """
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            resp = requests.get(self.CREDITS_URL, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning("OpenRouterFetcher: request failed: %s", exc)
            return {"error": str(exc), "total": 0, "used": 0, "remaining": 0}
        except Exception as exc:  # pragma: no cover - defensive catch-all
            logger.exception("OpenRouterFetcher: unexpected error")
            return {"error": str(exc), "total": 0, "used": 0, "remaining": 0}

        if resp.status_code != 200:
            msg = f"HTTP {resp.status_code}"
            logger.warning("OpenRouterFetcher: %s", msg)
            return {"error": msg, "total": 0, "used": 0, "remaining": 0}

        try:
            payload = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("OpenRouterFetcher: invalid JSON: %s", exc)
            return {"error": f"invalid JSON: {exc}", "total": 0, "used": 0, "remaining": 0}

        data = payload.get("data", {}) or {}
        total = float(data.get("total_credits", 0))
        used = float(data.get("total_usage", 0))
        remaining = total - used
        return {"total": total, "used": used, "remaining": remaining}


class CommandCodeCostFetcher:
    """Fetch Command Code budget window stats from the billing endpoint.

    Uses ``/internal/billing/credits`` — the same endpoint the dashboard
    calls — which returns pre-computed ``fiveHour`` and ``weekly`` window
    totals with ``used``, ``cap``, and ``resetAt`` (ms epoch) fields.

    This is simpler AND more accurate than summing individual usage items
    because the server already knows the exact rolling windows.

    The session cookie is the full ``-b`` value from a logged-in browser
    session (must include both ``__Secure-commandcode_prod_.session_token``
    and ``__Secure-commandcode_prod_.session_data``).
    """

    BILLING_URL = "https://api.commandcode.ai/internal/billing/credits"

    def __init__(self, cookie_header: str, timeout: float = 12.0):
        self.cookie_header = cookie_header
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Accept": "*/*",
            "Cookie": self.cookie_header,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
            "Origin": "https://commandcode.ai",
            "Referer": "https://commandcode.ai/",
        }

    def _get_json(self, url: str) -> dict | None:
        try:
            resp = requests.get(
                url, headers=self._headers(), timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.warning("CommandCodeCostFetcher: request failed: %s", exc)
            return None
        if resp.status_code != 200:
            logger.warning("CommandCodeCostFetcher: HTTP %s", resp.status_code)
            return None
        try:
            return resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("CommandCodeCostFetcher: invalid JSON: %s", exc)
            return None

    def fetch(self) -> dict:
        """Return pre-computed window stats and real reset timestamps.

        Returns::

            {
                "cost_5h": float,       # USD used in the rolling 5h window
                "cost_7d": float,       # USD used in the rolling 7d window
                "budget_5h": float,     # hard cap (from API; default 45)
                "budget_7d": float,     # hard cap (from API; default 90)
                "reset_5h": float | None,     # unix timestamp (s) when 5h resets
                "reset_7d": float | None,     # unix timestamp (s) when week resets
                "pct_5h": float,        # 0–100 %
                "pct_7d": float,        # 0–100 %
                "error": "",            # non-empty on failure
            }
        """
        empty = {
            "cost_5h": 0.0, "cost_7d": 0.0,
            "budget_5h": 45, "budget_7d": 90,
            "reset_5h": None, "reset_7d": None,
            "pct_5h": 0.0, "pct_7d": 0.0,
            "error": "",
        }
        payload = self._get_json(self.BILLING_URL)
        if payload is None:
            return {**empty, "error": "no data from billing API"}

        limits = payload.get("windowLimits") or {}
        fh = limits.get("fiveHour") or {}
        wk = limits.get("weekly") or {}

        cost_5h = float(fh.get("used", 0))
        cost_7d = float(wk.get("used", 0))
        cap_5h = float(fh.get("cap", 45))
        cap_7d = float(wk.get("cap", 90))

        # resetAt is ms epoch; convert to seconds float.
        reset_5h_ms = fh.get("resetAt")
        reset_7d_ms = wk.get("resetAt")

        return {
            "cost_5h": cost_5h,
            "cost_7d": cost_7d,
            "budget_5h": cap_5h,
            "budget_7d": cap_7d,
            "reset_5h": (reset_5h_ms / 1000.0) if reset_5h_ms else None,
            "reset_7d": (reset_7d_ms / 1000.0) if reset_7d_ms else None,
            "pct_5h": min(cost_5h / cap_5h * 100, 100) if cap_5h else 0,
            "pct_7d": min(cost_7d / cap_7d * 100, 100) if cap_7d else 0,
            "error": "",
        }
