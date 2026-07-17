"""
Hermes AgentBar — system tray app (Windows)
===========================================

Shows **Command Code** token usage (read from a local HTTP bridge to a Linux
host) and **OpenRouter** credits, in a pystray + customtkinter popup.

Design notes
------------
* All GUI imports (``pystray``, ``PIL``, ``ui_panel``) are DEFERRED into
  functions and never happen at module import time.  This lets the pure,
  non-GUI logic (``config``, ``compute_status``, ``do_refresh``) be imported
  and unit-tested **headless** — no display, no GUI packages required.
* The only top-level imports are stdlib + ``config`` + ``fetchers`` (which
  itself only needs ``requests``).

HARD RULES (inherited from the project brief)
---------------------------------------------
* NO dollar figures anywhere.  Tokens + percentages only.
* Command Code has no usage API; we show token *volume* from the bridge.
* Graceful on missing OpenRouter key (the OpenRouter tab is skipped).
"""

import logging
import os
import threading

import config
from fetchers import CommandCodeCostFetcher, CommandCodeFetcher, OpenRouterFetcher

logger = logging.getLogger("hermes_agentbar")


# ─────────────────────────────────────────────
# Pure, headless-safe computation
# ─────────────────────────────────────────────

def compute_status(cc_data, credits, cfg):
    """Pure function: derive tooltip + status fields from raw fetch results.

    ``cc_data``  — Command Code usage dict (bridge contract), or None / an
                   ``{"error": ...}`` shape on failure.
    ``credits``  — OpenRouter credits dict ({"total","used","remaining"}), or
                   None / an ``{"error": ...}`` shape on failure.
    ``cfg``      — the merged config dict.

    Returns a dict:
        last5h_tokens (int)  — last-5h input+output token sum (0 if unknown)
        cc_ok         (bool) — last-good Command Code data is available
        or_remaining  (float|None) — remaining OpenRouter credits (None if n/a)
        or_ok         (bool) — last-good OpenRouter data is available
        tooltip       (str)  — ready-to-use tray tooltip string
    """
    last5h = 0
    cc_ok = False
    if isinstance(cc_data, dict) and not cc_data.get("error"):
        windows = cc_data.get("windows") or {}
        w5 = windows.get("5h") or {}
        try:
            last5h = int(w5.get("input", 0) or 0) + int(w5.get("output", 0) or 0)
            cc_ok = True
        except (TypeError, ValueError):
            cc_ok = False

    or_remaining = None
    or_ok = False
    if isinstance(credits, dict) and not credits.get("error"):
        try:
            or_remaining = float(credits.get("remaining", 0) or 0)
            or_ok = True
        except (TypeError, ValueError):
            or_ok = False

    if cc_ok:
        tooltip = "Hermes AgentBar — 5h: {:,}".format(last5h)
    else:
        tooltip = "Hermes AgentBar — ⚠ no data"

    return {
        "last5h_tokens": last5h,
        "cc_ok": cc_ok,
        "or_remaining": or_remaining,
        "or_ok": or_ok,
        "tooltip": tooltip,
    }


# ─────────────────────────────────────────────
# The app
# ─────────────────────────────────────────────

