#!/usr/bin/env python3
"""
Railway entrypoint for the Scoring Zone backlink agent.

One always-on process that does everything Railway needs:
  1. On boot: regenerate the dashboard JSON so the site isn't empty after a deploy.
  2. Background scheduler: once a day at RUN_HOUR (default 09:00, server time), run the
     PROPOSE pipeline (research + score) then rebuild the dashboard data. Never submits.
  3. Web server: serve the static dashboard in docs/ on 0.0.0.0:$PORT (Railway injects $PORT),
     plus two tiny POST endpoints the dashboard buttons use:
       POST /api/approve  {name,url}  → queue a target (data/approvals.json) for submission
       POST /api/dismiss  {name,url}  → add a target to the runtime exclusion list
     Both are unauthenticated by design (public page); a click only writes to a queue/exclusion
     file. The daily run decides whether to act and only auto-submits scripted sites after the
     go-live date (see backlink_agent/orchestrator.process_approvals + AUTO_SUBMIT_* env vars).

Run data (data/) lives on a Railway volume mounted at /app/data so it survives redeploys.
Submissions stay manual unless you opt into the auto phase (AUTO_SUBMIT_ENABLED).

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

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR / "docs"
DATA_DIR = BASE_DIR / "data"

# Ensure repo root is importable so `backlink_agent` resolves when run as a script.
sys.path.insert(0, str(BASE_DIR))

from backlink_agent import approvals          # noqa: E402
from backlink_agent import dashboard          # noqa: E402
from backlink_agent import orchestrator       # noqa: E402
from backlink_agent import research           # noqa: E402
from backlink_agent import submitter          # noqa: E402
from steel_utils import send_alert            # noqa: E402

MAX_POST_BYTES = 64_000  # approve/dismiss payloads are tiny; reject anything larger.


def _autosubmit_enabled() -> bool:
    return os.getenv("AUTO_SUBMIT_ENABLED", "").strip().lower() in ("1", "true", "yes")


def _autosubmit_dry_run() -> bool:
    # Safe default: dry-run ON unless explicitly disabled. A real submission requires
    # setting AUTO_SUBMIT_DRY_RUN=false, so a fresh deploy never posts by accident.
    return os.getenv("AUTO_SUBMIT_DRY_RUN", "true").strip().lower() not in ("0", "false", "no")


def _approval_pending(name: str, url: str) -> bool:
    """True if this target is queued and still awaiting submission (status 'approved'),
    so we don't double-fire one already submitting/submitted."""
    key = approvals._key(name, url)
    for a in approvals.load_approvals():
        if approvals._key(a.get("name", ""), a.get("url", "")) == key:
            return a.get("status") == "approved"
    return False

RUN_HOUR = int(os.getenv("RUN_HOUR", "9"))
PORT = int(os.getenv("PORT", "8080"))


