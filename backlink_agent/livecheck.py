#!/usr/bin/env python3
"""
Live-link verifier — checks whether submitted listings actually went live.

For every tracked submission (the merged Submissions view: markdown + submission
log + approvals) it fetches the likely listing page(s) and looks for an anchor tag
linking to scoringzone.net/.app:

  - PENDING rows ("Submitted", "Pending review", "Applied"): checked every
    RECHECK_PENDING_DAYS. Found → status "Live ✓ (auto-verified YYYY-MM-DD)" with
    a dofollow/nofollow note + the listing URL, and a webhook alert. Still nothing
    after GIVE_UP_DAYS since the submission date → "No link found — review" (once).
  - LIVE rows: re-checked every RECHECK_LIVE_DAYS, and ONLY when a concrete
    listing URL is known (we never guess paths to second-guess a human "Live ✓").
    A found link only refreshes state — the log status is never rewritten, so a
    human-written "Live ✓ (confirmed ...)" string survives. Two consecutive misses
    → "Link lost — review" + alert (one miss is ignored: anti-flapping).
  - Blocked / rejected / not-yet-submitted rows are never tracked.

Fetching is requests-first (free); a Steel cheap_scrape fallback handles JS-shell
pages, and a full Steel browser session (stealth + CAPTCHA solving) is the last
resort for hard bot walls that block cheap_scrape too (e.g. Crunchbase's
Cloudflare). Both fallbacks draw from the LIVECHECK_MAX_STEEL_PER_RUN budget.

IMPORTANT precedence note: dashboard._merge_submission_log overlays the submission
log over the markdown UNCONDITIONALLY, so a bad write here could mask a human
"Live ✓" on the dashboard. The transition guards in _allowed_write() are the
protection — keep them intact.

Entry points:
    run_daily_checks() — the scheduled sweep (serve.py, after the propose cycle)
    check_one(name, url) — forced single check (POST /api/check-live)
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
sys.path.insert(0, str(BASE_DIR))  # steel_utils lives at repo root

try:
    from . import dashboard as dashboard_mod
except ImportError:  # direct/script context
    import dashboard as dashboard_mod  # type: ignore

from steel_utils import _submission_key, record_submission, send_alert

STATE_FILE = DATA_DIR / "livecheck_state.json"

RECHECK_PENDING_DAYS = 3
RECHECK_LIVE_DAYS = 7
GIVE_UP_DAYS = 60
LOST_MISS_THRESHOLD = 2

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Anchor tags only — a scoringzone URL echoed in form values or plain text is not
# a live backlink. The full tag is captured so rel="nofollow" can be inspected.
_LINK_TAG_RE = re.compile(
    r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']*scoringzone\.(?:net|app)[^"\']*)["\'][^>]*>',
    re.IGNORECASE)

# Product-name mention — the weaker signal. Many directories (e.g. BetaList) link out
# through a redirect interstitial (/startups/scoring-zone/visit), so the raw HTML never
# contains scoringzone.net even though the listing is live. A name mention only counts
# on a page whose own path names the product (i.e. the listing page itself).
_PRODUCT_RE = re.compile(r"scoring[\s -]?zone", re.IGNORECASE)


def _today() -> str:
    return date.today().isoformat()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"[livecheck] could not read state: {e}")
    return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(STATE_FILE.parent),
                                   prefix=".livecheck_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[livecheck] could not save state: {e}")


def _status_bucket(status: str) -> str:
    """Python mirror of shared.js statusClass — which display bucket a status is in."""
    s = (status or "").strip().lower()
    if "live" in s:
        return "live"
    if "block" in s or "reject" in s:
        return "blocked"
    if any(b in s for b in ("submit", "pending", "applied", "ready", "progress")):
        return "pending"
    return "other"


# Statuses that look "pending" to statusClass but mean the submission hasn't
# happened (or already failed) — never live-check these.
_UNTRACKED_BITS = ("not submitted", "to submit", "needs manual", "submitting",
                   "submit error", "ready", "prepared", "no link found")


def _is_trackable_pending(status: str) -> bool:
    s = (status or "").strip().lower()
    if _status_bucket(status) != "pending":
        return False
    return not any(b in s for b in _UNTRACKED_BITS)


def _looks_like_listing_url(url: str) -> bool:
    """True when the URL path itself names Scoring Zone (betalist.com/startups/
    scoring-zone, f6s.com/scoring-zone, ...) — i.e. it IS the listing page."""
    try:
        return "scoring" in (urlparse(url).path or "").lower()
    except Exception:
        return False


def candidate_urls(url: str, st: dict) -> list[str]:
    """Ordered, deduped pages worth checking for the backlink (max 3)."""
    out: list[str] = []

    def add(u: str):
        u = (u or "").strip()
        if u and u not in out:
            out.append(u)

    add(st.get("listing_url", ""))
    if not url:
        return out[:3]
    add(url)
    if not _looks_like_listing_url(url):
        try:
            p = urlparse(url)
            base = f"{p.scheme or 'https'}://{p.netloc}"
            parent = (p.path or "").rstrip("/").rsplit("/", 1)[0]
            if parent:
                add(base + parent)
            add(base + "/directory")
        except Exception:
            pass
    return out[:3]


def fetch_html(url: str) -> tuple[int | None, str]:
    """Plain-HTTP fetch. Returns (status_code, html); (None, "") on failure. Never raises."""
    try:
        import requests
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=20,
                            allow_redirects=True)
        return resp.status_code, resp.text or ""
    except Exception:
        return None, ""


def _needs_steel(status: int | None, html: str) -> bool:
    """Plain HTTP failed or returned a JS shell — worth one Steel cheap_scrape."""
    if status is None or status in (401, 403, 429) or status >= 500:
        return True
    return "<a" not in html.lower() or len(html) < 2048


# Bot-wall block pages (Cloudflare and friends). cheap_scrape has no real browser,
# so hard-walled sites (e.g. Crunchbase) serve it these instead of the listing.
_BLOCK_PAGE_BITS = ("attention required!", "just a moment", "access denied",
                    "verify you are human", "are you a robot", "cf-chl",
                    "challenge-platform", "enable javascript and cookies")


def _looks_blocked(html: str) -> bool:
    low = (html or "").lower()
    if not low or len(low) < 1024:
        return True
    return any(b in low for b in _BLOCK_PAGE_BITS)


def _browser_fetch(url: str) -> str:
    """Last-resort fetch with a full Steel browser session (stealth + CAPTCHA
    solving + proxy) — the same stack the submitters use, which gets past walls
    that block both plain requests and cheap_scrape. Returns "" on failure."""
    try:
        from steel_utils import steel_page, wait_for_captchas
        with steel_page(solve_captcha=True, api_timeout_ms=120_000) as (pw, browser, page, client, sid):
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            wait_for_captchas(client, sid)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            return page.content() or ""
    except Exception as e:
        print(f"[livecheck] browser fetch failed for {url}: {type(e).__name__}: {e}")
        return ""


def _search_index_verify(url: str) -> bool:
    """Verify a bot-walled listing via the Google index (Serper): true when the
    exact listing URL ranks for site:<domain> "scoring zone". Some sites (e.g.
    Crunchbase) hard-block every direct fetch — but Googlebot gets in, so the
    page being indexed under the product name is solid evidence it's live.
    Only called for listing-style URLs (path names the product)."""
    if not os.getenv("SERPER_API_KEY", "").strip():
        return False
    try:
        try:
            from .research import serper_search
        except ImportError:  # direct/script context
            from research import serper_search  # type: ignore
        host = (urlparse(url).netloc or "").replace("www.", "")
        if not host:
            return False
        want = url.rstrip("/").lower()
        for r in serper_search(f'site:{host} "scoring zone"'):
            if (r.get("link") or "").rstrip("/").lower() == want:
                return True
    except Exception as e:
        print(f"[livecheck] search-index verify failed for {url}: {e}")
    return False


def find_backlink(html: str) -> tuple[str | None, bool | None]:
    """First anchor href pointing at scoringzone.net/.app → (href, dofollow)."""
    m = _LINK_TAG_RE.search(html or "")
    if not m:
        return None, None
    dofollow = "nofollow" not in m.group(0).lower()
    return m.group(1), dofollow


def listing_is_live(page_url: str, html: str) -> bool:
    """Weaker signal: the listing page itself (path names the product) exists and
    mentions Scoring Zone — covers directories that link out via redirect URLs."""
    if not _looks_like_listing_url(page_url):
        return False
    return bool(_PRODUCT_RE.search(html or ""))


def _allowed_write(current_status: str, proposed: str) -> bool:
    """The never-downgrade guard, re-checked immediately before every log write.
    If the row currently displays as live, the ONLY permitted overwrite is the
    explicit link-lost flag."""
    if _status_bucket(current_status) == "live":
        return proposed.startswith("Link lost")
    return True


def _current_row_status(name: str, url: str) -> str:
    """The row's status in the merged Submissions view right now (fresh read)."""
    try:
        key = _submission_key(name, url)
        for row in dashboard_mod.build_submissions():
            if _submission_key(row.get("name", ""), row.get("url", "") or "") == key \
                    or _submission_key(row.get("name", ""), "") == _submission_key(name, ""):
                return row.get("status") or ""
    except Exception as e:
        print(f"[livecheck] could not read current status for {name!r}: {e}")
    return ""


