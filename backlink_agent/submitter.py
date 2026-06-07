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


def dispatch(name: str, url: str, dry_run: bool = False) -> tuple[str, str]:
    """Run the submission for one target. Returns (status, detail) where status is a
    valid approval status: submitted | needs_manual_submit | error | approved (dry)."""
    scripted = _scripted_module(name, url)
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
    background thread; never raises."""
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
    print(f"[submitter] {name} → {status} ({detail})")
    return status, detail
