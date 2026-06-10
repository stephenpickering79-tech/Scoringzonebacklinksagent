#!/usr/bin/env python3
"""
Research Agent — builds the daily candidate pool for the backlink agent.

Sources, combined and de-duplicated:
  1. SEED (local, network-free): the user's curated target lists already in the repo
     (directory-submissions.md + backlink-targets.md). These are read straight off disk,
     so the candidate pool is NEVER empty even if the network/keys are unavailable.
     Only "open" targets are seeded (not already live/blocked/rejected/done).
  2. COMPETITOR BACKLINKS (file, additive): referring domains that link to competitor golf apps
     but not to scoringzone.net, harvested via Ubersuggest in Claude Code (see
     .claude/skills/harvest-competitor-backlinks) and committed as
     competitor_backlink_candidates.json. Gated on that file existing.
  3. WEB SEARCH (Serper, additive): actively searches Google for submission/directory pages
     ("submit your golf app", etc.), filtered to real listing opportunities. Gated on
     SERPER_API_KEY.
  4. ROUNDUP SCRAPE (Steel, additive): extra links scraped from known golf roundups. Gated on
     STEEL_API_KEY.

A single "research" session is recorded per run (steel_utils.record_session) so the dashboard
Sessions tab reflects that the agent ran.

See backlink_automation_plan.md for the wider design.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, date
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
# Targets to permanently exclude from proposals (never seeded, never discovered, never re-added).
# Committed list = excluded_targets.json (repo); data/excluded.json (volume) is for runtime
# additions (e.g. an "exclude" button later). A candidate is dropped if any excluded entry is a
# substring of its name or URL (case-insensitive).
EXCLUDED_FILE = BASE_DIR / "excluded_targets.json"
RUNTIME_EXCLUDED_FILE = DATA_DIR / "excluded.json"

# Competitor config (shared with the harvest skill + competitor search queries) and the harvest
# output it produces. Both committed at repo root (data/ is gitignored, so a volume path would
# never reach Railway).
COMPETITORS_FILE = BASE_DIR / "competitors.json"
COMPETITOR_CANDIDATES_FILE = BASE_DIR / "competitor_backlink_candidates.json"
COMPETITOR_FILE_STALE_DAYS = 45
# CURRENT PHASE: only categories the agent can submit to itself (no human outreach).
# roundup/blog/news/other entries stay in the harvest file as the seed list for the
# future email-outreach phase (see TODO.md) but are not fed to the pipeline.
SUBMITTABLE_CATEGORIES = ("directory", "review_site", "resource_page", "tool")

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


def load_excluded() -> set:
    """Normalized exclusion phrases from excluded_targets.json (+ optional data/excluded.json)."""
    out: set = set()
    for path in (EXCLUDED_FILE, RUNTIME_EXCLUDED_FILE):
        try:
            if path.exists():
                for entry in json.loads(path.read_text(encoding="utf-8")):
                    n = _norm_name(str(entry))
                    if n:
                        out.add(n)
        except Exception as e:
            print(f"[research] could not read {path.name}: {e}")
    return out


def _is_excluded(candidate: dict, excluded: set) -> bool:
    nm = _norm_name(candidate.get("name", ""))
    url = _norm_url(candidate.get("url", ""))
    return any(e in nm or (url and e in url) for e in excluded)


def load_competitors() -> list[dict]:
    """[{domain, name}, ...] from competitors.json; [] on any failure (non-fatal)."""
    try:
        data = json.loads(COMPETITORS_FILE.read_text(encoding="utf-8"))
        out = []
        for c in data.get("competitors", []):
            if isinstance(c, dict) and c.get("domain"):
                out.append({"domain": c["domain"].strip(), "name": (c.get("name") or c["domain"]).strip()})
        return out
    except Exception as e:
        print(f"[research] could not read {COMPETITORS_FILE.name}: {e}")
        return []


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
            # Retry transient Steel scrape failures (the 500s) with backoff before giving up.
            data = None
            for attempt in range(3):
                try:
                    data = discover.cheap_scrape(url, extract_links=True)
                    break
                except Exception as se:
                    if attempt == 2:
                        raise
                    print(f"[research] scrape retry {attempt + 1}/3 for {url}: {se}")
                    time.sleep(2 * (attempt + 1))
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


# ---------------------------------------------------------------------------
# Web-search discovery (Serper.dev) — actively finds NEW directories/listing pages
# ---------------------------------------------------------------------------

SERPER_URL = "https://google.serper.dev/search"

# Submission-intent query POOL — bias toward pages that ACCEPT listings, not listicles. Covers
# both buckets Stephen wants: golf/sports/fitness niche AND quality general maker/SaaS directories.
# A rotating window runs each day (see _todays_queries) so the agent doesn't re-hit the same top
# results every run and gradually covers the whole pool.
SEARCH_QUERY_POOL = [
    # --- Golf / sports / fitness niche ---
    "submit your golf app to directory",
    "golf app directory add your app",
    "golf training tools submit your site",
    "golf coaching resources submit your link",
    "putting training aids directory submit",
    "best golf apps \"add your app\"",
    "golf technology directory submit your product",
    "sports tech startup directory submit your startup",
    "fitness app directory submit your app",
    "golf coach resources \"list your\" tool",
    "sports app directory add your listing",
    # --- Golf/sports niche, local, association & editorial (highest-value, lowest-risk) ---
    "golf app review \"submit your app\"",
    "golf instruction resources page \"submit a resource\"",
    "golf coaching directory add your business",
    "golf technology companies directory",
    "golf startup directory list your company",
    "sports technology directory submit your company",
    "golf association member directory apply",
    "\"add your golf business\" directory",
    # --- Quality general / maker / SaaS directories (volume filler, kept lower-weight) ---
    "submit your SaaS directory dofollow",
    "submit your startup directory dofollow",
    "best indie maker directories submit your product",
    "launch your app directory submit",
    "startup directory \"add your startup\" free dofollow",
    "app of the day directory submit",
    "indie hackers tool directory submit",
    # --- Directory-intent (self-serve submission pages) ---
    "golf directory \"add your website\"",
    "sports directory submit site free",
    "golf links directory submit",
    "app directory \"submit your app\" sports",
    "niche directory \"list your business\" golf",
    "free web directory submit sports fitness",
]
# NOTE: competitor-derived queries ("{Name} alternatives", "{Name} review") were removed —
# they return articles/roundups, which are outreach-phase targets (see TODO.md). Competitor
# value flows through competitor_backlink_candidates.json instead.

# How many of the pool to run per day. The window rotates by date so coverage spreads over ~3 days.
QUERIES_PER_RUN = 12


def _todays_queries() -> list[str]:
    """A rotating slice of the query pool, keyed off the date so different queries run each day
    (avoids re-finding the same top results every run, and covers the whole pool over time)."""
    pool = SEARCH_QUERY_POOL
    if len(pool) <= QUERIES_PER_RUN:
        return list(pool)
    start = (date.today().toordinal() * QUERIES_PER_RUN) % len(pool)
    return [pool[(start + i) % len(pool)] for i in range(QUERIES_PER_RUN)]

# Search filtering philosophy (CURRENT PHASE): only self-serve submission targets are wanted —
# directories and listing pages the agent can submit to without human outreach. A result must
# carry a submission/directory signal in its TITLE or URL. Articles, roundups, reviews and
# newsletters are outreach-phase targets (see TODO.md) and are filtered out here.
_SEARCH_HINTS = (
    "submit", "directory", "directories", "list your", "add your", "get listed",
    "add a site", "add url", "add listing", "register your", "listing",
    "places to submit", "where to submit",
)
# Hosts that are never a submission target (encyclopedias, stores, social, listicle media,
# newsletter platforms).
_EXCLUDE_HOSTS = (
    "wikipedia.org", "youtube.com", "amazon.", "apple.com", "play.google.com", "quora.com",
    "medium.com", "linkedin.com", "facebook.com", "instagram.com", "tiktok.com",
    "substack.com", "beehiiv.com", "ghost.io", "mailchi.mp",
)
# Host prefixes / URL fragments that signal docs/support/editorial, not a submission page.
_BAD_HOST_PREFIX = ("docs.", "support.", "developers.", "help.", "dev.")
_BAD_PATH = ("/articles/", "/newsletter", "/showcase", "/help", "/support", "taxonomy",
             "/on-nbc", "/faq", "/company/")


# Noise that LOOKS like a relevant result but isn't a submission target: directory *builders*,
# how-to/tutorial articles, templates, and editorial blog/guide/news content. Matched in title
# or URL.
_SEARCH_NOISE = (
    "how to", "how-to", "website builder", "directory builder", " builder", "app template",
    "template", "wordpress", "/tutorial", " module", "create a ", "build a ",
    "lessons from", "mistakes", "ultimate guide", "best ways",
    "/blog/", "/guides/", "/guide/", "/post/", "/news/", "newsletter", "why ",
)


_OWN_HOSTS_CACHE: set | None = None


def _own_and_competitor_hosts() -> set:
    """Our own site + competitor domains — a backlink can't live on the linked-to site itself,
    and competitor-name queries otherwise return the competitor's own pages."""
    global _OWN_HOSTS_CACHE
    if _OWN_HOSTS_CACHE is None:
        hosts = {"scoringzone.net", "scoringzone.app"}
        try:
            data = json.loads(COMPETITORS_FILE.read_text(encoding="utf-8"))
            site = (data.get("site") or "").strip().lower()
            if site:
                hosts.add(site)
            hosts |= {c["domain"].strip().lower() for c in data.get("competitors", [])
                      if isinstance(c, dict) and c.get("domain")}
        except Exception:
            pass
        _OWN_HOSTS_CACHE = hosts
    return _OWN_HOSTS_CACHE


