"""
tests/test_fetchers.py — Hermes AgentBar
========================================

Unit tests for fetchers.py. All HTTP is mocked via unittest.mock, so there
is NO live network dependency.

Run with:  python -m pytest tests/test_fetchers.py -q
       or: python -m unittest tests.test_fetchers
"""

import json
import unittest
from unittest import mock

import requests

from fetchers import CommandCodeCostFetcher, CommandCodeFetcher, OpenRouterFetcher


# A representative bridge payload matching the documented contract.
SAMPLE_USAGE = {
    "updated": "2026-07-17T13:00:00Z",
    "windows": {
        "5h": {
            "input": 1000,
            "output": 500,
            "cache_read": 2000,
            "cache_write": 100,
            "reasoning": 300,
            "sessions": 4,
        },
        "7d": {
            "input": 8000,
            "output": 4000,
            "cache_read": 5000,
            "cache_write": 400,
            "reasoning": 1500,
            "sessions": 22,
        },
    },
    "by_model": [{"model": "gpt-5", "tokens": 12000}],
    "daily_7d": [{"day": "2026-07-11", "input": 1000, "output": 500}],
}


def _make_response(status_code: int, payload=None, text=""):
    """Build a fake requests.Response object."""
    resp = mock.Mock(spec=requests.Response)
    resp.status_code = status_code
    if payload is not None:
        resp.json.return_value = payload
    resp.text = text
    return resp


# ─────────────────────────────────────────────
# CommandCodeFetcher (bridge)
# ─────────────────────────────────────────────

class TestCommandCodeFetcher(unittest.TestCase):
    def test_no_trailing_slash_double(self):
        fetcher = CommandCodeFetcher(base_url="http://localhost:8766/")
        fake = _make_response(200, SAMPLE_USAGE)
        with mock.patch("requests.get", return_value=fake) as m_get:
            fetcher.fetch()
        self.assertEqual(m_get.call_args[0][0], "http://localhost:8766/api/usage")

    def test_fetch_parses_documented_json(self):
        fetcher = CommandCodeFetcher(base_url="http://localhost:8766")
        fake = _make_response(200, SAMPLE_USAGE)
        with mock.patch("requests.get", return_value=fake):
            data = fetcher.fetch()
        self.assertEqual(data["windows"]["5h"]["input"], 1000)
        self.assertEqual(data["windows"]["7d"]["sessions"], 22)
        self.assertFalse(data.get("error"))

    def test_fetch_returns_error_shape_on_network_error(self):
        fetcher = CommandCodeFetcher(base_url="http://localhost:8766")
        with mock.patch("requests.get", side_effect=requests.Timeout("timed out")):
            data = fetcher.fetch()
        self.assertIn("timed out", data["error"])

    def test_fetch_returns_error_shape_on_500(self):
        fetcher = CommandCodeFetcher(base_url="http://localhost:8766")
        fake = _make_response(500, text="nope")
        with mock.patch("requests.get", return_value=fake):
            data = fetcher.fetch()
        self.assertEqual(data["error"], "HTTP 500")

    def test_fetch_returns_error_shape_on_bad_json(self):
        fetcher = CommandCodeFetcher(base_url="http://localhost:8766")
        fake = mock.Mock(spec=requests.Response)
        fake.status_code = 200
        fake.json.side_effect = ValueError("bad")
        with mock.patch("requests.get", return_value=fake):
            data = fetcher.fetch()
        self.assertIn("invalid JSON", data["error"])

    def test_budget_pct_math(self):
        fetcher = CommandCodeFetcher(base_url="http://localhost:8766")
        pct = fetcher.budget_pct(SAMPLE_USAGE, 100_000)
        # 7d input 8000 + output 4000 + cache_read 5000 = 17000
        self.assertAlmostEqual(pct, 17.0, places=1)

    def test_budget_pct_zero_budget_safe(self):
        fetcher = CommandCodeFetcher(base_url="http://localhost:8766")
        self.assertEqual(fetcher.budget_pct(SAMPLE_USAGE, 0), 0.0)

    def test_budget_pct_clamps_lower_and_overruns(self):
        fetcher = CommandCodeFetcher(base_url="http://localhost:8766")
        self.assertEqual(fetcher.budget_pct(SAMPLE_USAGE, 10), 170000.0)
        self.assertEqual(fetcher.budget_pct({}, 1000), 0.0)


# ─────────────────────────────────────────────
# OpenRouterFetcher
# ─────────────────────────────────────────────

