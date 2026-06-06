#!/usr/bin/env python3
"""
Research Agent — builds the daily candidate pool for the backlink agent.

Two sources, combined and de-duplicated:
  1. SEED (local, network-free): the user's curated target lists already in the repo
     (directory-submissions.md + backlink-targets.md). These are read straight off disk,
     so the candidate pool is NEVER empty even if Steel/the network is unavailable.
     Only "open" targets are seeded (not already live/blocked/rejected/done).
  2. DISCOVERY (Steel, additive): new opportunities scraped from golf/SaaS roundup pages
     using the robust extractor in discover_targets.py. May legitimately find nothing on a
     given day — that's fine, the seed keeps the pool non-empty.

A single "research" session is recorded per run (steel_utils.record_session) so the dashboard
Sessions tab reflects that the agent ran.

See backlink_automation_plan.md for the wider design.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Make repo-root modules (steel_utils, discover_targets) importable when run as a script.
sys.path.insert(0, str(BASE_DIR))

# Reuse the dashboard's markdown table parser for seeding (same package).
try:
    from . import dashboard as dash
except ImportError:  # direct script / namespace-package run
    import dashboard as dash  # type: ignore

# Session recorder (repo root).
try:
    from ..steel_utils import record_session
except ImportError:
    from steel_utils import record_session  # type: ignore


SUBMISSIONS_MD = BASE_DIR / "directory-submissions.md"
TARGETS_MD = BASE_DIR / "backlink-targets.md"

# A target is "done/closed" (skip) if its status contains any of these. Everything else
# (not submitted, prepared, ready, in progress, pending, applied, submitted, blank) is "open".
CLOSED_STATUS_WORDS = ("live", "rejected", "blocked", "covered", "completed", "complete")

URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')


def _norm_url(url: str) -> str:
    if not url:
        return ""
    try:
        p = urlparse(url.strip())
        host = (p.netloc or "").lower().lstrip("www.")
        path = (p.path or "").rstrip("/").lower()
        return f"{host}{path}"
    except Exception:
        return url.strip().lower().rstrip("/")


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _is_open(status: str) -> bool:
    s = (status or "").lower()
    return not any(w in s for w in CLOSED_STATUS_WORDS)


def _priority_score(priority: str) -> int:
    """Map a prioritized-table 'Priority' cell to a seed score (higher = top of list)."""
    m = re.search(r"\d+", priority or "")
    if not m:
        return 85
    n = int(m.group(0))
    return max(80, 96 - (n - 1) * 2)  # 1->96, 2->94, 3->92, ...


def _clean(cell: str) -> str:
    return re.sub(r"\s+", " ", (cell or "").strip())


def seed_from_tracker(open_only: bool = True) -> tuple[list[dict], set]:
    """Read targets from the curated lists on disk. Returns (candidates, known_keys).

    With open_only=True (default) only "open" targets are returned (for proposals); with
    open_only=False every tracked target is returned (used as the never-empty hard-floor).
    `known_keys` is the set of normalized names/urls of ALL tracker rows (any status), used to
    exclude already-tracked items from discovery. Network-free; each source guarded + logged.
    """
    seeded: list[dict] = []
    known: set = set()
    seen: set = set()
    closed_names: set = set()

    def _name_col(headers: set):
        for h in ("Directory / Target", "Directory", "Target"):
            if h in headers:
                return h
        return None

    # --- directory-submissions.md (master + Live + prioritized tables) ---
    tables: list = []
    try:
        tables = dash.parse_markdown_tables(SUBMISSIONS_MD.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[research] seed parse of directory-submissions.md failed (non-fatal): {e}")

    # Pre-pass over EVERY table: record all known names/urls (for discovery dedup) and any name
    # marked closed/live in ANY table (so a target that's "live" in one table but stale in another
    # is treated as done — e.g. BetaList).
    for t in tables:
        headers = set(t["headers"])
        nc = _name_col(headers)
        for row in t["rows"]:
            nm = _clean(row.get(nc)) if nc else ""
            url = _clean(row.get("URL")) if "URL" in headers else ""
            if nm:
                known.add(_norm_name(nm))
            if url:
                known.add(_norm_url(url))
            if nc and "Status" in headers and nm and not _is_open(row.get("Status")):
                closed_names.add(_norm_name(nm))

    def add(name, url, status, notes, score, method):
        nkey, ukey = _norm_name(name), _norm_url(url)
        known.add(nkey)
        if ukey:
            known.add(ukey)
        if open_only and (not _is_open(status) or nkey in closed_names):
            return
        dedup_key = ukey or nkey
        if not dedup_key or dedup_key in seen:
            return
        seen.add(dedup_key)
        seeded.append({
            "name": name or url,
            "url": url or "",
            "source": "Your target list",
            "preapproved": True,
            "status": status or "",
            "notes": notes or "",
            "seed_score": score,
            "discovered_at": datetime.now().isoformat(),
            "method": method,
        })

    for t in tables:
        headers = set(t["headers"])
        if {"Directory / Target", "Status"} <= headers:          # master consolidated table
            for row in t["rows"]:
                add(_clean(row.get("Directory / Target")), "", _clean(row.get("Status")),
                    _clean(row.get("Notes")), 85, "tracker:master")
        elif {"Target", "Status"} <= headers and "URL" in headers:  # prioritized targets table
            for row in t["rows"]:
                add(_clean(row.get("Target")), _clean(row.get("URL")), _clean(row.get("Status")),
                    _clean(row.get("Notes / Action") or row.get("Notes")),
                    _priority_score(row.get("Priority")), "tracker:prioritized")

    # --- backlink-targets.md (fallback ONLY: if the main tables yielded nothing, extract bare
    # URLs so the pool is still non-empty). On a normal run this is skipped to avoid noise. ---
    if not seeded:
        try:
            if TARGETS_MD.exists():
                for m in URL_RE.finditer(TARGETS_MD.read_text(encoding="utf-8")):
                    u = m.group(0).rstrip(".,;:!?)")
                    add(urlparse(u).netloc.lstrip("www.") or u, u, "", "From backlink-targets.md", 82,
                        "tracker:backlink-targets")
        except Exception as e:
            print(f"[research] seed parse of backlink-targets.md failed (non-fatal): {e}")

    # Dedupe by normalized name across all tables, keeping the highest-scored entry (so a target
    # in both the prioritized table and the master log appears once, with the better score/notes).
    best: dict = {}
    for c in seeded:
        k = _norm_name(c["name"])
        if k not in best or c["seed_score"] > best[k]["seed_score"]:
            best[k] = c
    seeded = sorted(best.values(), key=lambda c: c["seed_score"], reverse=True)

    return seeded, known


def all_tracker_targets() -> list[dict]:
    """Every tracked target (any status) as candidate dicts — the never-empty hard floor."""
    seeded, _ = seed_from_tracker(open_only=False)
    return seeded


def discover_new(known: set, max_items: int) -> tuple[list[dict], int, int]:
    """Scrape roundup pages for new opportunities. Returns (candidates, pages_scraped, errors).

    Additive only. Skipped cleanly when STEEL_API_KEY is absent (no network) — the seed already
    guarantees a non-empty pool.
    """
    if not os.getenv("STEEL_API_KEY", "").strip():
        print("[research] No STEEL_API_KEY — skipping discovery (seed-only run).")
        return [], 0, 0

    # Reuse the proven, robust extractor + URL list from discover_targets.py (repo root).
    try:
        import discover_targets as discover  # type: ignore
    except Exception as e:
        print(f"[research] could not import discover_targets (skipping discovery): {e}")
        return [], 0, 0

    out: list[dict] = []
    seen: set = set()
    pages_scraped = 0
    errors = 0
    # Cap discoveries so the curated list stays the focus and noise can't flood the dashboard.
    cap = min(max_items, 12)
    for url in discover.ROUNDUP_URLS:
        if len(out) >= cap:
            break
        page_host = urlparse(url).netloc.lower().lstrip("www.")
        try:
            data = discover.cheap_scrape(url, extract_links=True)
            pages_scraped += 1
            for t in discover.extract_potential_targets(data, url):
                u = t.get("url", "")
                anchor = (t.get("anchor_text") or "").strip()
                if not _looks_like_opportunity(u, anchor, page_host):
                    continue
                ukey = _norm_url(u)
                if not ukey or ukey in known or ukey in seen:
                    continue
                seen.add(ukey)
                out.append({
                    "name": anchor or urlparse(u).netloc.lstrip("www.") or u,
                    "url": u,
                    "source": f"found on {page_host}",
                    "preapproved": False,
                    "title": anchor,
                    "description": t.get("reason", ""),
                    "snippet": "",
                    "discovered_at": datetime.now().isoformat(),
                    "method": "roundup_scrape",
                })
                if len(out) >= cap:
                    break
        except Exception as e:
            errors += 1
            print(f"[research] discovery error on {url}: {e}")
    return out, pages_scraped, errors


# Strong signals that a link is an actual "submit your site/app" opportunity (not nav/article).
_STRONG_HINTS = (
    "submit", "get-listed", "get listed", "add your", "add-your", "list your", "list-your",
    "add url", "add-url", "add a tool", "add-tool", "suggest", "submit your", "submit-your",
    "/directory", "directories", "add-listing", "add listing", "/add", "feature your",
)
# Anchors that are obviously navigation/editorial, never a submission target.
_JUNK_ANCHORS = (
    "contact", "review", "feature", "login", "log in", "sign up", "sign in", "menu", "home",
    "about", "privacy", "terms", "news", "equipment", "instruction", "course", "player",
    "tuition", "subscribe", "newsletter", "cookie", "advertise", "get in touch", "read more",
    "shop", "deals", "gps", "watch", "best ", "all reviews",
)


_SHARE_HOSTS = (
    "reddit.com", "twitter.com", "x.com", "facebook.com", "linkedin.com", "pinterest.com",
    "t.me", "telegram", "wa.me", "whatsapp", "tumblr.com", "mastodon", "flipboard", "getpocket",
)


def _looks_like_opportunity(url: str, anchor: str, page_host: str) -> bool:
    """Keep only external links that look like a real submission/listing opportunity."""
    if not url or url.startswith("mailto:"):
        return False
    host = urlparse(url).netloc.lower().lstrip("www.")
    if not host or host == page_host:
        return False  # internal nav links on the roundup page itself
    if any(s in host for s in _SHARE_HOSTS):
        return False  # social share/submit widgets, not directories
    hay = f"{url} {anchor}".lower()
    if not any(h in hay for h in _STRONG_HINTS):
        return False  # must signal a submit/list/directory action
    a = anchor.lower().strip()
    if any(j in a for j in _JUNK_ANCHORS):
        return False
    return True


def run(max_candidates: int = 50) -> list[dict]:
    """Build the de-duplicated candidate pool: curated seed (always) + new discoveries (additive)."""
    seeded, known = seed_from_tracker()
    print(f"[research] seeded {len(seeded)} open target(s) from the curated list.")

    remaining = max(0, max_candidates - len(seeded))
    discovered, pages_scraped, errors = discover_new(known, remaining)
    print(f"[research] discovered {len(discovered)} new candidate(s) "
          f"from {pages_scraped} page(s); {errors} error(s).")

    candidates = seeded + discovered

    # Record one research session so the dashboard Sessions tab reflects the run.
    if not os.getenv("STEEL_API_KEY", "").strip():
        outcome, detail = "skipped", "skipped (no STEEL_API_KEY)"
    elif pages_scraped == 0:
        outcome, detail = "error", "no pages scraped"
    else:
        outcome, detail = "completed", f"{pages_scraped} pages, {len(discovered)} new"
    record_session(
        f"research-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        target=f"Daily research — {detail}",
        mode="research",
        outcome=outcome,
    )

    return candidates[:max_candidates] if max_candidates else candidates


if __name__ == "__main__":
    results = run()
    out_path = DATA_DIR / f"candidates_{datetime.now().strftime('%Y-%m-%d')}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Research complete. {len(results)} candidates saved to {out_path}")