def _search_result_is_opportunity(title: str, snippet: str, link: str) -> bool:
    host = urlparse(link).netloc.lower().lstrip("www.")
    if not host or any(s in host for s in _SHARE_HOSTS) or any(b in host for b in _EXCLUDE_HOSTS):
        return False
    if any(host == h or host.endswith("." + h) for h in _own_and_competitor_hosts()):
        return False
    if any(host.startswith(p) for p in _BAD_HOST_PREFIX):
        return False
    low = link.lower()
    if low.endswith(".pdf") or any(b in low for b in _BAD_PATH):
        return False
    # Drop builder/how-to/template/editorial noise.
    hay = f"{title} {link}".lower()
    if any(n in hay for n in _SEARCH_NOISE):
        return False
    # Require a submission/directory signal in the TITLE or URL — current phase is self-serve
    # submission only; article/newsletter outreach comes later (TODO.md).
    return any(h in hay for h in _SEARCH_HINTS)


def serper_search(query: str, num: int = 10, page: int = 1) -> list[dict]:
    """Return Serper organic results [{title, link, snippet}, ...] for a query.

    num stays at 10: Serper's free tier 400s ("Query pattern not allowed") on
    num>10 for queries containing quoted phrases. Depth comes from page=2 instead.
    """
    import requests  # already a dependency

    key = os.getenv("SERPER_API_KEY", "").strip()
    resp = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={"q": query, "num": num, "page": page},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("organic", []) or []


