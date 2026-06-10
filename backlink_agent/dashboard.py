#!/usr/bin/env python3
"""
Dashboard data generator for the Scoring Zone backlink agent.

Builds the committed JSON the static dashboard reads (served via GitHub Pages
from /docs on main). It READS from the gitignored runtime dirs (data/, logs/)
and the human-maintained directory-submissions.md, and WRITES normalized JSON
into docs/data/ (which IS committed — *.json is not gitignored).

Run directly (no package install; backlink_agent/ has no __init__.py):
    python3 backlink_agent/dashboard.py

Outputs (docs/data/):
    submissions.json  parsed from directory-submissions.md (with manual DR)
    proposals.json    latest data/shortlist_*.json, normalized
    runs.json         history built from all data/summary_*.json
    sessions.json     Steel sessions from data/sessions.jsonl (redacted)
    meta.json         {generated_at, latest_run_date, counts}

directory-submissions.md is the single source of truth for submissions — this
script never writes it. Edit the markdown; re-run to refresh the dashboard.
"""

from __future__ import annotations

import glob
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
DOCS_DATA = BASE_DIR / "docs" / "data"
MD_PATH = BASE_DIR / "directory-submissions.md"
SESSIONS_LOG = DATA_DIR / "sessions.jsonl"
APPROVALS_FILE = DATA_DIR / "approvals.json"
SUBMISSION_LOG = DATA_DIR / "submission_log.json"

# Number of session-id characters to keep in the public (committed) JSON.
SESSION_ID_PREFIX = 8


# ---------------------------------------------------------------------------
# Generic GitHub-flavoured-markdown table parser
# ---------------------------------------------------------------------------

def _split_row(line: str) -> list[str]:
    """Split a '| a | b |' table row into stripped cell strings."""
    # Drop the leading/trailing pipe, then split. This keeps interior empty
    # cells while discarding the artefacts produced by the outer pipes.
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _is_separator(line: str) -> bool:
    """True for a markdown header separator like |---|:--:|---|."""
    return bool(re.match(r"^\s*\|[\s:\-|]+\|\s*$", line))


def parse_markdown_tables(md_text: str) -> list[dict]:
    """Return every GFM table as {headers: [...], rows: [ {header: cell} ]}.

    A table starts where a '| ... |' line is immediately followed by a
    separator row. Ragged rows are padded/truncated to the header width so a
    hand-edited table never throws.
    """
    lines = md_text.splitlines()
    tables: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        is_row = line.strip().startswith("|")
        has_sep = i + 1 < len(lines) and _is_separator(lines[i + 1])
        if is_row and has_sep:
            headers = _split_row(line)
            width = len(headers)
            rows: list[dict] = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                if _is_separator(lines[j]):
                    j += 1
                    continue
                cells = _split_row(lines[j])
                # Normalize ragged rows to the header width.
                if len(cells) < width:
                    cells += [""] * (width - len(cells))
                elif len(cells) > width:
                    cells = cells[:width]
                rows.append(dict(zip(headers, cells)))
                j += 1
            tables.append({"headers": headers, "rows": rows})
            i = j
        else:
            i += 1
    return tables


def _find_table(tables: list[dict], *required_headers: str) -> dict | None:
    """First table whose header set contains all of required_headers."""
    for t in tables:
        hs = set(t["headers"])
        if all(h in hs for h in required_headers):
            return t
    return None