class HermesAgentBar:
    """Owns tray icon, popup, fetchers, and the auto-refresh thread."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.last_cc = None          # last *good* Command Code usage dict
        self.last_credits = None     # last *good* OpenRouter credits dict
        self.has_any_data = False
        self.icon = None             # pystray.Icon (set in run())
        self.popup = None            # AgentBarPopup (set in run())
        self.root = None             # hidden tkinter root (set in run())
        self._stop = threading.Event()
        self._lock = threading.Lock()

        self.cc_fetcher = CommandCodeFetcher(
            cfg["bridge_url"],
            token=(cfg.get("bridge_token") or None),
        )
        key = (cfg.get("openrouter_key") or "").strip()
        self.or_fetcher = OpenRouterFetcher(key) if key else None

        # Command Code cost fetcher (optional — requires a session cookie in
        # config).  When absent, the forecast row is hidden in the popup.
        cc_sess = (cfg.get("cc_session_cookie") or "").strip()
        self.cc_cost_fetcher = CommandCodeCostFetcher(cc_sess) if cc_sess else None
        self.last_cc_cost = None  # last-good cost/forecast dict (or None)

    # ── fetch helpers ──

    @staticmethod
    def _safe_fetch(fetcher):
        try:
            return fetcher.fetch()
        except Exception as exc:  # never let a crash escape the thread
            logger.exception("fetcher %s crashed: %s", type(fetcher).__name__, exc)
            return {"error": str(exc)}

    # ── the core refresh (callable from menu / thread) ──

    def do_refresh(self, manual=False):
        """Fetch both sources, store last-good data, update tooltip + popup.

        Always re-reads the on-disk config first, so an edit to config.json
        + a Refresh (tray menu or popup button) takes effect immediately
        without restarting the app.
        """
        # Reload config so live edits (e.g. weekly_token_budget) are honoured.
        try:
            self.cfg = config.load_config()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("could not reload config: %s", exc)

        # If the OpenRouter key changed, rebuild the fetcher.
        key = (self.cfg.get("openrouter_key") or "").strip()
        if key:
            if self.or_fetcher is None:
                self.or_fetcher = OpenRouterFetcher(key)
            elif getattr(self.or_fetcher, "api_key", None) != key:
                self.or_fetcher = OpenRouterFetcher(key)
        else:
            self.or_fetcher = None

        new_cc = self._safe_fetch(self.cc_fetcher)
        with self._lock:
            if isinstance(new_cc, dict) and not new_cc.get("error"):
                self.last_cc = new_cc
                self.has_any_data = True
            # on error: keep the previous last-good payload

            if self.or_fetcher is not None:
                new_or = self._safe_fetch(self.or_fetcher)
                if isinstance(new_or, dict) and not new_or.get("error"):
                    self.last_credits = new_or

            if self.cc_cost_fetcher is not None:
                new_cost = self._safe_fetch(self.cc_cost_fetcher)
                if isinstance(new_cost, dict) and not new_cost.get("error"):
                    self.last_cc_cost = new_cost

            status = compute_status(self.last_cc, self.last_credits, self.cfg)
            if self.icon is not None:
                try:
                    self.icon.title = status["tooltip"]
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("could not set icon title: %s", exc)

        # Refresh the visible popup on the tkinter thread.
        self._to_tk(self._refresh_popup_if_visible)
        return status

    def _refresh_popup_if_visible(self):
        if self.popup is not None and self.popup.visible:
            with self._lock:
                cc, cr, ccost = self.last_cc, self.last_credits, self.last_cc_cost
            self.popup.show(cc, cr, self.cfg, cc_cost=ccost)

    # ── background loop ──

    def _refresh_loop(self):
        interval = max(1, int(self.cfg.get("refresh_minutes", 5) or 5)) * 60
        while not self._stop.is_set():
            if self._stop.wait(interval):
                break
            try:
                self.do_refresh()
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("refresh loop error: %s", exc)

    # ── tkinter marshalling ──

    def _to_tk(self, fn, *args, **kwargs):
        """Run ``fn`` on the tkinter main thread if a root exists."""
        if self.root is not None:
            self.root.after(0, lambda: fn(*args, **kwargs))

    # ── menu / activation callbacks ──

    def _on_toggle(self, icon=None, item=None):
        self._to_tk(self._toggle_popup)

    def _toggle_popup(self):
        if self.popup is not None:
            with self._lock:
                cc, cr, ccost = self.last_cc, self.last_credits, self.last_cc_cost
            self.popup.toggle(cc, cr, self.cfg, cc_cost=ccost)

    def _on_show(self, icon=None, item=None):
        # Left-click (default item) toggles the popup: open if hidden,
        # hide if already visible. Right-click "Show" behaves the same.
        self._to_tk(self._toggle_popup)

    def _on_refresh(self, icon=None, item=None):
        # Runs on the pystray thread; do_refresh is thread-safe.
        self.do_refresh(manual=True)

    def manual_refresh(self):
        """Public refresh entry (also wired to the popup's refresh button)."""
        return self.do_refresh(manual=True)

    def _on_open_config(self, icon=None, item=None):
        path = config.config_path()
        try:
            if os.name == "nt":
                os.startfile(str(path))  # noqa: F821 (Windows only)
            else:
                import webbrowser
                webbrowser.open(str(path))
        except Exception as exc:
            logger.warning("could not open config %s: %s", path, exc)

    def _on_quit(self, icon=None, item=None):
        self._stop.set()
        self._to_tk(self.root.quit) if self.root is not None else None
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception:  # pragma: no cover - defensive
            pass

    # ── tray icon image (deferred import) ──

    @staticmethod
    def _build_icon_image():
        from PIL import Image, ImageDraw

        here = os.path.dirname(os.path.abspath(__file__))
        ico = os.path.join(here, "assets", "hermes_agentbar.ico")
        if os.path.exists(ico):
            return Image.open(ico)

        # Fallback: a teal (#2DD4BF) rounded square with a dark centre dot.
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(45, 212, 191, 255))
        d.ellipse([25, 25, 39, 39], fill=(14, 21, 37, 255))
        return img

    # ── entry point ──

    def run(self):
        # All GUI imports are deferred to here.
        import tkinter as tk
        import pystray
        from pystray import MenuItem as Item, Menu
        from ui_panel import AgentBarPopup

        # Hidden tkinter root (owns the popup window).
        self.root = tk.Tk()
        self.root.withdraw()
        self.popup = AgentBarPopup(
            self.root,
            on_refresh=self.manual_refresh,
            on_close=None,
        )
        image = self._build_icon_image()
        # Left-click on the tray icon activates the menu item marked `default`
        # (pystray/Windows: WM_LBUTTONUP -> Menu.__call__ -> default item).
        # `default` is a MenuItem kwarg, NOT a Menu kwarg -- that was the bug.
        menu = Menu(
            Item("Show", self._on_show, default=True),
            Item("Refresh now", self._on_refresh),
            Item("Open config", self._on_open_config),
            Item("Quit", self._on_quit),
        )
        self.icon = pystray.Icon(
            "HermesAgentBar",
            image,
            "Hermes AgentBar — starting…",
            menu,
        )

        # Initial fetch so the tooltip is populated before the first refresh.
        self.do_refresh()

        # Background auto-refresh thread (daemon).
        self._thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._thread.start()

        # Run pystray in a worker thread; keep tkinter mainloop on the main
        # thread (tkinter is not thread-safe and must run where root was made).
        tray_thread = threading.Thread(target=self.icon.run, daemon=True)
        tray_thread.start()
        try:
            self.root.mainloop()
        finally:
            self._stop.set()
            try:
                self.icon.stop()
            except Exception:  # pragma: no cover - defensive
                pass


# ─────────────────────────────────────────────
# Module entry point
# ─────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = config.load_config()
    HermesAgentBar(cfg).run()


if __name__ == "__main__":
    main()
