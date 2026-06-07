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
from datetime import datetime
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
    return _merge_submission_log(submissions)


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
        existing = by_key.get(_sub_key(name, url)) or by_name.get(_sub_key(name, ""))
        if existing:
            existing["status"] = status
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
                "status": status,
                "url": url or None,
                "notes": notes,
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

    _write("submissions.json", submissions)
    _write("proposals.json", proposals)
    _write("runs.json", runs)
    _write("sessions.json", sessions)
    _write("approvals.json", approvals)

    counts = {
        "submissions": len(submissions),
        "proposals": len(proposals.get("items", [])),
        "runs": len(runs),
        "sessions": len(sessions),
        "approvals": len(approvals),
    }
    latest_run_date = proposals.get("date") or (runs[-1]["date"] if runs else None)
    _write("meta.json", build_meta(counts, latest_run_date))
    print(f"Done. counts={counts}")


if __name__ == "__main__":
    generate_all()