def _state_key(name: str, url: str) -> str:
    """State identity. Name-first (names are stable; a row's URL is rewritten to the
    listing URL once found, which would silently fork a URL-based key)."""
    return _submission_key(name, "") or _submission_key("", url)


def check_target(name: str, url: str, state: dict, *, row_status: str = "",
                 force: bool = False, steel_budget: list | None = None) -> dict:
    """Check one target now. Mutates state[key] and writes the submission log /
    sends alerts on a permitted transition. Never raises."""
    key = _state_key(name, url)
    st = state.setdefault(key, {})
    for field, default in (("name", name), ("url", url), ("status", "pending"),
                           ("first_tracked", _today()), ("attempts", 0),
                           ("consecutive_misses", 0)):
        st.setdefault(field, default)
    # Always write the log with the ORIGINAL submission URL so record_submission
    # updates the existing entry instead of appending a near-duplicate.
    log_url = st.get("url") or url

    found_url: str | None = None
    dofollow: bool | None = None  # None = listing live but link goes via a redirect
    method = "none"
    blocked_fetches = 0
    clean_fetches = 0
    cands = candidate_urls(url, st)
    for cand in cands:
        status_code, html = fetch_html(cand)
        method = "requests"
        if _needs_steel(status_code, html):
            budget_left = steel_budget is None or steel_budget[0] > 0
            if budget_left:
                if steel_budget is not None:
                    steel_budget[0] -= 1
                try:
                    from steel_utils import cheap_scrape
                    html = cheap_scrape(cand, extract_links=False).get("html") or ""
                    method = "steel"
                except Exception as e:
                    print(f"[livecheck] cheap_scrape failed for {cand}: {e}")
                # Hard bot wall (Cloudflare et al.) blocks cheap_scrape too —
                # escalate to a full browser session if the budget allows.
                if _looks_blocked(html) and (steel_budget is None or steel_budget[0] > 0):
                    if steel_budget is not None:
                        steel_budget[0] -= 1
                    fetched = _browser_fetch(cand)
                    if fetched:
                        html = fetched
                        method = "steel-browser"
        if _looks_blocked(html):
            blocked_fetches += 1
        else:
            clean_fetches += 1
        href, rel_ok = find_backlink(html)
        if href:
            found_url, dofollow = cand, rel_ok
            break
        if listing_is_live(cand, html):
            found_url, dofollow = cand, None
            break

    # Every fetch bot-walled → the page can't be read directly. For listing-style
    # URLs, fall back to the Google index (Googlebot gets in even when we can't).
    all_blocked = bool(cands) and blocked_fetches > 0 and clean_fetches == 0
    if not found_url and all_blocked:
        for cand in cands:
            if _looks_like_listing_url(cand) and _search_index_verify(cand):
                found_url, dofollow, method = cand, None, "search-index"
                break

    st["attempts"] = st.get("attempts", 0) + 1
    st["last_checked"] = _now()
    st["last_method"] = method
    result = {"key": key, "checked": True, "found": bool(found_url),
              "listing_url": found_url, "dofollow": dofollow,
              "status_written": "", "detail": ""}

    current = row_status or _current_row_status(name, url)
    if method == "search-index":
        rel_word = "indexed"
        found_note = (f"Listing live at {found_url} — verified via the Google index "
                      "(page bot-walled to direct fetches; link rel unknown).")
    elif dofollow is None:
        rel_word = "redirect"
        found_note = f"Listing live at {found_url} (links via redirect — no direct dofollow link)."
    else:
        rel_word = "dofollow" if dofollow else "nofollow"
        found_note = f"{rel_word} link found at {found_url}"

    if found_url:
        st.update({"consecutive_misses": 0, "listing_url": found_url,
                   "dofollow": dofollow, "found_at": _now()})
        first_time_live = st.get("status") != "live"
        st["status"] = "live"
        if _status_bucket(current) == "live":
            # Human (or an earlier pass) already marked it live — refresh state only.
            result["detail"] = "already live; state refreshed"
        else:
            proposed = f"Live ✓ (auto-verified {_today()})"
            if _allowed_write(current, proposed):
                record_submission(name, log_url, status=proposed, method="livecheck",
                                  notes=found_note, listing_url=found_url)
                result["status_written"] = proposed
                if first_time_live:
                    send_alert(f"Backlink LIVE: {name} — {found_url} ({rel_word})")
        return result

    # Not found — but if every fetch was bot-walled (and the index couldn't confirm
    # either), that is absence of EVIDENCE, not absence of the link. Never count it
    # as a miss or write a downgrade; keep whatever status the row already has.
    if all_blocked:
        st["last_blocked"] = _now()
        result["detail"] = ("unverifiable — bot wall blocked every fetch; "
                            "status left unchanged")
        return result

    if st.get("status") == "live":
        st["consecutive_misses"] = st.get("consecutive_misses", 0) + 1
        if st["consecutive_misses"] >= LOST_MISS_THRESHOLD:
            st["status"] = "lost"
            proposed = "Link lost — review"
            if _allowed_write(current, proposed):
                record_submission(name, log_url, status=proposed, method="livecheck",
                                  notes=f"Previously-live link no longer found at "
                                        f"{st.get('listing_url') or url} "
                                        f"({st['consecutive_misses']} consecutive checks).")
                result["status_written"] = proposed
                send_alert(f"Backlink LOST: {name} — link no longer found at "
                           f"{st.get('listing_url') or url}")
        else:
            result["detail"] = (f"miss {st['consecutive_misses']}/{LOST_MISS_THRESHOLD} "
                                "— waiting for a second miss before flagging")
        return result

    # Pending and still nothing: give up after GIVE_UP_DAYS since the submission date.
    submitted_date = st.get("submitted_date") or st.get("first_tracked") or _today()
    try:
        age_days = (date.today() - date.fromisoformat(submitted_date[:10])).days
    except Exception:
        age_days = 0
    if age_days > GIVE_UP_DAYS and st.get("status") != "gave_up" and not force:
        st["status"] = "gave_up"
        proposed = "No link found — review"
        if _allowed_write(current, proposed):
            record_submission(name, log_url, status=proposed, method="livecheck",
                              notes=f"No backlink found after {age_days} days of checking. "
                                    "Verify manually or follow up with the directory.")
            result["status_written"] = proposed
    else:
        result["detail"] = "no link yet"
    return result


