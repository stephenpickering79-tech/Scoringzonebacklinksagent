#!/usr/bin/env python3
"""
Steel-powered submission for Tinylaunch (backlinks focus).

Tinylaunch is a product launch platform / directory for indie makers and startups. Submitting can get your product listed, featured, and earn backlinks/visibility.

From inspection:
- "submit product" option on homepage.
- /submit page has launch/submit form (at minimum email; likely more fields for product details, description, etc.).
- May require account creation for full submission/launch.
- There is also a paid "get featured" or full directory submission service, but we're targeting free listing for backlink.

Prepared content is in tinylaunch-submission.txt (use the short desc, long desc, features, founder info, website, etc.).

This script uses best-effort form filling based on inspection. Adjust selectors as needed (run with session viewer open to debug).

Run:
export STEEL_API_KEY=...
python3 submit_tinylaunch.py

It will:
- Navigate, find submit flow.
- Fill product info.
- Submit.
- Screenshot results.
"""

from __future__ import annotations

import time
from pathlib import Path

from steel_utils import record_session, steel_page

# Dashboard session metadata
MODE = "submit_tinylaunch"
TARGET_URL = "https://www.tinylaunch.com/submit"

# Data from tinylaunch-submission.txt
PRODUCT_NAME = "Scoring Zone"
WEBSITE = "https://www.scoringzone.net"
SHORT_DESC = "The dedicated short-game performance app for golfers. Scored drills, pressure tests, and XP-driven practice for the 60% of shots inside 100 yards. Short Game Handicap from 60-shot Performance Hub. Free during early access. PWA."
LONG_DESC = """Scoring Zone turns short game practice into a scored, gamified system with real feedback.

Key Features:
- 50+ structured drills across putting, chipping, pitching, bunkers and distance wedges
- Performance Hub: one 60-shot session gives you a Short Game Handicap + Putting Handicap plus a PDF report showing exactly where you're losing strokes
- Pressure Mode with timers, streaks and elimination to simulate tournament conditions
- XP and progression — drills unlock as you level up; beat your handicap benchmark for 2x/3x XP
- AI Practice Assistant with guided sessions, clock-system wedge calculator, practice notepad and pre-round warm-ups
- Sim Lab for indoor simulator users
- Round stats logging with strokes-gained style insights benchmarked to your level

Why it exists:
Around 60% of golf shots happen inside 100 yards. Most amateurs spend 80%+ of practice time on full swing. Scoring Zone forces focus on the scoring shots with objective scoring and benchmarks. Your Short Game Handicap drops as you improve — the fastest feedback loop most golfers have ever had for their short game.

Currently free in early access (no credit card). App Store and Google Play launches planned once the user base is established. PGA Coaches Founders Programme running for pros who want early access, shape the product, and earn lifetime 50% commission on referred students.

Founder: Stephen Pickering, 3-handicap from Northern Ireland (ex-North of Ireland Amateur competitor), based in Dubai. Built solo.

Website: www.scoringzone.net
Get the app: Scan the QR on the homepage or visit scoringzone.app (PWA)"""

TAGS = "Golf App, Short Game Training, Putting Practice, Golf Practice Tools, Golf Improvement, Pressure Training"
FOUNDER = "Stephen Pickering"
EMAIL = "stephenpickering79@gmail.com"