def discover_via_search(known: set, max_items: int) -> tuple[list[dict], int, int]:
    """Find new submission/directory pages via Serper web search. Returns (candidates, queries_run, errors).

    Additive + gated: skipped cleanly when SERPER_API_KEY is absent. Filters to real submission
    pages and de-dupes against everything already known.
    """
    if not os.getenv("SERPER_API_KEY", "").strip():
        print("[research] No SERPER_API_KEY — skipping web-search discovery.")
        return [], 0, 0
    if max_items <= 0:
        return [], 0, 0

    out: list[dict] = []
    seen: set = set()
    queries_run = 0
    errors = 0
    cap = min(max_items, 35)
    for query in _todays_queries():
        if len(out) >= cap:
            break
        accepted_for_query = 0
        # Page 1 always; page 2 only when page 1 produced something (bounds Serper spend while
        # going deeper on queries that are actually paying off).
        for page in (1, 2):
            if page > 1 and accepted_for_query == 0:
                break
            if len(out) >= cap:
                break
            try:
                results = serper_search(query, num=10, page=page)
                queries_run += 1
            except Exception as e:
                errors += 1
                print(f"[research] search error for '{query}' (page {page}): {e}")
                break
            for r in results:
                link = (r.get("link") or "").strip()
                title = (r.get("title") or "").strip()
                snippet = (r.get("snippet") or "").strip()
                if not link or not _search_result_is_opportunity(title, snippet, link):
                    continue
                ukey = _norm_url(link)
                if not ukey or ukey in known or ukey in seen:
                    continue
                seen.add(ukey)
                accepted_for_query += 1
                out.append({
                    "name": title or urlparse(link).netloc.lstrip("www.") or link,
                    "url": link,
                    "source": f"web search: {query}",
                    "preapproved": False,
                    "title": title,
                    "description": snippet,
                    "snippet": snippet,
                    "discovered_at": datetime.now().isoformat(),
                    "method": "web_search",
                })
                if len(out) >= cap:
                    break
    return out, queries_run, errors