def _due(st: dict, interval_days: float) -> bool:
    last = st.get("last_checked")
    if not last:
        return True
    try:
        elapsed = datetime.now() - datetime.fromisoformat(last)
        return elapsed.total_seconds() >= interval_days * 86400
    except Exception:
        return True


def run_daily_checks() -> dict:
    """The scheduled sweep. Never raises. Returns a summary dict for logging."""
    summary = {"tracked": 0, "checked": 0, "went_live": 0, "lost": 0,
               "gave_up": 0, "changed": False, "errors": 0}
    try:
        rows = dashboard_mod.build_submissions()
    except Exception as e:
        print(f"[livecheck] could not build submissions: {e}")
        summary["errors"] += 1
        return summary

    state = load_state()
    steel_budget = [int(os.getenv("LIVECHECK_MAX_STEEL_PER_RUN", "5"))]

    for row in rows:
        name = row.get("name") or ""
        url = (row.get("url") or "").strip()
        status = row.get("status") or ""
        key = _state_key(name, url)
        st = state.get(key, {})

        bucket = _status_bucket(status)
        if bucket == "pending" and _is_trackable_pending(status):
            if not url and not st.get("listing_url"):
                continue  # nowhere to look
            if st.get("status") == "gave_up":
                continue
            summary["tracked"] += 1
            if not _due(st, RECHECK_PENDING_DAYS):
                continue
            if not st.get("submitted_date") and row.get("date"):
                state.setdefault(key, {})["submitted_date"] = str(row["date"])[:10]
        elif bucket == "live":
            # Only verify live rows with a CONCRETE listing URL — never guess
            # paths just to second-guess a human "Live ✓".
            listing = st.get("listing_url") or (url if _looks_like_listing_url(url) else "")
            if not listing:
                continue
            summary["tracked"] += 1
            if not _due(st, RECHECK_LIVE_DAYS):
                continue
            if st.get("status") not in ("live", "lost"):
                st = state.setdefault(key, st or {})
                st["status"] = "live"  # trust the human status as the baseline
                st.setdefault("listing_url", listing)
        else:
            continue

        try:
            result = check_target(name, url, state, row_status=status,
                                  steel_budget=steel_budget)
            summary["checked"] += 1
            written = result.get("status_written", "")
            if written.startswith("Live"):
                summary["went_live"] += 1
            elif written.startswith("Link lost"):
                summary["lost"] += 1
            elif written.startswith("No link found"):
                summary["gave_up"] += 1
            if written:
                summary["changed"] = True
        except Exception as e:
            print(f"[livecheck] check failed for {name!r}: {e}")
            summary["errors"] += 1

    save_state(state)
    print(f"[livecheck] sweep: {summary}")
    return summary


def check_one(name: str, url: str) -> dict:
    """Forced single check for the dashboard 'Check now' button."""
    state = load_state()
    # Budget 2: one cheap_scrape + one full-browser escalation for hard bot walls.
    result = check_target(name, url, state, force=True, steel_budget=[2])
    save_state(state)
    return result


if __name__ == "__main__":
    print(json.dumps(run_daily_checks(), indent=2))