def _parse_dr(cell: str):
    """Return (int|None, raw_label). '75' -> (75,'75'); '—' -> (None,'—')."""
    label = (cell or "").strip()
    m = re.search(r"\d+", label)
    return (int(m.group(0)) if m else None, label)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_submissions() -> list[dict]:
    """Parse the consolidated-log master table + merge live URLs by name."""
    if not MD_PATH.exists():
        return []
    tables = parse_markdown_tables(MD_PATH.read_text(encoding="utf-8"))

    master = _find_table(tables, "Directory / Target", "DR (approx)")
    live = _find_table(tables, "Directory", "URL")

    # name(lower) -> first non-"—" URL from the Live/Submitted table.
    url_by_name: dict[str, str] = {}
    if live:
        for row in live["rows"]:
            name = (row.get("Directory") or "").strip()
            url = (row.get("URL") or "").strip()
            if name and url and url != "—" and name.lower() not in url_by_name:
                url_by_name[name.lower()] = url

    submissions: list[dict] = []
    if master:
        for row in master["rows"]:
            name = (row.get("Directory / Target") or "").strip()
            if not name or name == "—" and not row.get("Date"):
                # Tolerate the "—" numbered placeholder rows but still include
                # them if they carry a real target name; skip truly empty ones.
                pass
            if not name:
                continue
            dr, dr_label = _parse_dr(row.get("DR (approx)", ""))
            submissions.append({
                "name": name,
                "date": (row.get("Date") or "").strip(),
                "dr": dr,
                "dr_label": dr_label,
                "status": (row.get("Status") or "").strip(),
                "url": url_by_name.get(name.lower()),
                "notes": (row.get("Notes") or "").strip(),
            })

    # Overlay real submittals recorded by the agent / submit_*.py (data/submission_log.json):
    # update a matching markdown row's status+date, else append a new row. Lets a submission
    # show on the Submissions tab even though the agent can't edit the committed markdown.
    submissions = _merge_submission_log(submissions)
    # Also fold in everything you've approved / the agent has acted on (data/approvals.json) so the
    # Submissions tab is the unified record: curated history + approved/submitting/needs-manual/error.
    return _merge_approvals(submissions)


def _sub_key(name: str, url: str) -> str:
    """Match key shared with steel_utils.record_submission — URL if present, else name."""
    u = (url or "").strip().lower().replace("https://", "").replace("http://", "")
    u = u.lstrip("www.").rstrip("/")
    if u:
        return u
    return " ".join((name or "").strip().lower().split())


def _merge_submission_log(submissions: list[dict]) -> list[dict]:
    if not SUBMISSION_LOG.exists():
        return submissions
    try:
        log = json.loads(SUBMISSION_LOG.read_text(encoding="utf-8"))
    except Exception:
        return submissions
    if not isinstance(log, list):
        return submissions

    by_key = {_sub_key(s.get("name", ""), s.get("url", "")): s for s in submissions}
    # Markdown rows often lack a URL but the log has one — also index by name so they still match.
    by_name = {_sub_key(s.get("name", ""), ""): s for s in submissions}
    for e in log:
        if not isinstance(e, dict):
            continue
        name, url = e.get("name", ""), e.get("url", "")
        status = e.get("status") or "Submitted"
        date = e.get("date") or ""
        notes = e.get("notes") or "Auto-submitted by agent"
        listing_url = e.get("listing_url") or ""
        existing = by_key.get(_sub_key(name, url)) or by_name.get(_sub_key(name, ""))
        if existing:
            existing["status"] = status
            if date:
                existing["date"] = date
            if listing_url:
                # The live-checker found the actual listing page — link the row there
                # and surface its dofollow/nofollow note.
                existing["url"] = listing_url
                if e.get("notes"):
                    existing["notes"] = e["notes"]
            elif not existing.get("url") and url:
                existing["url"] = url
        else:
            submissions.append({
                "name": name or url,
                "date": date,
                "dr": None,
                "dr_label": "—",
                "status": status,
                "url": listing_url or url or None,
                "notes": notes,
            })
    return submissions


# Approval status → Submissions-tab label.
_APPROVAL_STATUS_LABEL = {
    "approved": "Approved — to submit",
    "submitting": "Submitting…",
    "submitted": "Submitted (auto)",
    "needs_manual_submit": "Needs manual submit",
    "error": "Submit error",
}
# Existing statuses we may overwrite with an approval label (weak / placeholder ones only).
_WEAK_STATUS_BITS = ("", "not submitted", "ready", "prepared", "to submit")