def log(msg: str) -> None:
    print(f"[serve {datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)


def _add_runtime_exclusion(phrase: str) -> bool:
    """Append a phrase to data/excluded.json (research.load_excluded reads it).
    Idempotent. Returns True if newly added."""
    path = research.RUNTIME_EXCLUDED_FILE
    existing: list = []
    try:
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        existing = []
    if not isinstance(existing, list):
        existing = []
    if any(str(e).strip().lower() == phrase.strip().lower() for e in existing):
        return False
    existing.append(phrase)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
    return True


class DashboardHandler(SimpleHTTPRequestHandler):
    """Serves the static dashboard (GET, via the parent) and accepts two small
    POST endpoints used by the Approve / Dismiss buttons.

    These are intentionally unauthenticated (Stephen's explicit choice for a
    public page). The blast radius is limited by design: a click only writes to
    a queue/exclusion file — the agent decides on its own schedule whether to
    act, and only ever auto-submits to the scripted sites after the go-live date.
    """

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            return None
        if length <= 0 or length > MAX_POST_BYTES:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return None

    def _send_json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _token_ok(self) -> bool:
        """When APPROVE_TOKEN is set, require it (header X-Approve-Token or ?k=). When
        unset, allow (back-compat) but warn — a public page can't hold a true secret, so
        this just blocks drive-by/bot hits; the allowlist + velocity cap bound the rest."""
        want = os.getenv("APPROVE_TOKEN", "").strip()
        if not want:
            log("WARNING: APPROVE_TOKEN not set — /api/approve is unauthenticated.")
            return True
        got = (self.headers.get("X-Approve-Token") or "").strip()
        if not got:
            got = parse_qs(urlparse(self.path).query).get("k", [""])[0].strip()
        return got == want

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        route = urlparse(self.path).path  # ignore query string (?k=token)
        handlers = {
            "/api/approve": self._handle_approve,
            "/api/dismiss": self._handle_dismiss,
            "/api/mark-submitted": self._handle_mark_submitted,
            "/api/check-live": self._handle_check_live,
        }
        if route in handlers:
            if not self._token_ok():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            handlers[route]()
        else:
            self._send_json(404, {"ok": False, "error": "unknown endpoint"})

    def _handle_mark_submitted(self) -> None:
        """Record a target the human submitted manually as 'submitted' (no browser run).
        Updates the Approve queue + the submission log so both tabs reflect it."""
        payload = self._read_json() or {}
        name = (payload.get("name") or "").strip()
        url = (payload.get("url") or "").strip()
        if not name and not url:
            self._send_json(400, {"ok": False, "error": "name or url required"})
            return
        try:
            from steel_utils import record_submission
            approvals.set_status(name, url, "submitted", "Marked submitted manually.")
            record_submission(name, url, status="Submitted (manual)", method="manual",
                              notes="Marked submitted manually from the dashboard.")
            dashboard.refresh_approvals()
            dashboard.refresh_submissions()
            dashboard.refresh_livecheck()  # a manual submit creates a new "watching" row
        except Exception as e:
            log(f"mark-submitted failed: {e}")
            self._send_json(500, {"ok": False, "error": "could not mark submitted"})
            return
        log(f"mark-submitted: {name or url}")
        self._send_json(200, {"ok": True, "name": name or url})

    def _handle_check_live(self) -> None:
        """Force a live-link check for one target now (the 'Check now' button).
        Synchronous: the requests path is fast; worst case is one Steel cheap-scrape."""
        payload = self._read_json() or {}
        name = (payload.get("name") or "").strip()
        url = (payload.get("url") or "").strip()
        if not name and not url:
            self._send_json(400, {"ok": False, "error": "name or url required"})
            return
        try:
            from backlink_agent import livecheck
            result = livecheck.check_one(name, url)
            dashboard.refresh_submissions()
            dashboard.refresh_livecheck()
        except Exception as e:
            log(f"check-live failed: {e}")
            self._send_json(500, {"ok": False, "error": "check failed"})
            return
        log(f"check-live: {name or url} → found={result.get('found')}")
        self._send_json(200, {"ok": True, **result})

    def _handle_approve(self) -> None:
        payload = self._read_json() or {}
        name = (payload.get("name") or "").strip()
        url = (payload.get("url") or "").strip()
        if not name and not url:
            self._send_json(400, {"ok": False, "error": "name or url required"})
            return
        try:
            added = approvals.add_approval(name, url)
            dashboard.refresh_approvals()  # so the Approved panel reflects it now
        except Exception as e:
            log(f"approve failed: {e}")
            self._send_json(500, {"ok": False, "error": "could not queue approval"})
            return

        # If auto-submit is enabled, the agent acts on this approval immediately in a
        # background thread (scripted site → its script; otherwise the generic submitter).
        # Triggers whenever the item is still "approved" (newly added OR previously queued
        # but never submitted) — so re-clicking Approve kicks a stuck item. The Approved tab
        # shows "Submitting…" then the final status via live refresh.
        submitting = False
        if _autosubmit_enabled() and _approval_pending(name, url):
            dry = _autosubmit_dry_run()
            log(f"auto-submit: starting background submission for {name or url} (dry={dry})")
            threading.Thread(
                target=submitter.run_approval_submission,
                args=(name, url),
                kwargs={"dry_run": dry},
                name=f"submit-{name[:20]}",
                daemon=True,
            ).start()
            submitting = True
        elif not _autosubmit_enabled():
            log("auto-submit: AUTO_SUBMIT_ENABLED not set — approval queued only (not submitting).")

        log(f"approve: {name or url} (new={added}, submitting={submitting})")
        self._send_json(200, {"ok": True, "added": added, "name": name or url, "submitting": submitting})

    def _handle_dismiss(self) -> None:
        payload = self._read_json() or {}
        name = (payload.get("name") or "").strip()
        url = (payload.get("url") or "").strip()
        phrase = name or url
        if not phrase:
            self._send_json(400, {"ok": False, "error": "name or url required"})
            return
        try:
            added = _add_runtime_exclusion(phrase)
            # Rebuild proposals so the dismissed suggestion disappears NOW and stays
            # gone on reload (the day's shortlist file still contains it).
            dashboard.refresh_proposals()
        except Exception as e:
            log(f"dismiss failed: {e}")
            self._send_json(500, {"ok": False, "error": "could not dismiss"})
            return
        log(f"dismiss: {phrase} (new={added})")
        self._send_json(200, {"ok": True, "added": added, "name": phrase})

    def end_headers(self):
        # No-cache so a deploy's new index.html/app.js/styles are always picked up — browsers
        # otherwise keep stale JS and the dashboard looks unchanged after an update.
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):  # quieter access log, routed through our logger
        log("%s - %s" % (self.address_string(), fmt % args))


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
        send_alert(f"propose run FAILED in orchestrator: {e}")
    try:
        dashboard.generate_all()
    except Exception as e:
        log(f"dashboard.generate_all() failed: {e}")
        send_alert(f"dashboard generation FAILED: {e}")
    # Live-link sweep: flips pending submissions to Live when the backlink appears,
    # flags lost links. Cadence/guards live in livecheck itself.
    try:
        from backlink_agent import livecheck
        summary = livecheck.run_daily_checks()
        log(f"livecheck: {summary}")
        if summary.get("changed"):
            dashboard.refresh_submissions()
        # Always refresh the Live Links data — last_checked/attempts move every sweep
        # even when no status was written.
        dashboard.refresh_livecheck()
    except Exception as e:
        log(f"livecheck failed: {e}")
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

    log(f"auto-submit on Approve: {'ENABLED' if _autosubmit_enabled() else 'disabled'} "
        f"(AUTO_SUBMIT_ENABLED={os.getenv('AUTO_SUBMIT_ENABLED', '')!r}, dry_run={_autosubmit_dry_run()})")

    threading.Thread(target=scheduler_loop, name="scheduler", daemon=True).start()

    handler = partial(DashboardHandler, directory=str(DOCS_DIR))
    httpd = HTTPServer(("0.0.0.0", PORT), handler)
    log(f"Serving dashboard from docs/ on 0.0.0.0:{PORT} (daily propose at {RUN_HOUR:02d}:00 server time)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("Shutting down.")
        httpd.server_close()


if __name__ == "__main__":
    main()