class TestOpenRouterFetcher(unittest.TestCase):
    def test_fetch_computes_remaining(self):
        fetcher = OpenRouterFetcher(api_key="sk-test")
        payload = {"data": {"total_credits": 317.0, "total_usage": 289.9}}
        fake = _make_response(200, payload)
        with mock.patch("requests.get", return_value=fake) as m_get:
            data = fetcher.fetch()

        args, kwargs = m_get.call_args
        self.assertEqual(args[0], "https://openrouter.ai/api/v1/credits")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test")

        self.assertAlmostEqual(data["total"], 317.0, places=6)
        self.assertAlmostEqual(data["used"], 289.9, places=6)
        self.assertAlmostEqual(data["remaining"], 27.1, places=6)
        self.assertNotIn("error", data)

    def test_fetch_error_shape_on_500(self):
        fetcher = OpenRouterFetcher(api_key="sk-test")
        fake = _make_response(500, text="nope")
        with mock.patch("requests.get", return_value=fake):
            data = fetcher.fetch()
        self.assertEqual(data["error"], "HTTP 500")
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["used"], 0)
        self.assertEqual(data["remaining"], 0)

    def test_fetch_error_shape_on_network_error(self):
        fetcher = OpenRouterFetcher(api_key="sk-test")
        with mock.patch(
            "requests.get", side_effect=requests.Timeout("timed out")
        ):
            data = fetcher.fetch()
        self.assertIn("timed out", data["error"])
        self.assertEqual(data["total"], 0)
        self.assertEqual(data["used"], 0)
        self.assertEqual(data["remaining"], 0)


# ─────────────────────────────────────────────
# CommandCodeCostFetcher (billing endpoint)
# ─────────────────────────────────────────────

# Real API response shape from /internal/billing/credits
SAMPLE_BILLING = {
    "credits": {
        "belowThreshold": False,
        "monthlyCredits": 101.28,
        "purchasedCredits": 0,
        "premiumMonthlyCredits": 94.44,
        "opensourceMonthlyCredits": 6.85,
    },
    "windowLimits": {
        "limited": True,
        "fiveHour": {
            "used": 0.0087,
            "cap": 45,
            "exceeded": False,
            "resetAt": 1784341496479,
        },
        "weekly": {
            "used": 10.6391,
            "cap": 90,
            "exceeded": False,
            "resetAt": 1784475788422,
        },
    },
}


class TestCommandCodeCostFetcher(unittest.TestCase):
    def _make_response(self, payload):
        return _make_response(200, payload)

    def test_fetch_parses_billing_payload(self):
        fetcher = CommandCodeCostFetcher(cookie_header="__Secure-...")
        fake = self._make_response(SAMPLE_BILLING)
        with mock.patch("requests.get", return_value=fake):
            result = fetcher.fetch()

        self.assertEqual(result["error"], "")
        self.assertAlmostEqual(result["cost_5h"], 0.0087, places=6)
        self.assertAlmostEqual(result["cost_7d"], 10.6391, places=6)
        self.assertEqual(result["budget_5h"], 45)
        self.assertEqual(result["budget_7d"], 90)
        # resetAt in ms → converted to seconds
        self.assertAlmostEqual(result["reset_5h"], 1784341496.479, places=3)
        self.assertAlmostEqual(result["reset_7d"], 1784475788.422, places=3)
        self.assertAlmostEqual(result["pct_5h"], 0.0087 / 45 * 100, places=4)
        self.assertAlmostEqual(result["pct_7d"], 10.6391 / 90 * 100, places=4)

    def test_http_500_returns_error(self):
        fetcher = CommandCodeCostFetcher(cookie_header="test")
        resp = mock.Mock(spec=requests.Response)
        resp.status_code = 500
        with mock.patch("requests.get", return_value=resp):
            result = fetcher.fetch()
        self.assertNotEqual(result["error"], "")
        self.assertEqual(result["cost_5h"], 0)

    def test_network_failure_returns_error(self):
        fetcher = CommandCodeCostFetcher(cookie_header="test")
        with mock.patch("requests.get",
                        side_effect=requests.Timeout("timed out")):
            result = fetcher.fetch()
        self.assertNotEqual(result["error"], "")

    def test_missing_keys_graceful(self):
        """Partial payloads degrade safely."""
        fetcher = CommandCodeCostFetcher(cookie_header="test")
        fake = self._make_response({})  # no windowLimits at all
        with mock.patch("requests.get", return_value=fake):
            result = fetcher.fetch()
        self.assertEqual(result["cost_5h"], 0)
        self.assertEqual(result["cost_7d"], 0)
        self.assertEqual(result["budget_5h"], 45)
        self.assertIsNone(result["reset_5h"])

    def test_invalid_json_returns_error(self):
        fetcher = CommandCodeCostFetcher(cookie_header="test")
        fake = mock.Mock(spec=requests.Response)
        fake.status_code = 200
        fake.json.side_effect = ValueError("bad")
        with mock.patch("requests.get", return_value=fake):
            result = fetcher.fetch()
        self.assertNotEqual(result["error"], "")


if __name__ == "__main__":
    unittest.main()
