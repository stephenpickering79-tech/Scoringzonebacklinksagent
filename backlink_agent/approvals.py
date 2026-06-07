#!/usr/bin/env python3
"""
Approval queue store for the Scoring Zone backlink agent.

A human clicks "Approve" on a proposal in the dashboard; serve.py appends the
target here. The orchestrator then processes the queue on its own schedule
(manual phase, then auto for scripted sites) — the public click never directly
fires a browser submission.

The queue lives on the Railway volume (data/approvals.json, gitignored) so it
survives redeploys. dashboard.py mirrors a redacted copy to docs/data so the
"Approved" panel can render. Each entry:

    {"name", "url", "approved_at", "status", "updated_at"?, "detail"?}

status ∈ {"approved", "submitted", "needs_manual_submit", "error"}.

De-dupe matches the exclusion logic in research.py: a candidate is "the same"
if its normalized URL matches, else its normalized name.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

# Reuse the same normalizers the rest of the pipeline uses so "already queued"
# means the same thing here as de-dupe/exclusion do elsewhere.
try:
    from .research import _norm_name, _norm_url
except ImportError:  # direct script / non-package run
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from research import _norm_name, _norm_url  # type: ignore

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
APPROVALS_FILE = DATA_DIR / "approvals.json"

VALID_STATUSES = {"approved", "submitting", "submitted", "needs_manual_submit", "error"}


def _key(name: str, url: str) -> str:
    """Identity for an approval — URL if we have one, else the name."""
    return _norm_url(url or "") or _norm_name(name or "")


def load_approvals() -> list[dict]:
    """Read the queue. Never raises — a missing/corrupt file yields []."""
    try:
        if APPROVALS_FILE.exists():
            data = json.loads(APPROVALS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [a for a in data if isinstance(a, dict)]
    except Exception as e:
        print(f"[approvals] could not read {APPROVALS_FILE.name}: {e}")
    return []


def save_approvals(approvals: list[dict]) -> None:
    """Write the queue atomically (temp file + replace) so a concurrent reader
    on another thread never sees a half-written file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".approvals_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(approvals, f, indent=2, ensure_ascii=False)
        os.replace(tmp, APPROVALS_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_approval(name: str, url: str = "") -> bool:
    """Queue a target for submission. Idempotent.

    Returns True if a new entry was added, False if it was already queued.
    """
    name = (name or "").strip()
    url = (url or "").strip()
    if not name and not url:
        raise ValueError("approval needs a name or a url")

    approvals = load_approvals()
    k = _key(name, url)
    if any(_key(a.get("name", ""), a.get("url", "")) == k for a in approvals):
        return False

    approvals.append({
        "name": name or url,
        "url": url,
        "approved_at": datetime.now().isoformat(timespec="seconds"),
        "status": "approved",
    })
    save_approvals(approvals)
    return True


def set_status(name: str, url: str, status: str, detail: str = "") -> bool:
    """Update the status of a queued approval (called by the orchestrator after
    it processes the queue). Returns True if a matching entry was updated."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    approvals = load_approvals()
    k = _key(name, url)
    changed = False
    for a in approvals:
        if _key(a.get("name", ""), a.get("url", "")) == k:
            a["status"] = status
            a["updated_at"] = datetime.now().isoformat(timespec="seconds")
            if detail:
                a["detail"] = detail
            changed = True
    if changed:
        save_approvals(approvals)
    return changed
