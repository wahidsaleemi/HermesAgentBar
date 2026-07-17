#!/usr/bin/env python3
"""
Hermes AgentBar -- read-only HTTP bridge for Command Code token usage.

Serves GET /api/usage as JSON, sourced read-only from Hermes' state.db
(~/.hermes/state.db). No writes are ever performed against the database.

Design goals (per project brief):
  * stdlib only (http.server) -- no Flask or third-party deps.
  * Read-only DB access via sqlite3 URI mode=ro.
  * Parameterized window sizes; all SQL is constant except bound params.
  * Binds loopback 127.0.0.1 by default; optional Bearer-token auth.
  * Graceful handling of a busy/contended DB -> HTTP 503 JSON.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# --- Configuration (env-overridable) -----------------------------------

DB_PATH = os.environ.get("HERMES_AGENTBAR_DB", "/home/wahid/.hermes/state.db")
HOST = os.environ.get("HERMES_AGENTBAR_HOST", "127.0.0.1")
PORT = int(os.environ.get("HERMES_AGENTBAR_PORT", "8766"))
# If set, require "Authorization: Bearer <token>" on every request.
REQUIRED_TOKEN = os.environ.get("HERMES_AGENTBAR_TOKEN", "")

# Constant, project-specified filter for Command Code rows. These literals
# come from the brief and are NEVER derived from user input.
CC_WHERE = (
    "(billing_base_url LIKE '%commandcode.ai%' OR billing_provider LIKE '%commandcode%')"
)

# Window definitions. Seconds are passed to sqlite as bound parameters so the
# window sizes are always parameterized (no string interpolation into SQL).
WINDOWS = {
    "5h": 5 * 3600,
    "7d": 7 * 86400,
}


def _connect():
    """Open the DB strictly read-only. Raises if the file is missing."""
    uri = "file:%s?mode=ro" % sqlite3.connect  # placeholder; real URI built below
    # Build a file:// URI with mode=ro so writes are refused by SQLite itself.
    path = DB_PATH
    uri = "file:" + path + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    # Brief busy timeout: if the DB is briefly locked by Hermes, wait a little
    # instead of immediately failing. 50ms per the brief.
    con.execute("PRAGMA busy_timeout=50")
    return con


def _window_row(cur, seconds):
    """Aggregate one window (input/output/cache_read/cache_write/reasoning/sessions)."""
    cur.execute(
        "SELECT COALESCE(SUM(input_tokens),0),"
        "       COALESCE(SUM(output_tokens),0),"
        "       COALESCE(SUM(cache_read_tokens),0),"
        "       COALESCE(SUM(cache_write_tokens),0),"
        "       COALESCE(SUM(reasoning_tokens),0),"
        "       COUNT(DISTINCT session_id) "
        "FROM session_model_usage "
        "WHERE " + CC_WHERE + " AND last_seen >= ?",
        (seconds,),
    )
    r = cur.fetchone()
    return {
        "input": int(r[0]),
        "output": int(r[1]),
        "cache_read": int(r[2]),
        "cache_write": int(r[3]),
        "reasoning": int(r[4]),
        "sessions": int(r[5]),
    }


def _by_model(cur, seconds):
    """Top 10 models by (input+output+cache_read) over the 7d window."""
    cur.execute(
        "SELECT model,"
        "       COALESCE(SUM(input_tokens),0)+COALESCE(SUM(output_tokens),0)"
        "       +COALESCE(SUM(cache_read_tokens),0) AS tokens "
        "FROM session_model_usage "
        "WHERE " + CC_WHERE + " AND last_seen >= ? "
        "GROUP BY model ORDER BY tokens DESC LIMIT 10",
        (seconds,),
    )
    return [{"model": m, "tokens": int(t)} for m, t in cur.fetchall()]


def _daily_7d(cur, start_ts):
    """Last 7 calendar days (today + 6 prior), input/output per day."""
    cur.execute(
        "SELECT substr(date(last_seen,'unixepoch','localtime'),1,10) AS day,"
        "       COALESCE(SUM(input_tokens),0),"
        "       COALESCE(SUM(output_tokens),0) "
        "FROM session_model_usage "
        "WHERE " + CC_WHERE + " AND last_seen >= ? "
        "GROUP BY day ORDER BY day",
        (start_ts,),
    )
    rows = cur.fetchall()
    by_day = {d: (i, o) for d, i, o in rows}
    # Ensure exactly 7 calendar days are present, even if a day has no usage.
    out = []
    base = datetime.fromtimestamp(start_ts)
    for k in range(7):
        d = (base + timedelta(days=k)).strftime("%Y-%m-%d")
        i, o = by_day.get(d, (0, 0))
        out.append({"day": d, "input": int(i), "output": int(o)})
    return out


def build_usage():
    """Query the DB and assemble the JSON contract. Raises sqlite3.OperationalError."""
    con = _connect()
    try:
        cur = con.cursor()
        now = time.time()
        windows = {}
        for name, sec in WINDOWS.items():
            windows[name] = _window_row(cur, now - sec)
        by_model = _by_model(cur, now - WINDOWS["7d"])
        # Last 7 calendar days: today's local midnight minus 6 days.
        today_mid = datetime.fromtimestamp(now).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_ts = (today_mid - timedelta(days=6)).timestamp()
        daily = _daily_7d(cur, start_ts)
        return {
            "updated": datetime.fromtimestamp(now).strftime("%H:%M:%S"),
            "windows": windows,
            "by_model": by_model,
            "daily_7d": daily,
        }
    finally:
        con.close()


class Handler(BaseHTTPRequestHandler):
    # Silence default request logging noise.
    def log_message(self, format, *args):  # noqa: A002 - matches base signature
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self):
        if not REQUIRED_TOKEN:
            return True
        h = self.headers.get("Authorization", "")
        return h == "Bearer " + REQUIRED_TOKEN

    def do_GET(self):
        # Only /api/usage (with or without ?windows=1) is served.
        path = self.path.split("?", 1)[0]
        if path not in ("/api/usage",):
            self._send_json({"error": "not_found", "detail": path}, status=404)
            return

        if not self._check_auth():
            self._send_json(
                {"error": "unauthorized", "detail": "Bearer token required"}, status=401
            )
            return

        try:
            payload = build_usage()
        except sqlite3.OperationalError as e:
            # DB busy / locked / contended -> 503, never crash the server.
            self._send_json(
                {
                    "error": "database_unavailable",
                    "detail": "state.db is locked or busy; try again shortly",
                },
                status=503,
            )
            return
        except Exception as e:  # pragma: no cover - defensive
            self._send_json({"error": "internal_error", "detail": str(e)}, status=500)
            return

        self._send_json(payload, status=200)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    auth_note = " (Bearer auth ENABLED)" if REQUIRED_TOKEN else " (no auth, loopback)"
    print(
        "Hermes AgentBar bridge listening on http://%s:%d%s" % (HOST, PORT, auth_note)
    )
    print("  DB (read-only): %s" % DB_PATH)
    print("  Endpoint: GET /api/usage")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