def discover_from_competitor_file(known: set, max_items: int) -> tuple[list[dict], int]:
    """Candidates from the committed Ubersuggest harvest (competitor_backlink_candidates.json).

    Returns (candidates, skipped). Additive + offline: silently returns ([], 0) when the file is
    absent (it only exists after the harvest skill has run). Highest-DA replicable domains first,
    capped per run so the file drains over days as items get approved/dismissed (handled/excluded
    domains stop matching on later runs).
    """
    if not COMPETITOR_CANDIDATES_FILE.exists():
        print("[research] no competitor_backlink_candidates.json — skipping competitor discovery "
              "(run the harvest-competitor-backlinks skill in Claude Code to create it).")
        return [], 0
    if max_items <= 0:
        return [], 0
    try:
        data = json.loads(COMPETITOR_CANDIDATES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[research] could not read {COMPETITOR_CANDIDATES_FILE.name}: {e}")
        return [], 0

    generated_at = data.get("generated_at", "")
    try:
        age_days = (datetime.now() - datetime.fromisoformat(generated_at)).days
        if age_days > COMPETITOR_FILE_STALE_DAYS:
            print(f"[research] competitor harvest is {age_days} days old — re-run the "
                  f"harvest-competitor-backlinks skill to refresh it.")
    except Exception:
        pass

    replicable = [c for c in data.get("candidates", []) if isinstance(c, dict) and c.get("replicable")]
    # Current phase: self-serve submission targets only. Roundups/blogs/news need email
    # outreach — that's a later phase (TODO.md); they stay in the file as its seed list.
    rows = [c for c in replicable if (c.get("category") or "") in SUBMITTABLE_CATEGORIES]
    outreach_skipped = len(replicable) - len(rows)
    if outreach_skipped:
        print(f"[research] competitor file: {outreach_skipped} outreach-phase entr(ies) "
              f"(roundup/blog/news/forum) skipped — submittable categories only.")
    rows.sort(key=lambda c: c.get("domain_authority") or 0, reverse=True)

    out: list[dict] = []
    skipped = 0
    cap = min(max_items, 15)
    for c in rows:
        domain = (c.get("domain") or "").strip()
        url = (c.get("url") or "").strip() or (f"https://{domain}/" if domain else "")
        if not url:
            continue
        # Dedupe on BOTH the page URL and the bare domain so a deep harvest path and a
        # homepage found via web search collapse to one candidate.
        keys = {_norm_url(url)}
        if domain:
            keys.add(_norm_url(f"https://{domain}"))
        keys.discard("")
        if not keys or any(k in known for k in keys):
            skipped += 1
            continue
        known |= keys
        comps = [x for x in (c.get("competitors_to") or []) if x]
        da = c.get("domain_authority")
        out.append({
            "name": (c.get("name") or domain or url),
            "url": url,
            "source": f"links to {', '.join(comps[:3]) or 'competitors'} (DA {da if da is not None else '?'})",
            "preapproved": False,
            "title": c.get("name") or domain,
            "description": (f"{c.get('category', 'site')}: {c.get('replicability_reason', '')} "
                            f"Referring domain (DA {da if da is not None else '?'}) that links to "
                            f"competitor(s) {', '.join(comps)} but not scoringzone.net."),
            "snippet": c.get("replicability_reason", ""),
            "domain_authority": da,
            "discovered_at": generated_at or datetime.now().isoformat(),
            "method": "competitor_backlinks",
        })
        if len(out) >= cap:
            break
    return out, skipped


def _load_handled_urls() -> set:
    """Normalized URLs already in the Approve queue or the submission log, so discovery never
    re-surfaces (and the LLM never re-scores) a target the human has already actioned/submitted.
    Read directly (no import coupling); best-effort."""
    out: set = set()
    for fname in ("approvals.json", "submission_log.json"):
        path = DATA_DIR / fname
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                for entry in (data if isinstance(data, list) else []):
                    if isinstance(entry, dict):
                        u = _norm_url(entry.get("url", ""))
                        if u:
                            out.add(u)
        except Exception as e:
            print(f"[research] could not read {fname} for dedup: {e}")
    return out


def run(max_candidates: int = 50) -> list[dict]:
    """Build the de-duplicated candidate pool: curated seed (always) + new discoveries (additive).

    Discovery has three additive sources — competitor backlinks (gated on the harvest file),
    web search (SERPER_API_KEY) and roundup scrape (STEEL_API_KEY) — each de-duped against the
    curated list, each other, AND already-handled targets (Approve queue + submission log).
    """
    seeded, known = seed_from_tracker()
    print(f"[research] seeded {len(seeded)} open target(s) from the curated list.")

    # Don't re-discover/re-score targets already queued or submitted (saves OpenRouter spend +
    # avoids duplicate proposals/submissions).
    handled = _load_handled_urls()
    if handled:
        known |= handled
        print(f"[research] skipping {len(handled)} already-handled target(s) (approved/submitted).")

    remaining = max(0, max_candidates - len(seeded))

    # Highest-signal discovery first: domains that already link to competitor golf apps
    # (Ubersuggest harvest committed by the harvest-competitor-backlinks skill).
    competitor, comp_skipped = discover_from_competitor_file(known, remaining)
    if competitor or comp_skipped:
        print(f"[research] competitor backlinks added {len(competitor)} candidate(s); "
              f"{comp_skipped} already known/handled.")
    remaining = max(0, remaining - len(competitor))

    # Primary live discovery: active web search for submission/directory pages (Serper).
    searched, queries_run, serr = discover_via_search(known, remaining)
    for c in searched:
        known.add(_norm_url(c["url"]))
    print(f"[research] web search added {len(searched)} candidate(s) "
          f"from {queries_run} querie(s); {serr} error(s).")

    # Secondary discovery: scrape known golf roundups (Steel) for any extra links.
    remaining = max(0, remaining - len(searched))
    scraped, pages_scraped, rerr = discover_new(known, remaining)
    print(f"[research] roundup scrape added {len(scraped)} candidate(s) "
          f"from {pages_scraped} page(s); {rerr} error(s).")

    # Combine + final cross-source de-dupe (curated first, then search, then scrape) + apply
    # the permanent exclusion list (e.g. Reddit, Product Hunt) so excluded targets never re-appear.
    excluded = load_excluded()
    candidates: list[dict] = []
    seen: set = set()
    dropped_excluded = 0
    for c in seeded + competitor + searched + scraped:
        if _is_excluded(c, excluded):
            dropped_excluded += 1
            continue
        key = _norm_url(c.get("url", "")) or _norm_name(c.get("name", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append(c)
    if dropped_excluded:
        print(f"[research] excluded {dropped_excluded} candidate(s) per excluded_targets.json.")

    # Record one research session so the dashboard Sessions tab reflects the run.
    new_total = len(competitor) + len(searched) + len(scraped)
    if (not competitor and not os.getenv("STEEL_API_KEY", "").strip()
            and not os.getenv("SERPER_API_KEY", "").strip()):
        outcome, detail = "skipped", "skipped (no SERPER_API_KEY / STEEL_API_KEY)"
    else:
        outcome = "completed"
        detail = (f"{queries_run} searches, {pages_scraped} pages, "
                  f"{len(competitor)} competitor, {new_total} new")
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
