#!/usr/bin/env python3
"""
Submission dispatcher + status driver.

One place that turns an approved target into an actual submission:
  - picks the per-site script (Eat Sleep Golf, Tinylaunch) when one matches,
    otherwise the generic best-effort submitter (submit_generic.py);
  - drives the approval's status on the dashboard: submitting → submitted /
    needs_manual_submit / error, refreshing the committed JSON as it goes.

Used by serve.py (immediate, on Approve) and orchestrator.process_approvals
(the scheduled backstop sweep). Never raises to the caller.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))  # submit_*.py + steel_utils live at repo root

try:
    from . import approvals as approvals_mod
    from . import dashboard as dashboard_mod
except ImportError:  # direct/script context
    import approvals as approvals_mod  # type: ignore
    import dashboard as dashboard_mod  # type: ignore

# Per-site scripts that handle a specific directory. Matched as substrings against
# the approval's name + url (lower-cased). Everything else → generic submitter.
SCRIPTED_SITES = {
    "submit_eatsleepgolf": ("eat sleep golf", "eatsleepgolf"),
    "submit_tinylaunch": ("tinylaunch",),
}

# Domains the scripted sites cover — always auto-submit-allowed.
_SCRIPTED_DOMAINS = {"eatsleepgolf.net", "tinylaunch.com"}
_ALLOWLIST_FILE = BASE_DIR / "auto_submit_allowlist.json"


def _domain(url: str) -> str:
    try:
        host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    except Exception:
        host = ""
    return host[4:] if host.startswith("www.") else host


def _load_allowlist() -> set:
    """Domains the agent may auto-submit to (scripted domains always included).
    Read fresh each call so edits to auto_submit_allowlist.json take effect without restart."""
    allow = set(_SCRIPTED_DOMAINS)
    try:
        if _ALLOWLIST_FILE.exists():
            data = json.loads(_ALLOWLIST_FILE.read_text(encoding="utf-8"))
            for d in (data.get("domains", []) if isinstance(data, dict) else data):
                d = _domain(str(d)) or str(d).strip().lower()
                if d:
                    allow.add(d)
    except Exception as e:
        print(f"[submitter] could not read allowlist: {e}")
    return allow


def _scripted_module(name: str, url: str):
    hay = f"{name} {url}".lower()
    for module_name, needles in SCRIPTED_SITES.items():
        if any(n in hay for n in needles):
            return module_name
    return None


# Serialize submissions: one Steel session at a time (concurrency = 1), and cap how many
# auto-submits run per day so link velocity stays steady/low (a penalty-safety control).
_SUBMIT_LOCK = threading.Lock()


def _daily_cap() -> int:
    import os
    try:
        return max(0, int(os.getenv("MAX_AUTOSUBMITS_PER_DAY", "3")))
    except ValueError:
        return 3


def _submitted_today() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    n = 0
    for a in approvals_mod.load_approvals():
        ts = a.get("updated_at") or a.get("approved_at") or ""
        if a.get("status") in ("submitted", "submitting") and ts.startswith(today):
            n += 1
    return n


def dispatch(name: str, url: str, dry_run: bool = False) -> tuple[str, str]:
    """Run the submission for one target. Returns (status, detail) where status is a
    valid approval status: submitted | needs_manual_submit | error | approved (dry).

    Safety gate: only scripted sites or domains on the auto-submit allowlist are submitted
    automatically; anything else is flagged needs_manual_submit (protects the domain from
    low-quality/automated link-spam penalties)."""
    scripted = _scripted_module(name, url)
    if not scripted:
        dom = _domain(url)
        if not dom or dom not in _load_allowlist():
            return ("needs_manual_submit",
                    "Not on auto-submit allowlist — review & submit manually "
                    "(add the domain to auto_submit_allowlist.json to allow).")
    try:
        if scripted:
            if dry_run:
                return "approved", f"dry run — would auto-submit via {scripted}"
            mod = importlib.import_module(scripted)
            outcome = mod.submit()  # "submitted" | "aborted"
            label = scripted
        else:
            if not url:
                return "needs_manual_submit", "No URL to submit to."
            mod = importlib.import_module("submit_generic")
            outcome = mod.submit(name, url, dry_run=dry_run)  # "submitted"|"aborted"|"dry"
            label = "generic submitter"
    except Exception as e:
        return "error", f"{type(e).__name__}: {e}"[:200]

    if outcome == "submitted":
        return "submitted", f"Submitted via {label}."
    if outcome == "dry":
        return "approved", "dry run — filled the form, did not submit."
    # aborted / anything else → couldn't complete automatically.
    return "needs_manual_submit", f"{label} could not complete this form — submit manually."


def run_approval_submission(name: str, url: str, dry_run: bool = False) -> tuple[str, str]:
    """Drive one approval end-to-end with live dashboard status. Safe to call from a
    background thread; never raises. Serializes submissions (concurrency=1) and respects
    the daily auto-submit cap (link-velocity safety)."""
    # Daily velocity cap — count is unaffected by dry runs (those don't reach "submitted").
    cap = _daily_cap()
    if not dry_run and _submitted_today() >= cap:
        msg = f"daily auto-submit cap ({cap}) reached — left queued for next run."
        print(f"[submitter] {name}: {msg}")
        return "approved", msg

    # One Steel submission at a time.
    with _SUBMIT_LOCK:
        try:
            approvals_mod.set_status(name, url, "submitting", "Agent submitting…")
            dashboard_mod.refresh_approvals()
        except Exception as e:
            print(f"[submitter] could not mark submitting for {name!r}: {e}")

        status, detail = dispatch(name, url, dry_run=dry_run)

    try:
        approvals_mod.set_status(name, url, status, detail)
        dashboard_mod.refresh_approvals()
        dashboard_mod.refresh_submissions()
    except Exception as e:
        print(f"[submitter] could not record result for {name!r}: {e}")
    if status == "error":
        try:
            from steel_utils import send_alert
            send_alert(f"submission error for {name}: {detail}")
        except Exception:
            pass
    print(f"[submitter] {name} → {status} ({detail})")
    return status, detail
