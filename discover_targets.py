#!/usr/bin/env python3
"""
Bulk discovery of backlink / submission targets for Scoring Zone.

Uses Steel's cheap `client.scrape()` endpoint for reconnaissance (much lower cost than full browser sessions).
Then you can decide which promising pages are worth a full interactive Steel session (with CAPTCHA solving) for deeper extraction or form filling.

Usage:
    python discover_targets.py

It will process a starter list of high-value "best golf apps / short game tools" roundup pages.
Output: console summary + (optionally) a targets.csv or updated list you can feed into the tracker.

Extend the ROUNDUP_URLS list with anything new you find.

This pairs perfectly with steel_utils.py.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from steel_utils import cheap_scrape, steel_api_key  # just to validate key is set early

# High-value pages known to list golf apps, practice tools, directories, or "submit your app" opportunities.
# Add more as we discover them via manual search or previous runs.
ROUNDUP_URLS = [
    "https://golfinsideruk.com/best-golf-apps/",
    "https://www.todays-golfer.com/equipment/best/golf-apps/",
    "https://www.golfmagic.com/equipment/golf-tech/best-golf-apps",
    "https://oldduffergolf.com/golf-apps/",
    "https://practical-golf.com/golf-gps-app-free",
    "https://www.golf-escapes.com/29-of-the-best-golf-apps-you-cant-live-without/",
    "https://goatcode.ai/best-golf-app-for-improvement-2026.html",
    "https://whygolf.com/pages/best-golf-app-2026",
    "https://www.golfmonthly.com/best-golf-deals/best-golf-gps-apps-213526",
    "https://golfpass.com/travel-advisor/articles/best-apple-watch-golf-apps",  # broader but useful
    # Golf-specific directories and resource pages (high priority for niche backlinks)
    "https://www.eatsleepgolf.net/directory",
    "https://www.eatsleepgolf.net/get-listed",
    # Add any "golf directory", "golf resources", "submit golf app", "best putting apps", "short game training" pages here.
]

# Simple heuristics for things that look like directories, submission pages, or contact opportunities
SUBMISSION_HINTS = [
    "submit", "get listed", "directory", "apply", "nominate", "feature", "review",
    "contact", "editor", "pitch", "press", "write for us", "contribute",
    "golf app", "practice app", "short game", "putting app", "training tool",
    "resource", "tool list", "app list", "best of", "roundup",
    "golf directory", "submit your", "feature your", "get your app", "coach directory",
    "association", "society", "club resources",
]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def extract_potential_targets(scrape_result: dict, base_url: str) -> list[dict]:
    """Robust extraction of interesting links + emails from a scraped page.
    Handles Steel's Link objects (have .url and .text attrs) and dicts.
    """
    targets = []
    html = scrape_result.get("html") or ""
    markdown = scrape_result.get("markdown") or ""
    links = scrape_result.get("links") or []

    # From the structured links (Steel returns list of steel.types.scrape_response.Link or dicts)
    for link in links:
        # Support both objects with attrs and dicts
        url = getattr(link, "url", None) or getattr(link, "href", None)
        if not url and hasattr(link, "get"):
            url = link.get("url") or link.get("href")
        if not url and isinstance(link, dict):
            url = link.get("url") or link.get("href")
        if not url:
            continue

        text = getattr(link, "text", "") or ""
        if not text and hasattr(link, "get"):
            text = link.get("text", "") or ""
        if not text and isinstance(link, dict):
            text = link.get("text", "") or ""

        text_lower = text.lower()
        full = urljoin(base_url, url)

        if any(h in text_lower or h in full.lower() for h in SUBMISSION_HINTS):
            targets.append({
                "url": full,
                "source": base_url,
                "anchor_text": (text or "")[:120],
                "reason": "submission_hint_in_link",
            })

    # Also hunt for bare URLs and emails in the text content (markdown + html)
    text_blob = f"{markdown}\n{html}"
    for match in re.finditer(r'https?://[^\s<>"\']+', text_blob):
        u = match.group(0).rstrip(".,;:!?")
        if any(h in u.lower() for h in SUBMISSION_HINTS):
            targets.append({
                "url": u,
                "source": base_url,
                "anchor_text": "",
                "reason": "url_in_content",
            })

    for email in EMAIL_RE.findall(text_blob):
        targets.append({
            "url": f"mailto:{email}",
            "source": base_url,
            "anchor_text": email,
            "reason": "email_found",
        })

    # Dedupe by url
    seen = set()
    unique = []
    for t in targets:
        if t["url"] not in seen:
            seen.add(t["url"])
            unique.append(t)
    return unique


def main():
    print("Scoring Zone Backlinks — Target Discovery (Steel cheap scrape mode)")
    print(f"Started: {datetime.utcnow().isoformat()}Z")
    print(f"Using Steel key: ...{steel_api_key()[-6:]} (validated)\n")

    all_targets = []

    for url in ROUNDUP_URLS:
        print(f"Scraping: {url}")
        try:
            data = cheap_scrape(url, extract_links=True)
            print(f"  → status={data.get('status')}, title={data.get('title')}")
            found = extract_potential_targets(data, url)
            print(f"  → {len(found)} promising links/emails extracted")
            all_targets.extend(found)
        except Exception as e:
            print(f"  ERROR: {e}")

    # Simple summary + optional CSV export
    print(f"\n=== Total promising items found: {len(all_targets)} ===")

    # Print a few examples
    for t in all_targets[:10]:
        print(f"  - {t['url'][:90]}  (from {t['source']}, reason={t['reason']})")

    # Export for easy import into tracker / manual review
    out_file = f"discovered_targets_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    with open(out_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["url", "source", "anchor_text", "reason"])
        writer.writeheader()
        writer.writerows(all_targets)

    print(f"\nExported full list to {out_file}")
    print("Next: review the CSV, add the best ones to directory-submissions.md or backlink-strategy.md,")
    print("then decide which ones are worth a full interactive Steel session (with solve_captcha=True) for form filling.")


if __name__ == "__main__":
    main()