def _merge_approvals(submissions: list[dict]) -> list[dict]:
    """Fold the Approve queue (data/approvals.json) into the Submissions list so approved /
    submitting / needs-manual / errored targets appear there too — without clobbering a real
    'Live ✓'/'Submitted' history row."""
    if not APPROVALS_FILE.exists():
        return submissions
    try:
        approvals = json.loads(APPROVALS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return submissions
    if not isinstance(approvals, list):
        return submissions

    by_key = {_sub_key(s.get("name", ""), s.get("url", "")): s for s in submissions}
    by_name = {_sub_key(s.get("name", ""), ""): s for s in submissions}
    for a in approvals:
        if not isinstance(a, dict):
            continue
        name, url = a.get("name", ""), a.get("url", "")
        label = _APPROVAL_STATUS_LABEL.get(a.get("status", ""), a.get("status") or "Approved")
        date = (a.get("updated_at") or a.get("approved_at") or "")[:10]
        existing = by_key.get(_sub_key(name, url)) or by_name.get(_sub_key(name, ""))
        if existing:
            cur = (existing.get("status") or "").strip().lower()
            if any(cur == w or cur.startswith(w) for w in _WEAK_STATUS_BITS if w) or not cur:
                existing["status"] = label
                if date:
                    existing["date"] = date
            if not existing.get("url") and url:
                existing["url"] = url
        else:
            submissions.append({
                "name": name or url,
                "date": date,
                "dr": None,
                "dr_label": "—",
                "status": label,
                "url": url or None,
                "notes": a.get("detail", ""),
            })
    return submissions


def _latest_dated_file(pattern: str) -> Path | None:
    files = sorted(glob.glob(str(DATA_DIR / pattern)))
    return Path(files[-1]) if files else None


_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def build_proposals() -> dict:
    """Latest shortlist_*.json normalized for the Output view."""
    latest = _latest_dated_file("shortlist_*.json")
    if not latest:
        return {"date": None, "items": []}
    m = _DATE_RE.search(latest.name)
    date = m.group(1) if m else None
    try:
        raw = json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        return {"date": date, "items": []}
    items = []
    for c in raw:
        items.append({
            "name": c.get("name", ""),
            "url": c.get("url", ""),
            "score": c.get("score", 0),
            "topical_relevance": c.get("topical_relevance", ""),
            "justification": c.get("justification", ""),
            "recommended_action": c.get("recommended_action", ""),
            "source": c.get("source", ""),
            "submission_method": c.get("submission_method", ""),
            "spam_risk": c.get("spam_risk", ""),
            "quality_tier": c.get("quality_tier", ""),
            "notes": c.get("notes", ""),
        })
    # Apply the exclusion lists (same logic research uses) so a Dismissed suggestion
    # disappears from the page immediately — the day's shortlist file still contains
    # it, and without this filter it would resurface on every rebuild until the next
    # research run. Lazy import: research is heavier and must never block the build.
    try:
        try:
            from . import research
        except ImportError:
            import research  # type: ignore
        excluded = research.load_excluded()
        if excluded:
            items = [it for it in items if not research._is_excluded(it, excluded)]
    except Exception as e:
        print(f"  build_proposals: exclusion filter skipped: {e}")
    return {"date": date, "items": items}


def build_runs() -> list[dict]:
    """History from every data/summary_*.json, sorted ascending by date."""
    runs = []
    for path in sorted(glob.glob(str(DATA_DIR / "summary_*.json"))):
        try:
            s = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            continue
        runs.append({
            "date": s.get("date"),
            "mode": s.get("mode"),
            "candidates_found": s.get("candidates_found", 0),
            "shortlist_size": s.get("shortlist_size", 0),
        })
    runs.sort(key=lambda r: (r.get("date") or ""))
    return runs


def build_sessions() -> list[dict]:
    """Steel sessions from data/sessions.jsonl, collapsed by id + redacted.

    The committed JSON drops viewer_url and truncates session_id (the site is
    public via Pages). Full detail stays only in the gitignored .jsonl.
    """
    if not SESSIONS_LOG.exists():
        return []
    by_id: dict[str, dict] = {}
    for line in SESSIONS_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        sid = rec.get("session_id") or rec.get("timestamp") or str(len(by_id))
        existing = by_id.get(sid, {})
        existing.update({k: v for k, v in rec.items() if v not in (None, "", [])})
        by_id[sid] = existing

    sessions = []
    for sid, rec in by_id.items():
        full_id = rec.get("session_id") or ""
        sessions.append({
            "session_id_short": (full_id[:SESSION_ID_PREFIX] + "…") if full_id else "",
            "target": rec.get("target"),
            "mode": rec.get("mode"),
            "timestamp": rec.get("timestamp"),
            "screenshots": rec.get("screenshots", []),
            "screenshot_count": len(rec.get("screenshots", []) or []),
            "outcome": rec.get("outcome", "started"),
        })
    sessions.sort(key=lambda s: (s.get("timestamp") or ""), reverse=True)
    return sessions


def build_approvals() -> list[dict]:
    """The Approve queue (data/approvals.json) normalized for the dashboard panel.

    Read directly (no secrets in the queue) so this works whether dashboard.py is
    run standalone or imported. Newest first.
    """
    if not APPROVALS_FILE.exists():
        return []
    try:
        raw = json.loads(APPROVALS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out = []
    for a in raw:
        if not isinstance(a, dict):
            continue
        out.append({
            "name": a.get("name", ""),
            "url": a.get("url", ""),
            "status": a.get("status", "approved"),
            "approved_at": a.get("approved_at"),
            "updated_at": a.get("updated_at"),
            "detail": a.get("detail", ""),
        })
    out.sort(key=lambda x: (x.get("approved_at") or ""), reverse=True)
    return out


LIVECHECK_STATE = DATA_DIR / "livecheck_state.json"

# Status bucketing — mirrors shared.js statusClass() / livecheck._status_bucket(),
# except lost/no-link/error get their own "attention" bucket here (the JS renders
# those red as "blocked"). Do NOT import livecheck at module level: it imports
# dashboard, so a top-level back-import is circular. Keep all three in sync.
def _status_bucket(status: str) -> str:
    s = (status or "").strip().lower()
    if "live" in s:
        return "live"
    if "lost" in s or "no link" in s or "error" in s:
        return "attention"
    if "block" in s or "reject" in s:
        return "blocked"
    if any(b in s for b in ("submit", "pending", "applied", "ready", "progress")):
        return "pending"
    return "other"


# Pending-looking statuses that aren't awaiting verification (queue states / not
# yet submitted) — mirror of livecheck._UNTRACKED_BITS.
_LC_UNTRACKED_BITS = ("not submitted", "to submit", "needs manual", "submitting",
                      "submit error", "ready", "prepared", "no link found")


def _load_livecheck_state() -> dict:
    try:
        if LIVECHECK_STATE.exists():
            data = json.loads(LIVECHECK_STATE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"  could not read {LIVECHECK_STATE.name}: {e}")
    return {}


def _plus_days(iso_ts: str, days: int) -> str | None:
    try:
        return (datetime.fromisoformat(iso_ts) + timedelta(days=days)).date().isoformat()
    except Exception:
        return None


def build_livecheck(submissions: list[dict] | None = None) -> dict:
    """The Live Links page data: verification state (data/livecheck_state.json)
    merged with the Submissions view, with all next-check math done server-side.

    Degrades gracefully when the state file is absent (fresh deploy / local run):
    live rows still come from submissions, watching rows show "due now".
    """
    rows = submissions if submissions is not None else build_submissions()
    state = _load_livecheck_state()

    try:  # cadence constants from livecheck; lazy import (circular at module level)
        try:
            from . import livecheck as _lc
        except ImportError:
            import livecheck as _lc  # type: ignore
        pend_days, live_days = _lc.RECHECK_PENDING_DAYS, _lc.RECHECK_LIVE_DAYS
    except Exception:
        pend_days, live_days = 3, 7

    today = datetime.now().date().isoformat()
    live: list[dict] = []
    watching: list[dict] = []
    attention: list[dict] = []
    seen_attention: set = set()

    for row in rows:
        name = row.get("name") or ""
        url = row.get("url") or ""
        status = row.get("status") or ""
        st = state.get(_sub_key(name, ""), {})
        bucket = _status_bucket(status)

        if bucket == "live":
            dofollow = st.get("dofollow")
            if dofollow is True:
                rel = "dofollow"
            elif dofollow is False:
                rel = "nofollow"
            elif st.get("found_at"):
                rel = "redirect"   # listing verified live, but it links out via a redirect
            else:
                rel = "unknown"    # human-confirmed live; livecheck never saw it
            listing_url = st.get("listing_url") or url or None
            tracked = bool(st)
            next_due = None
            if st.get("listing_url") and st.get("last_checked"):
                next_due = _plus_days(st["last_checked"], live_days)
            live.append({
                "name": name,
                "listing_url": listing_url,
                "rel": rel,
                "found_at": st.get("found_at"),
                "last_checked": st.get("last_checked"),
                "next_check_due": next_due,
                "status_label": status,
                "tracked": tracked,
                "dr": row.get("dr"),
                "date": row.get("date") or "",
            })
        elif bucket == "attention":
            key = _sub_key(name, "")
            seen_attention.add(key)
            attention.append({
                "name": name,
                "url": url or None,
                "listing_url": st.get("listing_url"),
                "problem": "lost" if "lost" in status.lower() else "gave_up",
                "status_label": status,
                "last_checked": st.get("last_checked"),
                "attempts": st.get("attempts", 0),
            })
        elif bucket == "pending":
            s_low = status.strip().lower()
            if any(b in s_low for b in _LC_UNTRACKED_BITS):
                continue   # queue state, not awaiting verification
            if st.get("status") == "gave_up":
                continue   # surfaced via its log status in attention instead
            last = st.get("last_checked")
            next_due = _plus_days(last, pend_days) if last else today
            submitted = (st.get("submitted_date") or row.get("date")
                         or st.get("first_tracked") or "")
            days_waiting = None
            try:
                days_waiting = (datetime.now().date()
                                - datetime.fromisoformat(str(submitted)[:10]).date()).days
            except Exception:
                pass
            watching.append({
                "name": name,
                "url": url or None,
                "status_label": status,
                "submitted_date": str(submitted)[:10] or None,
                "last_checked": last,
                "attempts": st.get("attempts", 0),
                "next_check_due": next_due,
                "days_waiting": days_waiting,
            })

    live.sort(key=lambda x: x.get("found_at") or "", reverse=True)
    watching.sort(key=lambda x: x.get("next_check_due") or "")
    attention.sort(key=lambda x: x.get("last_checked") or "", reverse=True)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {"live": len(live), "watching": len(watching), "attention": len(attention)},
        "live": live,
        "watching": watching,
        "attention": attention,
    }


def refresh_livecheck() -> None:
    """Write only docs/data/livecheck.json (after a check-live / daily sweep /
    mark-submitted) so the Live Links tab reflects it without a full regen."""
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    _write("livecheck.json", build_livecheck())


def refresh_proposals() -> None:
    """Write only docs/data/proposals.json. Called by serve.py after a Dismiss so
    the excluded suggestion disappears immediately (and stays gone on reload)."""
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    _write("proposals.json", build_proposals())


def refresh_approvals() -> None:
    """Write only docs/data/approvals.json. Called by serve.py right after an
    Approve click so the Approved panel reflects it without a full regen."""
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    _write("approvals.json", build_approvals())


def refresh_submissions() -> None:
    """Write only docs/data/submissions.json. Called after a submittal so the
    Submissions tab reflects the new status without a full regen."""
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    _write("submissions.json", build_submissions())


def build_meta(counts: dict, latest_run_date) -> dict:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest_run_date": latest_run_date,
        "counts": counts,
    }


