"""
Headless tests for Hermes AgentBar — NO GUI imports at top level.

These run on a machine without pystray/customtkinter/PIL and with no display.
They exercise only:
  * config.load_config / save_config round-trip
  * compute_status() pure logic
  * HermesAgentBar.do_refresh() with monkeypatched fetchers (no network)

ui_panel, pystray, and PIL are imported ONLY inside hermes_agentbar.run(),
which these tests never call — so importing hermes_agentbar here stays headless.
"""

import os
import sys
import json
import unittest
from unittest import mock

# Ensure the repo root is importable.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import config
import hermes_agentbar
from hermes_agentbar import compute_status, HermesAgentBar


# ─────────────────────────────────────────────
# config round-trip
# ─────────────────────────────────────────────

class TestConfig(unittest.TestCase):
    def _tmp_config(self):
        """Point config at a temp dir and return its path."""
        import tempfile
        d = tempfile.mkdtemp(prefix="habcfg_")
        p = os.path.join(d, "HermesAgentBar", "config.json")
        return d, p

    def test_defaults_without_file(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="habcfg_")
        p = os.path.join(d, "HermesAgentBar", "config.json")
        with mock.patch.object(config, "config_path", return_value=p):
            cfg = config.load_config()
        self.assertEqual(cfg["bridge_url"], "http://127.0.0.1:8766")
        self.assertEqual(cfg["refresh_minutes"], 5)
        self.assertEqual(cfg["weekly_token_budget"], 0)
        # All default keys present.
        for k in config.DEFAULT_CONFIG:
            self.assertIn(k, cfg)

    def test_save_then_load_roundtrip(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="habcfg_")
        p = os.path.join(d, "HermesAgentBar", "config.json")
        with mock.patch.object(config, "config_path", return_value=p):
            cfg_in = dict(config.DEFAULT_CONFIG)
            cfg_in["bridge_url"] = "http://example:9999"
            cfg_in["refresh_minutes"] = 12
            cfg_in["openrouter_key"] = "sk-test-123"
            config.save_config(cfg_in)

            self.assertTrue(os.path.exists(p))
            cfg_out = config.load_config()

        self.assertEqual(cfg_out["bridge_url"], "http://example:9999")
        self.assertEqual(cfg_out["refresh_minutes"], 12)
        self.assertEqual(cfg_out["openrouter_key"], "sk-test-123")
        # Unknown keys in file are ignored, defaults fill gaps.
        with mock.patch.object(config, "config_path", return_value=p):
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"bridge_url": "http://x", "bogus": 1}, f)
            cfg_partial = config.load_config()
        self.assertEqual(cfg_partial["bridge_url"], "http://x")
        self.assertEqual(cfg_partial["refresh_minutes"], config.DEFAULT_CONFIG["refresh_minutes"])
        self.assertNotIn("bogus", cfg_partial)


# ─────────────────────────────────────────────
# compute_status pure logic
# ─────────────────────────────────────────────

class TestComputeStatus(unittest.TestCase):
    def _good_cc(self):
        return {
            "updated": "12:00:00",
            "windows": {
                "5h": {"input": 1000, "output": 250, "cache_read": 30,
                       "cache_write": 5, "reasoning": 40, "sessions": 2},
                "7d": {"input": 9000, "output": 1500, "cache_read": 70,
                       "cache_write": 9, "reasoning": 80, "sessions": 9},
            },
            "by_model": [{"model": "m1", "tokens": 123}],
            "daily_7d": [{"day": "2026-07-17", "input": 10, "output": 20}],
        }

    def test_good_cc_and_or(self):
        cc = self._good_cc()
        credits = {"total": 10.0, "used": 4.0, "remaining": 6.0}
        s = compute_status(cc, credits, {})
        self.assertEqual(s["last5h_tokens"], 1000 + 250)
        self.assertTrue(s["cc_ok"])
        self.assertTrue(s["or_ok"])
        self.assertEqual(s["or_remaining"], 6.0)
        self.assertIn("5h: 1,250", s["tooltip"])

    def test_cc_error_shape_keeps_no_data(self):
        cc = {"error": "HTTP 503"}
        s = compute_status(cc, None, {})
        self.assertFalse(s["cc_ok"])
        self.assertFalse(s["or_ok"])
        self.assertEqual(s["or_remaining"], None)
        self.assertIn("no data", s["tooltip"])

    def test_cc_none(self):
        s = compute_status(None, None, {})
        self.assertFalse(s["cc_ok"])
        self.assertEqual(s["last5h_tokens"], 0)
        self.assertIn("no data", s["tooltip"])

    def test_or_error_shape(self):
        cc = self._good_cc()
        credits = {"error": "HTTP 401", "total": 0, "used": 0, "remaining": 0}
        s = compute_status(cc, credits, {})
        self.assertTrue(s["cc_ok"])
        self.assertFalse(s["or_ok"])
        self.assertEqual(s["or_remaining"], None)


