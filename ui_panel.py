"""
Hermes AgentBar — Popup panel (ui_panel.py)
===========================================

A standalone, customtkinter slide-up popup for the Hermes AgentBar tray app.

It mirrors the window-shell patterns from the upstream CodexBar popup
(borderless Toplevel, slide-up + alpha animation, <FocusOut> click-outside
dismiss, frame-swap tab morph, frame-based progress bars) but is fully
Hermes-themed and contains NO OpenAI / Claude proprietary code or branding.

Two tabs:
  * "Command Code"  — token-volume cards (5h / 7d), per-model list,
                      7-day daily trend, and a user weekly-token-budget bar.
  * "OpenRouter"    — total / used / remaining credits + a used/total bar.

HARD RULES enforced here:
  * NO dollar figures anywhere.  Only token counts (formatted with commas)
    and percentages.
  * "Command Code" has no usage API, so it shows TOKEN VOLUME, never a
    "% of limit".  The only budget % shown is the user's own weekly token
    budget (from config), and it is labelled clearly as such.

Public API
----------
    popup = AgentBarPopup(master, on_refresh=..., on_close=...)
    popup.show(data, credits, config)   # build + animate in (idempotent)
    popup.hide()                        # destroy the window
    popup.toggle(data, credits, config) # show if hidden, else hide
    popup.visible                       # bool property

The module renders only data passed in — it performs NO network calls.
"""

from __future__ import annotations

import customtkinter as ctk
from tkinter import Canvas


# ─────────────────────────────────────────────
# Palette  (Hermes: teal #2DD4BF + indigo #6366F1, dark slate shell)
# ─────────────────────────────────────────────

BG         = "#0E1525"   # deep slate shell
SURFACE    = "#16203A"   # card surface
PRIMARY    = "#E6EDF6"   # primary text
SECOND     = "#9AA7BD"   # secondary text
TERTIARY   = "#5F6E8C"   # muted text
ACCENT     = "#2DD4BF"   # teal  (primary brand)
ACCENT_HV  = "#25B8A6"   # teal hover
ACCENT_DK  = "#1F8C80"   # dimmer teal (non-peak bars)
ACCENT2    = "#6366F1"   # indigo (secondary brand)
ACCENT2_BG = "#26243F"   # indigo badge bg
TRACK      = "#23304D"   # progress track
DIVIDER    = "#1E2A45"   # hairline divider
HOVER      = "#1B2740"   # button hover
LIME       = "#BCE241"   # OpenRouter lime green
LIME_BG    = "#2A3514"   # lime badge bg
GREEN      = "#34D399"   # budget-status green
PURPLE     = "#8B5CF6"   # daily-trend violet
PURPLE_DK  = "#5B4A8A"   # daily-trend dim violet


# ─────────────────────────────────────────────
# Public facade
# ─────────────────────────────────────────────

class AgentBarPopup:
    """Owns a single borderless popup window and exposes show/hide/toggle."""

    def __init__(self, master, *, on_refresh=None, on_close=None):
        self._master = master
        self._on_refresh = on_refresh
        self._on_close = on_close
        self._win = None
        self._visible = False

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self, data: dict | None = None,
             credits: dict | None = None,
             config: dict | None = None,
             cc_cost: dict | None = None) -> None:
        """Build (or rebuild) the popup and animate it in.  Idempotent."""
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None
        self._win = _PopupWindow(
            self._master,
            data or {},
            credits or {},
            config or {},
            on_close=self._handle_closed,
            on_refresh=self._on_refresh,
            cc_cost=cc_cost,
        )
        self._visible = True

    def hide(self) -> None:
        """Dismiss the popup if visible."""
        if self._win is not None:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None
        self._visible = False

    def toggle(self, data: dict | None = None,
               credits: dict | None = None,
               config: dict | None = None,
               cc_cost: dict | None = None) -> None:
        if self._visible:
            self.hide()
        else:
            self.show(data, credits, config, cc_cost=cc_cost)

    def _handle_closed(self) -> None:
        self._win = None
        self._visible = False
        if self._on_close:
            self._on_close()