def main():
    print("=== Tinylaunch Submission (Steel) ===")
    print("Starting Steel session...")

    with steel_page(solve_captcha=True) as (pw, browser, page, client, sid):
        print("Navigating to Tinylaunch...")
        screenshots: list[str] = []  # tracked for the dashboard session record
        page.goto("https://www.tinylaunch.com/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)

        page.screenshot(path="tinylaunch_home.png", full_page=True)
        screenshots.append("tinylaunch_home.png")
        print("Home screenshot saved.")

        # Try to find and click "submit product"
        print("Looking for submit product button/link...")
        clicked = False
        for sel in [
            'a:has-text("submit product")',
            'button:has-text("submit product")',
            'a:has-text("Submit")',
            'button:has-text("Submit")',
            'a[href*="submit"]',
        ]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    print(f"Clicking: {sel}")
                    el.click()
                    clicked = True
                    break
            except Exception:
                pass

        if not clicked:
            print("Could not auto-click submit. Navigating directly to /submit...")
            page.goto("https://www.tinylaunch.com/submit", wait_until="domcontentloaded", timeout=30000)

        time.sleep(3)
        print(f"On submit page. Title: {page.title()}")

        page.screenshot(path="tinylaunch_submit_page.png", full_page=True)
        screenshots.append("tinylaunch_submit_page.png")
        print("Submit page screenshot saved.")

        # Fill form - best effort based on inspection (email + likely product fields)
        print("Filling form fields (best effort)...")

        def try_fill_any(selectors: list[str], value: str, *, timeout: int = 3000) -> bool:
            """Try each selector in order; return True as soon as one fills successfully."""
            for sel in selectors:
                try:
                    page.fill(sel, value, timeout=timeout)
                    return True
                except Exception:
                    continue
            return False

        # Track core fields so we never submit an empty/partial form.
        filled = {
            "name": try_fill_any(
                ['input[name*="name"]', 'input[placeholder*="name" i]', 'input[placeholder*="product" i]'],
                PRODUCT_NAME,
            ),
            "website": try_fill_any(
                ['input[name*="website"]', 'input[type="url"]', 'input[placeholder*="website" i]', 'input[placeholder*="url" i]'],
                WEBSITE,
            ),
            "email": try_fill_any(
                ['input[name*="email"]', 'input[type="email"]', 'input[placeholder*="email" i]'],
                EMAIL,
            ),
        }

        # Description / short (optional).
        try_fill_any(['textarea', 'textarea[placeholder*="desc" i]', 'textarea[placeholder*="about" i]'], SHORT_DESC)

        # Longer desc if second textarea (optional).
        textareas = page.query_selector_all("textarea")
        if len(textareas) > 1:
            try:
                textareas[1].fill(LONG_DESC[:1500])
            except Exception:
                pass

        # Tags or category (optional).
        try_fill_any(['input[name*="tag"]', 'input[placeholder*="tag" i]', 'input[name*="category" i]'], TAGS)

        # Founder if separate (optional).
        try_fill_any(['input[name*="founder"]', 'input[placeholder*="founder" i]'], FOUNDER)

        page.screenshot(path="tinylaunch_filled.png", full_page=True)
        screenshots.append("tinylaunch_filled.png")
        print(f"Filled screenshot saved. Core fields set: {filled}")

        # Guard: don't submit if no core field landed — selectors don't match this form.
        core_ok = sum(filled.values())
        if core_ok == 0:
            print("ABORT: could not fill ANY core field (name/website/email).")
            print("Selectors likely don't match. Inspect the form in the session viewer and")
            print("update the selectors. Nothing was submitted.")
            record_session(sid, target=TARGET_URL, mode=MODE,
                           outcome="aborted", screenshots=screenshots)
            return
        if core_ok < len(filled):
            missing = [k for k, ok in filled.items() if not ok]
            print(f"WARNING: some core fields were not filled: {missing}. Proceeding — verify the result screenshot.")

        # Submit
        print("Submitting...")
        submitted = False
        for sel in [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Launch")',
            'button:has-text("Send")',
            'form button',
        ]:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    btn.click()
                    submitted = True
                    print(f"Clicked submit: {sel}")
                    break
            except Exception:
                pass

        if not submitted:
            print("No auto-submit button found. You may need to click in the session viewer.")

        time.sleep(5)
        page.wait_for_load_state("domcontentloaded", timeout=30000)

        result_ss = Path(f"tinylaunch_submitted_{int(time.time())}.png")
        page.screenshot(path=str(result_ss), full_page=True)
        screenshots.append(result_ss.name)
        print(f"Result screenshot: {result_ss}")

        record_session(sid, target=TARGET_URL, mode=MODE,
                       outcome="submitted" if submitted else "aborted",
                       screenshots=screenshots)

        print("\n=== Tinylaunch submission attempt complete ===")
        print("Review screenshots and session viewer. If account creation or additional steps required, complete manually and note in tracker.")
        print("Next: Update directory-submissions.md with status/date.")

if __name__ == "__main__":
    main()
