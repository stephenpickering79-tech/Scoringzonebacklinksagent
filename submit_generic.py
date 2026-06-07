#!/usr/bin/env python3
"""
Generic best-effort directory submitter (Steel).

For approved targets that have no dedicated script. Opens the target URL, fills the
common fields from submission_profile.PROFILE using the same selector strategy the
per-site scripts use, then clicks the submit button. Best-effort by nature: it works
on simple "submit your site / get listed" forms and will not handle logins,
multi-step wizards, or unusual fields — those are reported as "aborted" so the caller
can flag them "needs manual submit". Screenshots + a Steel session are always recorded.

Usage:
    from submit_generic import submit
    outcome = submit("Some Directory", "https://example.com/submit")   # "submitted"|"aborted"|"dry"

    # CLI: python submit_generic.py "Name" https://example.com/submit [--dry]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from steel_utils import record_session, record_submission, steel_page, wait_for_captchas
from submission_profile import (
    COMPANY_NAME, CONTACT_EMAIL, CONTACT_NAME, LONG_DESC, TAGS, WEBSITE, pick_short_desc,
)

MODE = "submit_generic"

# Submit-button selectors, tried in order (same set the per-site scripts use).
_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Submit")',
    'button:has-text("Add")',
    'button:has-text("List")',
    'button:has-text("Send")',
    'button:has-text("Get Listed")',
    'button:has-text("Launch")',
    'form button',
]


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower())[:40]


def submit(name: str, url: str, *, dry_run: bool = False) -> str:
    """Best-effort submit `name` (Scoring Zone) to the directory at `url`.

    Returns "submitted" | "aborted" | "dry". Raises only on hard failures (e.g. no
    STEEL_API_KEY), which the caller catches and records as an error.
    """
    print(f"=== Generic submit: {name} → {url} (dry_run={dry_run}) ===")
    slug = _slug(name or url)

    with steel_page(solve_captcha=True, api_timeout_ms=300_000) as (pw, browser, page, client, sid):
        screenshots: list[str] = []

        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)  # let dynamic content / anti-bot settle
        wait_for_captchas(client, sid)  # let Steel solve any load-time CAPTCHA before we fill

        before = Path(f"generic_{slug}_before.png")
        try:
            page.screenshot(path=str(before), full_page=True)
            screenshots.append(before.name)
        except Exception:
            pass

        def try_fill(selector: str, value: str, *, timeout: int = 4000) -> bool:
            try:
                page.fill(selector, value, timeout=timeout)
                return True
            except Exception:
                return False

        # Core fields (we refuse to submit unless at least one of these landed).
        filled = {
            "name": try_fill(
                'input[name*="company" i], input[name*="product" i], input[name="name"], '
                'input[placeholder*="company" i], input[placeholder*="product" i], input[placeholder*="name" i]',
                COMPANY_NAME,
            ),
            "website": try_fill(
                'input[name*="website" i], input[name*="url" i], input[type="url"], '
                'input[placeholder*="website" i], input[placeholder*="url" i]',
                WEBSITE,
            ),
            "email": try_fill(
                'input[name*="email" i], input[type="email"], input[placeholder*="email" i]',
                CONTACT_EMAIL,
            ),
        }

        # Descriptions: first textarea = short, second = long (best-effort). The short text is
        # rotated per target (footprint diversity — avoid identical copy on every directory).
        short_desc = pick_short_desc(url)
        textareas = page.query_selector_all("textarea")
        if textareas:
            try:
                textareas[0].fill(short_desc[:500])
            except Exception:
                pass
        if len(textareas) > 1:
            try:
                textareas[1].fill(LONG_DESC[:2000])
            except Exception:
                pass
        if not textareas:
            try_fill('textarea, [contenteditable="true"]', short_desc[:500])

        # Optional fields — never gate on these.
        try_fill('input[name*="tag" i], input[name*="categor" i], input[placeholder*="tag" i], input[placeholder*="categor" i]', TAGS)
        try_fill('input[name*="contact" i], input[name*="founder" i], input[name*="your-name" i], input[placeholder*="your name" i]', CONTACT_NAME)

        try:
            page.screenshot(path=f"generic_{slug}_filled.png", full_page=True)
            screenshots.append(f"generic_{slug}_filled.png")
        except Exception:
            pass

        core_ok = sum(filled.values())
        print(f"Core fields filled: {filled} ({core_ok}/3)")

        if core_ok == 0:
            # Distinguish a login/members wall from "selectors didn't match" so the dashboard
            # detail is honest about why it bowed out (rather than posting partial data).
            reason = "no core field matched — not a simple submit form"
            try:
                if page.query_selector('input[type="password"]'):
                    reason = "login/account required (password field present) — submit manually"
                else:
                    txt = (page.inner_text("body")[:2000] or "").lower()
                    if any(p in txt for p in ("sign in to", "log in to", "create an account to", "members only")):
                        reason = "login/account required — submit manually"
            except Exception:
                pass
            print(f"ABORT: {reason}.")
            record_session(sid, target=url, mode=MODE, outcome="aborted", screenshots=screenshots)
            return "aborted"

        if dry_run:
            print("DRY RUN: filled the form but NOT clicking submit.")
            record_session(sid, target=url, mode=MODE, outcome="dry", screenshots=screenshots)
            return "dry"

        # Click submit.
        submitted = False
        for sel in _SUBMIT_SELECTORS:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    print(f"Clicking submit: {sel}")
                    btn.click()
                    submitted = True
                    break
            except Exception:
                continue

        wait_for_captchas(client, sid)  # solve any CAPTCHA the submit triggered
        time.sleep(5)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass

        result = Path(f"generic_{slug}_result_{int(time.time())}.png")
        try:
            page.screenshot(path=str(result), full_page=True)
            screenshots.append(result.name)
        except Exception:
            pass

        outcome = "submitted" if submitted else "aborted"
        record_session(sid, target=url, mode=MODE, outcome=outcome, screenshots=screenshots)
        if submitted:
            # Best-effort: if our backlink is already visible on the result page, record whether
            # it's dofollow/nofollow (disavow-readiness). Usually the listing is reviewed later, so
            # this is often "not yet visible" — a proper check belongs in a later verification pass.
            rel_note = "link not yet visible (listing likely pending review)"
            try:
                link = page.query_selector('a[href*="scoringzone"]')
                if link:
                    rel = (link.get_attribute("rel") or "").lower()
                    rel_note = "nofollow link" if "nofollow" in rel else "dofollow link"
            except Exception:
                pass
            record_submission(name, url, status="Submitted (auto)", method="auto",
                              notes=f"Auto-submitted via generic Steel submitter. {rel_note}.")
        print(f"=== Generic submit {outcome}: {name} ===")
        return outcome


def main():
    args = [a for a in sys.argv[1:] if a != "--dry"]
    dry = "--dry" in sys.argv
    if len(args) < 2:
        print('Usage: python submit_generic.py "Name" https://example.com/submit [--dry]')
        return
    submit(args[0], args[1], dry_run=dry)


if __name__ == "__main__":
    main()