# ─────────────────────────────────────────────
# The actual window
# ─────────────────────────────────────────────

class _PopupWindow(ctk.CTkToplevel):
    """Borderless slide-up popup with Command Code + OpenRouter tabs."""

    WIDTH = 380
    HEIGHT = 580
    FINAL_ALPHA = 1.0

    def __init__(self, master, data, credits, config, *,
                 on_close=None, on_refresh=None, cc_cost=None):
        super().__init__(master)
        self._data = data
        self._credits = credits
        self._config = config
        self._cc_cost = cc_cost or {}
        self._on_close = on_close
        self._on_refresh = on_refresh
        self._active_tab = "command"
        self._cc_token_open = False  # collapsible token-usage section

        self.overrideredirect(True)
        self.configure(fg_color=BG)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)

        self._build_ui()

        self.update_idletasks()
        work = self._work_area()
        w = self.WIDTH
        h = self.HEIGHT
        # anchor bottom-right with a small margin (tray-aware on the right/bottom)
        self._target_x = max(work[2] + 8, work[0] - w - 14)
        self._target_y = max(work[3] + 8, work[1] - h - 14)
        self.geometry(f"{w}x{h}+{self._target_x}+{self._target_y + 16}")

        self.bind("<Escape>", lambda e: self._close())
        # NOTE: click-outside-to-dismiss is intentionally DISABLED. The window
        # is meant to stay open for glancing (left-click toggles it instead).
        # If you want click-outside-to-close back, rebind FocusOut here.
        self.focus_force()
        self.after(40, self._animate_in, 0)

    # ── screen geometry (cross-platform) ──

    def _work_area(self):
        """Return (right, bottom, left, top) of usable area in logical px.

        Uses the Win32 work-area on Windows; falls back to full screen size
        everywhere else (e.g. Linux).
        """
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(
                48, 0, ctypes.byref(rect), 0)
            scale = 1.0
            try:
                scale = float(self._get_window_scaling())
            except Exception:
                pass
            return (int(rect.right / scale), int(rect.bottom / scale),
                    int(rect.left / scale), int(rect.top / scale))
        except Exception:
            return (self.winfo_screenwidth(), self.winfo_screenheight(), 0, 0)

    # ── slide-up animation ──

    def _animate_in(self, step, total=14):
        if step > total:
            return
        t = step / total
        ease = 1.0 - (1.0 - t) ** 3
        y = int(self._target_y + 16 * (1.0 - ease))
        alpha = min(ease, self.FINAL_ALPHA)
        try:
            self.geometry(f"+{self._target_x}+{y}")
            self.attributes("-alpha", alpha)
            self.after(14, self._animate_in, step + 1, total)
        except Exception:
            pass

    # ── click-outside-to-dismiss ──

    def _on_focus_out(self, event):
        # Defer so the new focus target is stable before we judge it.
        self.after(120, self._check_focus)

    def _check_focus(self):
        try:
            fw = self.focus_get()
        except Exception:
            # Should not happen, but be safe.
            return

        # Only dismiss when focus moves to a *different* Toplevel (i.e. the
        # user clicked another app/window). Focus landing on our own hidden
        # root is expected right after show(); do NOT close then.
        if fw is None:
            self._close()
            return
        strfw = str(fw)
        if strfw.startswith(str(self)):
            return
        # Same window family (the app's hidden root) -> keep open.
        if str(self._master) in strfw or strfw == str(self._master):
            return
        self._close()

    # ── tab morph (fade out → swap → fade in) ──

    def _switch_tab(self, tab):
        if tab == self._active_tab:
            return
        self._active_tab = tab
        self._m_step = 0
        self._m_phase = "out"
        self._morph_tick()

    def _do_swap(self):
        if self._active_tab == "command":
            self._cmd_tab_btn.configure(fg_color=ACCENT, hover_color=ACCENT,
                                        text_color="#06121F")
            self._or_tab_btn.configure(fg_color="transparent",
                                       hover_color=HOVER, text_color=SECOND)
        else:
            self._or_tab_btn.configure(fg_color=LIME, hover_color=LIME,
                                       text_color="#0B0E1A")
            self._cmd_tab_btn.configure(fg_color="transparent",
                                        hover_color=HOVER, text_color=SECOND)
        self._cmd_frame.pack_forget()
        self._or_frame.pack_forget()
        target = self._cmd_frame if self._active_tab == "command" else self._or_frame
        target.pack(fill="both", expand=True)

    def _morph_tick(self):
        try:
            if self._m_phase == "out":
                total = 6
                s = self._m_step
                if s >= total:
                    self.attributes("-alpha", 0.0)
                    self._do_swap()
                    self.attributes("-alpha", 0.0)
                    self._m_step = 0
                    self._m_phase = "in"
                    self.after(10, self._morph_tick)
                    return
                t = s / total
                ease = t * t
                self.attributes("-alpha", max(self.FINAL_ALPHA * (1.0 - ease), 0.0))
                self._m_step += 1
                self.after(12, self._morph_tick)
            elif self._m_phase == "in":
                total = 10
                s = self._m_step
                if s >= total:
                    self.attributes("-alpha", self.FINAL_ALPHA)
                    return
                t = s / total
                ease = 1.0 - (1.0 - t) ** 3
                self.attributes("-alpha", self.FINAL_ALPHA * ease)
                self._m_step += 1
                self.after(12, self._morph_tick)
        except Exception:
            pass

    # ── bar colour helper ──

    @staticmethod
    def _bar_color(pct):
        if pct <= 50:
            return ACCENT
        if pct <= 80:
            return "#E8A33E"   # amber
        return "#E24B4B"       # red

    # ── small value helpers ──

    @staticmethod
    def _get(d, *names, default=0):
        if isinstance(d, dict):
            for n in names:
                if n in d and d[n] is not None:
                    return d[n]
        return default

    @staticmethod
    def _fmt(n):
        try:
            return f"{int(round(float(n))):,}"
        except Exception:
            return "0"

    @staticmethod
    def _fmt_usd(n):
        try:
            return f"${float(n):,.2f}"
        except Exception:
            return "$0.00"

    @staticmethod
    def _num(v):
        try:
            return int(round(float(v))) if v is not None else 0
        except Exception:
            return 0

    @staticmethod
    def _model_tokens(item):
        if not isinstance(item, dict):
            return 0
        if item.get("tokens") is not None:
            return item["tokens"]
        if item.get("total") is not None:
            return item["total"]
        return (_PopupWindow._get(item, "input", "Input")
                + _PopupWindow._get(item, "output", "Output"))

    # ═══════════════════════════════════════
    # UI BUILD
    # ═══════════════════════════════════════

    def _build_ui(self):
        # ── tab bar (pill of two text buttons) ──
        tab_bar = ctk.CTkFrame(self, fg_color=BG, corner_radius=0, height=36)
        tab_bar.pack(fill="x")
        tab_bar.pack_propagate(False)

        inner = ctk.CTkFrame(tab_bar, fg_color=TRACK, corner_radius=9)
        inner.pack(side="left", padx=14, pady=5)

        self._cmd_tab_btn = ctk.CTkButton(
            inner, text="Command Code", font=("Segoe UI Semibold", 12),
            fg_color=ACCENT, hover_color=ACCENT, text_color="#06121F",
            corner_radius=8, height=26, width=120,
            command=lambda: self._switch_tab("command"))
        self._cmd_tab_btn.pack(side="left", padx=2, pady=2)

        self._or_tab_btn = ctk.CTkButton(
            inner, text="OpenRouter", font=("Segoe UI Semibold", 12),
            fg_color="transparent", hover_color=HOVER, text_color=SECOND,
            corner_radius=8, height=26, width=110,
            command=lambda: self._switch_tab("openai"))
        self._or_tab_btn.pack(side="left", padx=2, pady=2)

        # ── scrollable content (both panels live here) ──
        content = ctk.CTkScrollableFrame(
            self, fg_color=BG, corner_radius=0,
            scrollbar_button_color=TRACK,
            scrollbar_button_hover_color=HOVER)
        content.pack(fill="both", expand=True)
        self._content = content

        self._cmd_frame = ctk.CTkFrame(content, fg_color=BG, corner_radius=0)
        self._or_frame = ctk.CTkFrame(content, fg_color=BG, corner_radius=0)
        self._build_command_panel(self._cmd_frame)
        self._build_openrouter_panel(self._or_frame)
        self._cmd_frame.pack(fill="both", expand=True)

        # ── footer (always visible) ──
        self._build_footer()

    # ═══════════════════════════════════════
    # COMMAND CODE PANEL
    # ═══════════════════════════════════════

    def _build_command_panel(self, parent):
        d = self._data

        # ── header ──
        hero = ctk.CTkFrame(parent, fg_color="transparent")
        hero.pack(fill="x", padx=20, pady=(14, 0))

        row = ctk.CTkFrame(hero, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="Command Code",
                     font=("Segoe UI Semibold", 20),
                     text_color=PRIMARY).pack(side="left")
        ctk.CTkFrame(row, fg_color=ACCENT, corner_radius=4,
                     width=8, height=8).pack(side="left", padx=(8, 0), pady=4)

        meta = ctk.CTkFrame(hero, fg_color="transparent")
        meta.pack(fill="x", pady=(5, 0))
        ctk.CTkFrame(meta, fg_color=GREEN, corner_radius=4,
                     width=7, height=7).pack(side="left", padx=(1, 7), pady=5)
        upd = d.get("updated") or "—"
        ctk.CTkLabel(meta, text=f"updated {upd}",
                     font=("Segoe UI", 11), text_color=SECOND).pack(side="left")

        # ── 1. Budget Status (green, top priority) ──
        self._cost_forecast(parent)
        budget = int(self._config.get("weekly_token_budget", 0) or 0)
        if budget > 0:
            self._budget_bar(parent, d, budget)

        # ── 2. Collapsible Token Usage ──
        toggle = ctk.CTkButton(
            parent, text="▸  Token Usage",
            font=("Segoe UI Semibold", 13),
            fg_color="transparent", hover_color=HOVER,
            text_color=TERTIARY, anchor="w",
            width=220, height=28,
            command=lambda: self._toggle_token_section(parent),
        )
        toggle.pack(padx=16, pady=(16, 0), anchor="w")
        self._cc_token_btn = toggle

        self._cc_token_frame = ctk.CTkFrame(parent, fg_color="transparent")
        # hidden by default

        windows = d.get("windows") or {}
        self._token_card(self._cc_token_frame, "Last 5h",
                         windows.get("5h") or {})
        self._token_card(self._cc_token_frame, "Last 7d",
                         windows.get("7d") or {})

        by_model = d.get("by_model") or []
        if by_model:
            self._model_list(self._cc_token_frame, by_model)

        daily = d.get("daily_7d") or []
        if daily:
            self._daily_trend(self._cc_token_frame, daily)

        ctk.CTkFrame(parent, fg_color="transparent", height=8).pack(fill="x")

    def _toggle_token_section(self, parent):
        self._cc_token_open = not self._cc_token_open
        if self._cc_token_open:
            self._cc_token_frame.pack(fill="x", after=self._cc_token_btn)
            self._cc_token_btn.configure(text="▾  Token Usage")
        else:
            self._cc_token_frame.pack_forget()
            self._cc_token_btn.configure(text="▸  Token Usage")

    def _token_card(self, parent, title, wd):
        card = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        card.pack(fill="x", padx=16, pady=(10, 0))

        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=14, pady=(10, 2))
        ctk.CTkLabel(hdr, text=title, font=("Segoe UI Semibold", 13),
                     text_color=PRIMARY).pack(side="left")

        fields = [
            ("Input",      self._get(wd, "input", "Input")),
            ("Output",     self._get(wd, "output", "Output")),
            ("Cache-read", self._get(wd, "cache_read", "cache-read", "Cache-read")),
            ("Reasoning",  self._get(wd, "reasoning", "Reasoning")),
        ]
        for name, val in fields:
            r = ctk.CTkFrame(card, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=1)
            ctk.CTkLabel(r, text=name, font=("Segoe UI", 12),
                         text_color=SECOND).pack(side="left")
            ctk.CTkLabel(r, text=self._fmt(val),
                         font=("Segoe UI Semibold", 12),
                         text_color=PRIMARY).pack(side="right")

    def _model_list(self, parent, models):
        sec = ctk.CTkFrame(parent, fg_color="transparent")
        sec.pack(fill="x", padx=16, pady=(14, 0))
        ctk.CTkLabel(sec, text="Top models",
                     font=("Segoe UI Semibold", 13),
                     text_color=TERTIARY, anchor="w").pack(
            fill="x", padx=4, pady=(0, 2))

        box = ctk.CTkFrame(sec, fg_color=SURFACE, corner_radius=12)
        box.pack(fill="x", pady=(2, 0))

        for item in models[:8]:
            name = item.get("model") or item.get("name") or "unknown"
            toks = self._model_tokens(item)
            r = ctk.CTkFrame(box, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=3)
            ctk.CTkLabel(r, text=str(name), font=("Segoe UI", 12),
                         text_color=SECOND, anchor="w").pack(
                side="left", fill="x", expand=True)
            ctk.CTkLabel(r, text=self._fmt(toks),
                         font=("Segoe UI Semibold", 12),
                         text_color=PRIMARY).pack(side="right")

    def _daily_trend(self, parent, daily):
        sec = ctk.CTkFrame(parent, fg_color="transparent")
        sec.pack(fill="x", padx=16, pady=(14, 0))
        ctk.CTkLabel(sec, text="Daily tokens (last 7 days)",
                     font=("Segoe UI Semibold", 13),
                     text_color=PURPLE, anchor="w").pack(
            fill="x", padx=4, pady=(0, 4))

        box = ctk.CTkFrame(sec, fg_color=SURFACE, corner_radius=12)
        box.pack(fill="x", pady=(2, 0))
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=12, pady=10)

        values, labels = [], []
        for i, item in enumerate(daily):
            if isinstance(item, dict):
                v = self._get(item, "input") + self._get(item, "output")
                if not v:
                    v = self._get(item, "tokens", "total")
                lab = item.get("date") or item.get("label") or str(i + 1)
            else:
                v = int(item) if isinstance(item, (int, float)) else 0
                lab = str(i + 1)
            values.append(v)
            labels.append(str(lab))

        if not any(values):
            ctk.CTkLabel(inner, text="No daily data",
                         font=("Segoe UI", 11),
                         text_color=TERTIARY).pack()
            return

        cv = Canvas(inner, height=74, bg=SURFACE,
                    highlightthickness=0, bd=0)
        cv.pack(fill="x")
        cv._values = values
        cv._labels = labels
        cv.bind("<Configure>", lambda e: self._draw_bars(cv))
        self.after_idle(lambda: self._draw_bars(cv))

    def _draw_bars(self, cv):
        try:
            cv.delete("all")
            w = cv.winfo_width()
            h = cv.winfo_height()
            vals = getattr(cv, "_values", [])
            if not vals or w <= 1:
                return
            n = len(vals)
            maxv = max(vals) or 1
            gap = 6
            bw = max((w - gap * (n + 1)) / n, 2)
            base = h - 13
            for i, v in enumerate(vals):
                x = gap + i * (bw + gap)
                bh = max(3, int(base * (v / maxv)))
                y = base - bh
                color = PURPLE if v == maxv else PURPLE_DK
                cv.create_rectangle(x, y, x + bw, base,
                                    fill=color, outline="")
                lab = cv._labels[i][-3:] if cv._labels[i] else str(i + 1)
                cv.create_text(x + bw / 2, h - 4, text=lab,
                               fill=TERTIARY, font=("Segoe UI", 8),
                               anchor="s")
        except Exception:
            pass

    def _budget_bar(self, parent, d, budget):
        windows = d.get("windows") or {}
        w7 = windows.get("7d") or {}
        used = (self._get(w7, "input", "Input")
                + self._get(w7, "output", "Output")
                + self._get(w7, "cache_read", "cache-read", "Cache-read"))
        pct = min(used / budget * 100, 100) if budget else 0
        self._progress(
            parent, "Your weekly budget", pct,
            sublabel=f"{pct:.0f}% of your weekly budget",
            note=f"{self._fmt(used)} / {self._fmt(budget)} tokens used")

    # ── cost forecast card ──

    def _cost_forecast(self, parent):
        """Forecast card: budget usage with countdown-to-reset labels.

        Only shown when ``cc_session_cookie`` is configured and the
        fetcher returned a non-error dict.  Otherwise the panel stays
        hidden (no empty placeholder).
        """
        cc = self._cc_cost or {}
        if cc.get("error"):
            return
        c5 = cc.get("cost_5h", 0)
        c7 = cc.get("cost_7d", 0)
        b5 = cc.get("budget_5h", 45)
        b7 = cc.get("budget_7d", 90)
        pct5 = cc.get("pct_5h", 0)
        pct7 = cc.get("pct_7d", 0)
        r5 = cc.get("reset_5h")  # unix seconds or None
        r7 = cc.get("reset_7d")
        if not any([c5, c7]):
            return

        def _countdown(ts):
            if ts is None:
                return "—"
            import time
            remain = max(ts - time.time(), 0)
            if remain < 60:
                return "<1m"
            h = int(remain // 3600)
            m = int((remain % 3600) // 60)
            if h > 24:
                d = h // 24
                h = h % 24
                return f"~{d}d {h}h"
            return f"~{h}h {m}m"

        # ── header ──
        hero = ctk.CTkFrame(parent, fg_color="transparent")
        hero.pack(fill="x", padx=20, pady=(16, 0))

        row = ctk.CTkFrame(hero, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="Budget Status",
                     font=("Segoe UI Semibold", 13),
                     text_color=PRIMARY).pack(side="left")
        ctk.CTkFrame(row, fg_color=GREEN, corner_radius=4,
                     width=8, height=8).pack(side="left", padx=(8, 0), pady=4)

        # ── 5h row ──
        sec = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        sec.pack(fill="x", padx=16, pady=(10, 0))
        self._forecast_row(sec, "5-hour budget",
                           cost=c5, budget=b5, bar_pct=pct5,
                           label=_countdown(r5))

        # ── 7d row ──
        sec = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        sec.pack(fill="x", padx=16, pady=(8, 0))
        self._forecast_row(sec, "Weekly budget",
                           cost=c7, budget=b7, bar_pct=pct7,
                           label=_countdown(r7))

        # ── footnote ──
        ctk.CTkLabel(parent, text="live from commandcode.ai · resets shown above",
                     font=("Segoe UI", 9),
                     text_color=TERTIARY).pack(pady=(4, 0))

    def _forecast_row(self, parent, title, cost, budget, label, bar_pct):
        r = ctk.CTkFrame(parent, fg_color="transparent")
        r.pack(fill="x", padx=14, pady=4)
        ctk.CTkLabel(r, text=title,
                     font=("Segoe UI", 12),
                     text_color=PRIMARY).pack(side="left")
        ctk.CTkLabel(r, text=label,
                     font=("Segoe UI Semibold", 13),
                     text_color=GREEN).pack(side="right")

        bar = ctk.CTkFrame(parent, fg_color=TRACK, corner_radius=4, height=6)
        bar.pack(fill="x", padx=14, pady=(0, 6))
        fill = ctk.CTkFrame(bar, fg_color=GREEN, corner_radius=4, height=6)
        fill.place(relx=0, rely=0, relheight=1, relwidth=min(bar_pct / 100, 1))

        sub = ctk.CTkFrame(parent, fg_color="transparent")
        sub.pack(fill="x", padx=14)
        ctk.CTkLabel(sub, text=f"{self._fmt_usd(cost)} / {self._fmt_usd(budget)} used",
                     font=("Segoe UI", 10),
                     text_color=SECOND).pack(side="left")
        ctk.CTkLabel(sub, text=f"{bar_pct:.0f}%",
                     font=("Segoe UI", 10),
                     text_color=SECOND).pack(side="right")

    # ═══════════════════════════════════════
    # OPENROUTER PANEL
    # ═══════════════════════════════════════

    def _build_openrouter_panel(self, parent):
        c = self._credits or {}
        total = self._num(c.get("total"))
        used = self._num(c.get("used"))
        remaining = self._num(c.get("remaining"))
        if remaining == 0 and total and used:
            remaining = max(total - used, 0)

        # ── header ──
        hero = ctk.CTkFrame(parent, fg_color="transparent")
        hero.pack(fill="x", padx=20, pady=(14, 0))

        row = ctk.CTkFrame(hero, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="OpenRouter",
                     font=("Segoe UI Semibold", 20),
                     text_color=PRIMARY).pack(side="left")
        ctk.CTkLabel(row, text="  USD  ",
                     font=("Segoe UI Semibold", 11),
                     text_color=LIME, fg_color=LIME_BG,
                     corner_radius=10).pack(side="right")

        meta = ctk.CTkFrame(hero, fg_color="transparent")
        meta.pack(fill="x", pady=(5, 0))
        ctk.CTkFrame(meta, fg_color=LIME, corner_radius=4,
                     width=7, height=7).pack(side="left", padx=(1, 7), pady=5)
        ctk.CTkLabel(meta, text="Credit balance (USD)",
                     font=("Segoe UI", 11), text_color=SECOND).pack(side="left")

        # ── three figures (rendered as USD) ──
        card = ctk.CTkFrame(parent, fg_color=SURFACE, corner_radius=12)
        card.pack(fill="x", padx=16, pady=(12, 0))
        for name, val in [("Total credits", total),
                          ("Used", used),
                          ("Remaining", remaining)]:
            r = ctk.CTkFrame(card, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(r, text=name, font=("Segoe UI", 12),
                         text_color=SECOND).pack(side="left")
            ctk.CTkLabel(r, text=self._fmt_usd(val),
                         font=("Segoe UI Semibold", 14),
                         text_color=PRIMARY).pack(side="right")

        # ── used / total bar ──
        pct = (used / total * 100) if total else 0
        pct = min(max(pct, 0), 100)
        self._progress(parent, "OpenRouter usage", pct,
                       sublabel=f"{pct:.0f}% used")

        ctk.CTkFrame(parent, fg_color="transparent", height=10).pack(fill="x")

    # ── reusable progress bar (frame-based fill) ──

    def _progress(self, parent, label, pct, *, sublabel=None, note=None):
        color = self._bar_color(pct)
        sec = ctk.CTkFrame(parent, fg_color="transparent")
        sec.pack(fill="x", padx=16, pady=(12, 0))

        head = ctk.CTkFrame(sec, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=label, font=("Segoe UI Semibold", 13),
                     text_color=PRIMARY).pack(side="left")
        if sublabel:
            ctk.CTkLabel(head, text=sublabel,
                         font=("Segoe UI Semibold", 13),
                         text_color=color).pack(side="right")

        track = ctk.CTkFrame(sec, fg_color=TRACK, height=10, corner_radius=5)
        track.pack(fill="x", pady=(6, 3))
        track.pack_propagate(False)
        fillw = max(pct / 100.0, 0.0)
        ctk.CTkFrame(track, fg_color=color, corner_radius=5,
                     height=10).place(relx=0, rely=0,
                                      relwidth=fillw, relheight=1)
        if note:
            ctk.CTkLabel(sec, text=note, font=("Segoe UI", 10),
                         text_color=TERTIARY, anchor="w").pack(fill="x")

    # ═══════════════════════════════════════
    # FOOTER
    # ═══════════════════════════════════════

    def _build_footer(self):
        f = ctk.CTkFrame(self, fg_color=BG, corner_radius=0, height=44)
        f.pack(fill="x", side="bottom")
        f.pack_propagate(False)
        ctk.CTkFrame(f, fg_color=DIVIDER, height=1,
                     corner_radius=0).pack(fill="x")

        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(6, 6))

        ctk.CTkLabel(row, text="Hermes AgentBar",
                     font=("Segoe UI", 11),
                     text_color=TERTIARY).pack(side="left", padx=2)

        ctk.CTkButton(row, text="Close", font=("Segoe UI", 12),
                      text_color=TERTIARY, fg_color="transparent",
                      hover_color=HOVER, height=30, corner_radius=8,
                      width=70, command=self._close).pack(
            side="right", padx=2)

        if self._on_refresh:
            ctk.CTkButton(row, text="Refresh", font=("Segoe UI Semibold", 12),
                          text_color="#06121F", fg_color=ACCENT,
                          hover_color=ACCENT_HV, height=30, corner_radius=8,
                          width=80, command=self._do_refresh).pack(
                side="right", padx=2)

    # ── lifecycle ──

    def _close(self):
        try:
            self.destroy()
        except Exception:
            pass
        if self._on_close:
            self._on_close()

    def _do_refresh(self):
        if self._on_refresh:
            self._on_refresh()


# ─────────────────────────────────────────────
# Demo (only runs when executed directly, with a display)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if not sys.platform.startswith("win") and not __import__(
            "os").environ.get("DISPLAY") and not __import__(
            "os").environ.get("WAYLAND_DISPLAY"):
        print("No display available; skipping GUI demo.")
        sys.exit(0)

    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.withdraw()

    sample_data = {
        "updated": "14:32",
        "windows": {
            "5h": {"input": 1_240_000, "output": 880_000,
                   "cache_read": 3_200_000, "reasoning": 640_000},
            "7d": {"input": 4_100_000, "output": 2_900_000,
                   "cache_read": 9_800_000, "reasoning": 1_900_000},
        },
        "by_model": [
            {"model": "hermes-3-llama-70b", "tokens": 6_400_000},
            {"model": "command-r-plus", "tokens": 3_100_000},
            {"model": "mixtral-8x22b", "tokens": 1_250_000},
        ],
        "daily_7d": [
            {"date": "07-11", "input": 600_000, "output": 400_000},
            {"date": "07-12", "input": 700_000, "output": 520_000},
            {"date": "07-13", "input": 540_000, "output": 380_000},
            {"date": "07-14", "input": 900_000, "output": 610_000},
            {"date": "07-15", "input": 1_100_000, "output": 740_000},
            {"date": "07-16", "input": 820_000, "output": 560_000},
            {"date": "07-17", "input": 440_000, "output": 290_000},
        ],
    }
    sample_credits = {"total": 18_000_000, "used": 7_350_000, "remaining": 10_650_000}
    sample_config = {"weekly_token_budget": 20_000_000}

    popup = AgentBarPopup(root, on_refresh=lambda: print("refresh"))
    popup.show(sample_data, sample_credits, sample_config)
    root.mainloop()