def _write(name: str, obj) -> None:
    path = DOCS_DATA / name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"  wrote {path.relative_to(BASE_DIR)}")


def generate_all() -> None:
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    print("Generating dashboard data into docs/data/ ...")

    # Each builder is isolated: one bad input never blocks the others.
    try:
        submissions = build_submissions()
    except Exception as e:
        print(f"  build_submissions failed: {e}")
        submissions = []
    try:
        proposals = build_proposals()
    except Exception as e:
        print(f"  build_proposals failed: {e}")
        proposals = {"date": None, "items": []}
    try:
        runs = build_runs()
    except Exception as e:
        print(f"  build_runs failed: {e}")
        runs = []
    try:
        sessions = build_sessions()
    except Exception as e:
        print(f"  build_sessions failed: {e}")
        sessions = []
    try:
        approvals = build_approvals()
    except Exception as e:
        print(f"  build_approvals failed: {e}")
        approvals = []
    try:
        livecheck = build_livecheck(submissions)
    except Exception as e:
        print(f"  build_livecheck failed: {e}")
        livecheck = {"generated_at": None,
                     "summary": {"live": 0, "watching": 0, "attention": 0},
                     "live": [], "watching": [], "attention": []}

    _write("submissions.json", submissions)
    _write("proposals.json", proposals)
    _write("runs.json", runs)
    _write("sessions.json", sessions)
    _write("approvals.json", approvals)
    _write("livecheck.json", livecheck)

    counts = {
        "submissions": len(submissions),
        "proposals": len(proposals.get("items", [])),
        "runs": len(runs),
        "sessions": len(sessions),
        "approvals": len(approvals),
        "livecheck": livecheck.get("summary", {}).get("live", 0),
    }
    latest_run_date = proposals.get("date") or (runs[-1]["date"] if runs else None)
    _write("meta.json", build_meta(counts, latest_run_date))
    print(f"Done. counts={counts}")


if __name__ == "__main__":
    generate_all()
