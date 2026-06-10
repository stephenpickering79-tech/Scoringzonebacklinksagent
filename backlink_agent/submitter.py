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
import sys
import threading
from datetime import datetime
from pathlib import Path

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

    Approval = authorization: the human clicking Approve on the dashboard IS the
    review, so every approved target is attempted (scripted module if one exists,
    generic submitter otherwise). Safety now rests on that human approval plus the
    MAX_AUTOSUBMITS_PER_DAY velocity cap and the dry-run default. Targets only come
    back needs_manual_submit when the form genuinely can't be completed, with the
    specific reason recorded."""
    scripted = _scripted_module(name, url)
    try:
        if scripted:
            if dry_run:
                return "approved", f"dry run — would auto-submit via {scripted}"
            mod = importlib.import_module(scripted)
            raw = mod.submit()
            label = scripted
        else:
            if not url:
                return "needs_manual_submit", "No submission URL — outreach-only target, handle manually."
            mod = importlib.import_module("submit_generic")
            raw = mod.submit(name, url, dry_run=dry_run)
            label = "generic submitter"
    except Exception as e:
        return "error", f"{type(e).__name__}: {e}"[:200]

    # Submitted/aborted paths return dicts (confirmation evidence / abort reason);
    # "dry" — and "aborted" from the older scripted modules — remain plain strings.
    confirmation, evidence, reason = "", "", ""
    if isinstance(raw, dict):
        outcome = raw.get("outcome", "")
        confirmation = raw.get("confirmation", "")
        evidence = raw.get("evidence", "")
        reason = raw.get("reason", "")
    else:
        outcome = raw

    if outcome == "submitted":
        if confirmation == "confirmed":
            suffix = f" Confirmation detected: {evidence[:120]}"
        elif confirmation == "error":
            suffix = f" WARNING — page showed an error: {evidence[:120]} Review the screenshots."
        else:
            suffix = " No on-page confirmation detected (may still be fine — check the result screenshot)."
        return "submitted", f"Submitted via {label}.{suffix}"
    if outcome == "dry":
        return "approved", "dry run — filled the form, did not submit."
    # aborted / anything else → couldn't complete automatically.
    if reason:
        return "needs_manual_submit", f"{label}: {reason}"[:300]
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
