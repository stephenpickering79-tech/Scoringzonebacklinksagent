#!/usr/bin/env python3
"""
Railway entrypoint for the Scoring Zone backlink agent.

One always-on process that does everything Railway needs:
  1. On boot: regenerate the dashboard JSON so the site isn't empty after a deploy.
  2. Background scheduler: once a day at RUN_HOUR (default 09:00, server time), run the
     PROPOSE pipeline (research + score) then rebuild the dashboard data. Never submits.
  3. Web server: serve the static dashboard in docs/ on 0.0.0.0:$PORT (Railway injects $PORT).

Run data (data/) lives on a Railway volume mounted at /app/data so it survives redeploys.
Submissions stay manual (run submit_*.py on demand) — they are NOT part of this process.

Env vars:
    PORT            injected by Railway (default 8080 locally)
    RUN_HOUR        hour of day (0-23) to run the daily propose job (default 9)
    RUN_ON_START    "true" to also run one propose cycle right after boot (handy on first deploy)
    STEEL_API_KEY   for research scraping (without it, research just returns nothing)
    OPENROUTER_API_KEY  optional, for LLM scoring (falls back to rule-based if absent)

Local test:
    PORT=8000 RUN_ON_START=true python serve.py
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timedelta
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR / "docs"
DATA_DIR = BASE_DIR / "data"

# Ensure repo root is importable so `backlink_agent` resolves when run as a script.
sys.path.insert(0, str(BASE_DIR))

from backlink_agent import dashboard          # noqa: E402
from backlink_agent import orchestrator       # noqa: E402

RUN_HOUR = int(os.getenv("RUN_HOUR", "9"))
PORT = int(os.getenv("PORT", "8080"))


def log(msg: str) -> None:
    print(f"[serve {datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def run_propose_cycle() -> None:
    """Run the daily research+score pipeline, then rebuild dashboard data. Never raises."""
    log("Propose cycle starting…")
    # The orchestrator reads these at call time via load_config().
    os.environ["AGENT_MODE"] = "propose"
    os.environ["DRY_RUN"] = "true"
    try:
        orchestrator.main()
    except Exception as e:
        log(f"orchestrator.main() failed: {e}")
    try:
        dashboard.generate_all()
    except Exception as e:
        log(f"dashboard.generate_all() failed: {e}")
    log("Propose cycle finished.")


def _next_run_after(now: datetime) -> datetime:
    nxt = now.replace(hour=RUN_HOUR, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return nxt


def scheduler_loop() -> None:
    """Sleep until the next RUN_HOUR, run the cycle, repeat. Survives individual failures."""
    while True:
        now = datetime.now()
        nxt = _next_run_after(now)
        wait_s = max(1.0, (nxt - now).total_seconds())
        log(f"Next propose run at {nxt.isoformat(timespec='minutes')} (in {wait_s / 3600:.1f}h)")
        time.sleep(wait_s)
        try:
            run_propose_cycle()
        except Exception as e:  # belt-and-braces; run_propose_cycle already guards internally
            log(f"scheduler caught: {e}")


def main() -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)

    # Refresh dashboard data at boot so the deployed site reflects the latest tracker + volume data.
    try:
        dashboard.generate_all()
    except Exception as e:
        log(f"boot dashboard.generate_all() failed: {e}")

    if os.getenv("RUN_ON_START", "").lower() in ("1", "true", "yes"):
        log("RUN_ON_START set — kicking off one propose cycle in the background.")
        threading.Thread(target=run_propose_cycle, name="propose-on-start", daemon=True).start()

    threading.Thread(target=scheduler_loop, name="scheduler", daemon=True).start()

    handler = partial(SimpleHTTPRequestHandler, directory=str(DOCS_DIR))
    httpd = HTTPServer(("0.0.0.0", PORT), handler)
    log(f"Serving dashboard from docs/ on 0.0.0.0:{PORT} (daily propose at {RUN_HOUR:02d}:00 server time)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down.")
        httpd.server_close()


if __name__ == "__main__":
    main()
