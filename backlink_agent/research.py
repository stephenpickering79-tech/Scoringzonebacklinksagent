#!/usr/bin/env python3
"""
Research Agent — discovers new backlink candidates.

This is a starting point. Expand with more sources over time.
"""

import json
from datetime import datetime
from pathlib import Path

# Reuse existing cheap scraping where possible
try:
    from ..steel_utils import cheap_scrape
except ImportError:
    # Fallback if run directly
    from steel_utils import cheap_scrape

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


KNOWN_ROUNDUP_URLS = [
    "https://golfinsideruk.com/best-golf-apps/",
    "https://www.todays-golfer.com/equipment/best/golf-apps/",
    "https://www.golfmagic.com/equipment/golf-tech/best-golf-apps",
    "https://oldduffergolf.com/golf-apps/",
    # Add more golf/sports tech roundups here over time
]

SEARCH_QUERIES = [
    "golf directory submit",
    "golf app directory",
    "sports tech directory",
    "golf coach tools list",
    "best golf apps 2026 submit",
]


def run(max_candidates: int = 50) -> list[dict]:
    candidates = []

    # 1. Scrape known roundup pages
    for url in KNOWN_ROUNDUP_URLS:
        try:
            data = cheap_scrape(url, extract_links=True)
            # Very naive extraction — improve this
            for link in data.get("links", [])[:20]:
                u = link.get("url") or ""
                if u and any(kw in u.lower() for kw in ["directory", "submit", "list", "resource", "tools"]):
                    candidates.append({
                        "url": u,
                        "source": url,
                        "discovered_at": datetime.now().isoformat(),
                        "method": "roundup_scrape",
                    })
        except Exception as e:
            print(f"Research error on {url}: {e}")

    # TODO: Add real web_search integration here (or use existing search tools)
    # For now we just return what we have from scraping.

    # Deduplicate and limit
    seen = set()
    unique = []
    for c in candidates:
        if c["url"] not in seen:
            seen.add(c["url"])
            unique.append(c)
            if len(unique) >= max_candidates:
                break

    return unique


if __name__ == "__main__":
    results = run()
    out_path = DATA_DIR / f"candidates_{datetime.now().strftime('%Y-%m-%d')}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Research complete. {len(results)} candidates saved to {out_path}")
