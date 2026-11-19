#!/usr/bin/env python3
"""
Uptime Monitor with Telegram alerts.

What it does:
  * Checks a list of websites / servers every N seconds.
  * Remembers the last status of every site in a small SQLite database.
  * Sends a Telegram message ONLY when a site goes DOWN or comes back UP
    (so you do not get spammed every minute).

Only the Python standard library is used - nothing to install.
"""

import json          # read the config file
import os            # read environment variables (bot token, chat id)
import socket        # for TCP port checks
import sqlite3       # tiny built-in database for history
import sys           # exit codes and command line arguments
import time          # sleep between checks and measure response time
import urllib.error  # HTTP / network errors
import urllib.parse  # build the Telegram request body
import urllib.request  # make HTTP requests
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Settings (can be overridden in config.json)
# ---------------------------------------------------------------------------
DEFAULT_INTERVAL = 60   # seconds between two full checks
DEFAULT_TIMEOUT = 10    # seconds to wait for a site before calling it DOWN
DB_FILE = "monitor.db"  # SQLite file created next to the script


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def init_db(path):
    """Create the database file and tables if they do not exist yet."""
    conn = sqlite3.connect(path)
    # "checks" keeps the full history of every check (useful for reports).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            is_up INTEGER NOT NULL,       -- 1 = up, 0 = down
            status_code INTEGER,          -- HTTP status, NULL for TCP checks
            response_ms REAL,             -- how long the check took
            error TEXT,                   -- error text when something failed
            checked_at TEXT NOT NULL      -- UTC time in ISO format
        )
        """
    )
    # "state" keeps ONLY the last known status per site, so we can detect changes.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS state (
            name TEXT PRIMARY KEY,
            is_up INTEGER NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def get_last_state(conn, name):
    """Return True/False for the last known status, or None if never checked."""
    row = conn.execute("SELECT is_up FROM state WHERE name = ?", (name,)).fetchone()
    return None if row is None else bool(row[0])


def save_result(conn, name, result):
    """Store one check result in history and update the last known state."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO checks (name, is_up, status_code, response_ms, error, checked_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, int(result["is_up"]), result["status_code"],
         result["response_ms"], result["error"], now),
    )
    # INSERT OR REPLACE = create the row the first time, overwrite it later.
    conn.execute(
        "INSERT OR REPLACE INTO state (name, is_up) VALUES (?, ?)",
        (name, int(result["is_up"])),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# The actual checks
# ---------------------------------------------------------------------------
def check_http(url, timeout):
    """Open a URL and report if it answered with a good status (below 400)."""
    started = time.monotonic()
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "uptime-monitor/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code = response.status
        ok = code < 400
        error = None if ok else "HTTP " + str(code)
    except urllib.error.HTTPError as exc:
        # The server answered, but with an error code such as 404 or 500.
        code, ok, error = exc.code, False, "HTTP " + str(exc.code)
    except Exception as exc:  # timeout, DNS error, connection refused, SSL error...
        code, ok, error = None, False, type(exc).__name__ + ": " + str(exc)
    elapsed = (time.monotonic() - started) * 1000
    return {"is_up": ok, "status_code": code, "response_ms": round(elapsed, 1), "error": error}


def check_tcp(host, port, timeout):
    """Try to open a TCP connection to host:port (useful for SSH, databases...)."""
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ok, error = True, None
    except Exception as exc:
        ok, error = False, type(exc).__name__ + ": " + str(exc)
    elapsed = (time.monotonic() - started) * 1000
    return {"is_up": ok, "status_code": None, "response_ms": round(elapsed, 1), "error": error}


def run_check(target, timeout):
    """Pick the right check type for one entry of the config file."""
    if target.get("type", "http") == "tcp":
        return check_tcp(target["host"], int(target["port"]), timeout)
    return check_http(target["url"], timeout)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(text):
    """Send a message through the Telegram Bot API.

    The token and chat id come from environment variables, so secrets are never
    written into the code or committed to Git:
        TELEGRAM_BOT_TOKEN  - from @BotFather
        TELEGRAM_CHAT_ID    - your chat id
    If they are missing, the message is just printed (handy for testing).
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[no Telegram credentials] " + text)
        return
    api_url = "https://api.telegram.org/bot" + token + "/sendMessage"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        urllib.request.urlopen(api_url, data=body, timeout=10).read()
    except Exception as exc:
        # Never crash the monitor just because Telegram is unreachable.
        # (Do not print the URL: it contains the token.)
        print("Could not send Telegram message: " + type(exc).__name__)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------
def describe(target):
    """Human readable name used in messages and in the database."""
    return target.get("name") or target.get("url") or (target["host"] + ":" + str(target["port"]))


def run_once(config, conn):
    """Check every target one time and alert on status changes."""
    timeout = config.get("timeout", DEFAULT_TIMEOUT)
    for target in config["targets"]:
        name = describe(target)
        result = run_check(target, timeout)
        previous = get_last_state(conn, name)   # None / True / False
        save_result(conn, name, result)

        status = "UP" if result["is_up"] else "DOWN"
        print(datetime.now().strftime("%H:%M:%S") + "  " + name + "  " + status
              + "  " + str(result["response_ms"]) + " ms")

        # Alert rules:
        #  - went from up -> down  : alert
        #  - went from down -> up  : alert "recovered"
        #  - first ever check that is already down : alert
        if previous is True and not result["is_up"]:
            send_telegram("DOWN: " + name + "\n" + str(result["error"]))
        elif previous is False and result["is_up"]:
            send_telegram("RECOVERED: " + name + " is back up (" + str(result["response_ms"]) + " ms)")
        elif previous is None and not result["is_up"]:
            send_telegram("DOWN: " + name + "\n" + str(result["error"]))


def main():
    # Usage: python monitor.py [config.json] [--once]
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    config_path = args[0] if args else "config.json"
    run_forever = "--once" not in sys.argv

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    conn = init_db(config.get("db_file", DB_FILE))
    interval = config.get("interval", DEFAULT_INTERVAL)

    if not run_forever:
        run_once(config, conn)
        return

    print("Monitoring " + str(len(config["targets"])) + " targets every " + str(interval) + " s. Ctrl+C to stop.")
    try:
        while True:
            run_once(config, conn)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