# ─────────────────────────────────────────────
# HermesAgentBar.do_refresh with stubbed fetchers (no network)
# ─────────────────────────────────────────────

class TestRefreshHeadless(unittest.TestCase):
    def test_refresh_stores_last_good_and_skips_bad(self):
        cfg = dict(config.DEFAULT_CONFIG)
        cfg["openrouter_key"] = "sk-test"

        good_cc = {
            "windows": {"5h": {"input": 500, "output": 500},
                        "7d": {"input": 1, "output": 1}},
            "by_model": [], "daily_7d": [],
        }
        good_or = {"total": 20.0, "used": 5.0, "remaining": 15.0}

        with mock.patch("hermes_agentbar.config.load_config",
                        return_value=cfg), \
             mock.patch("hermes_agentbar.OpenRouterFetcher") as MockOR, \
             mock.patch("hermes_agentbar.CommandCodeFetcher") as MockCC:
            # The app builds real fetcher instances from cfg; make them stubs.
            cc_inst = mock.MagicMock()
            cc_inst.fetch.return_value = good_cc
            MockCC.return_value = cc_inst

            or_inst = mock.MagicMock()
            or_inst.api_key = "sk-test"
            or_inst.fetch.return_value = good_or
            MockOR.return_value = or_inst

            app = HermesAgentBar(cfg)
            # No GUI: popups/icon are None, _to_tk is a no-op.
            status = app.do_refresh()

        self.assertTrue(app.has_any_data)
        self.assertIs(app.last_cc, good_cc)
        self.assertIs(app.last_credits, good_or)
        self.assertEqual(status["last5h_tokens"], 1000)
        self.assertTrue(status["cc_ok"])
        self.assertEqual(status["or_remaining"], 15.0)

    def test_refresh_reloads_config_and_rebuilds_or_fetcher_on_key_change(self):
        """Regression guard: editing config.json + Refresh must take effect."""
        cfg = dict(config.DEFAULT_CONFIG)

        # First state: no OpenRouter key configured.
        state = {"cfg": dict(config.DEFAULT_CONFIG)}
        with mock.patch("hermes_agentbar.config.load_config",
                        side_effect=lambda: state["cfg"]), \
             mock.patch("hermes_agentbar.OpenRouterFetcher") as MockOR, \
             mock.patch("hermes_agentbar.CommandCodeFetcher") as MockCC:
            cc_inst = mock.MagicMock()
            cc_inst.fetch.return_value = {"windows": {"5h": {"input": 1, "output": 1}}}
            MockCC.return_value = cc_inst
            or_inst = mock.MagicMock()
            or_inst.api_key = "sk-old"
            or_inst.fetch.return_value = {"total": 1.0, "used": 0.0, "remaining": 1.0}
            MockOR.return_value = or_inst

            app = HermesAgentBar(cfg)
            # First refresh: no OR key configured -> or_fetcher stays None.
            app.do_refresh()
            self.assertIsNone(app.or_fetcher)

            # Simulate the user editing config.json to add a key, then Refresh.
            state["cfg"] = dict(config.DEFAULT_CONFIG)
            state["cfg"]["openrouter_key"] = "sk-new"
            app.do_refresh()
            # A fetcher must now exist, and it must have been built with the
            # *new* key (proves do_refresh() re-read config + rebuilt it).
            self.assertIsNotNone(app.or_fetcher)
            self.assertTrue(MockOR.called)
            # Last constructor call received the new key as its first arg.
            self.assertEqual(MockOR.call_args_list[-1].args[0], "sk-new")

    def test_refresh_keeps_last_good_on_error(self):
        cfg = dict(config.DEFAULT_CONFIG)
        app = HermesAgentBar(cfg)

        app.cc_fetcher = mock.MagicMock()
        app.cc_fetcher.fetch.return_value = {"windows": {"5h": {"input": 7, "output": 3}}}
        app.or_fetcher = None  # no key configured

        app.do_refresh()
        self.assertEqual(app.last_cc["windows"]["5h"]["input"], 7)

        # Now the bridge errors; last-good must be retained.
        app.cc_fetcher.fetch.return_value = {"error": "boom"}
        status = app.do_refresh()
        self.assertEqual(app.last_cc["windows"]["5h"]["input"], 7)
        # Last-good data is retained, so cc_ok stays True even though the
        # *latest* fetch failed (the stored payload is still valid).
        self.assertTrue(status["cc_ok"])
        self.assertEqual(app.last_cc["windows"]["5h"]["input"], 7)


if __name__ == "__main__":
    unittest.main()
