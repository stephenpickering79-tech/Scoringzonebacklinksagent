#!/usr/bin/env python3
"""
Steel-powered submission script for Eat Sleep Golf Directory (pure backlinks focus).

This is the #1 priority golf-niche directory for targeted traffic and signups.

Usage:
  1. Make sure STEEL_API_KEY is set (use the one provided).
  2. python submit_eatsleepgolf.py

It will:
- Create a Steel session with CAPTCHA solving enabled.
- Load the form at https://www.eatsleepgolf.net/get-listed
- Fill company/product info, website, descriptions (short + long), contact.
- Submit the form.
- Handle any CAPTCHA automatically via Steel.
- Take a screenshot of the result page for confirmation.
- Print the session viewer URL so you can watch live.
- Note: After form submit, there may be a donation step (optional $5-20 to charity).

After successful run:
- Check your email for confirmation if any.
- Manually note the date in directory-submissions.md
- Once the listing is live (7-10 business days), add the exact URL + any badge to the Live table.
- Add UTM to your site link if possible.

Requirements: steel_utils.py in same dir, steel-sdk + playwright installed.

Adjust selectors if the form fields change (use the printed page info or session viewer to debug).
"""

from __future__ import annotations

import time
from pathlib import Path

from steel_utils import steel_page

# === Submission data (from eatsleepgolf-submission.txt) ===
COMPANY_NAME = "Scoring Zone"
WEBSITE = "https://www.scoringzone.net"
SHORT_DESC = """Scoring Zone is the dedicated short-game performance app for golfers. Scored drills, pressure tests, and XP-driven practice for putting, chipping, pitching and bunker play — the 60% of shots that happen inside 100 yards. Calculates a Short Game Handicap and Putting Handicap from a 60-shot Performance Hub assessment. Free during early access. PWA — install to your phone like a native app. No hardware or sensors required.

Built for the gap most golfers ignore: structured, measurable practice between rounds. Benchmarks against your handicap level so you know if your practice is actually working. Pressure modes simulate on-course consequence. Complementary to GPS and scorecard apps."""

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

TAGS = "Golf App | Short Game Training | Putting Practice | Golf Practice Tools | Golf Improvement App | Pressure Training"
CONTACT_NAME = "Stephen Pickering"
CONTACT_EMAIL = "stephenpickering79@gmail.com"

DONATION_NOTE = "Happy to support the charitable partners with a small donation."


def main():
    print("=== Scoring Zone — Eat Sleep Golf Directory Submission (Steel) ===")
    print("Starting Steel session with CAPTCHA solving...")

    with steel_page(solve_captcha=True, api_timeout_ms=300_000) as (pw, browser, page, client, session_id):
        print(f"Session viewer: Check the printed URL above or previous output.")
        print("Navigating to form...")

        # Load the page
        page.goto("https://www.eatsleepgolf.net/get-listed", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)  # Give time for any dynamic content / anti-bot

        # Optional: Take initial screenshot for debugging
        screenshot_path = Path("eatsleepgolf_form_before.png")
        page.screenshot(path=str(screenshot_path), full_page=True)
        print(f"Screenshot before fill: {screenshot_path}")

        # === Fill the form ===
        # Strategy: Try common field names, placeholders, and labels.
        # If this fails on your run, open the session viewer and adjust the selectors below.
        # Many "get listed" forms use simple text inputs + textarea for description.

        print("Filling form fields...")

        # Company / Product Name
        try:
            page.fill('input[name="company"], input[name="name"], input[placeholder*="company" i], input[placeholder*="name" i]', COMPANY_NAME, timeout=5000)
        except:
            page.get_by_label("Company").fill(COMPANY_NAME) if page.get_by_label("Company").count() > 0 else None

        # Website
        try:
            page.fill('input[name="website"], input[type="url"], input[placeholder*="website" i], input[placeholder*="url" i]', WEBSITE, timeout=5000)
        except:
            pass

        # Short Description (many forms have a "short description" or first textarea)
        try:
            page.fill('textarea[name="short_description"], textarea[name="description"], textarea[placeholder*="short" i]', SHORT_DESC[:500], timeout=5000)
        except:
            # Fallback to first textarea
            textareas = page.query_selector_all("textarea")
            if textareas:
                textareas[0].fill(SHORT_DESC[:500])

        # Long / Full Description (second textarea or specific field)
        try:
            textareas = page.query_selector_all("textarea")
            if len(textareas) > 1:
                textareas[1].fill(LONG_DESC[:2000])
            else:
                page.fill('textarea[name="long_description"], textarea[name="full_description"], textarea[placeholder*="full" i], textarea[placeholder*="about" i]', LONG_DESC[:2000], timeout=5000)
        except:
            pass

        # Contact / Email
        try:
            page.fill('input[name="email"], input[type="email"], input[placeholder*="email" i]', CONTACT_EMAIL, timeout=5000)
        except:
            pass

        # Contact name if separate
        try:
            page.fill('input[name="contact"], input[name="founder"], input[placeholder*="contact" i]', CONTACT_NAME, timeout=5000)
        except:
            pass

        # Tags / Category if present
        try:
            page.fill('input[name="tags"], input[name="category"], input[placeholder*="tag" i], input[placeholder*="category" i]', TAGS, timeout=5000)
        except:
            pass

        print("Form fields filled (best effort). Taking screenshot...")
        page.screenshot(path="eatsleepgolf_form_filled.png", full_page=True)

        # === Submit ===
        print("Looking for submit button...")
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Submit")',
            'button:has-text("Send")',
            'button:has-text("List")',
            'button:has-text("Get Listed")',
            'form button',
        ]

        submitted = False
        for sel in submit_selectors:
            try:
                btn = page.query_selector(sel)
                if btn and btn.is_visible():
                    print(f"Clicking submit: {sel}")
                    btn.click()
                    submitted = True
                    break
            except:
                continue

        if not submitted:
            print("Could not auto-detect submit button. You may need to click manually in the session viewer.")
            print("Waiting 30 seconds for manual intervention or form changes...")
            time.sleep(30)

        # Wait for any post-submit page / confirmation / donation step
        print("Waiting for response / confirmation page...")
        time.sleep(5)
        page.wait_for_load_state("domcontentloaded", timeout=30000)

        # Screenshot the result (confirmation or donation prompt)
        result_screenshot = Path(f"eatsleepgolf_submitted_{int(time.time())}.png")
        page.screenshot(path=str(result_screenshot), full_page=True)
        print(f"Result screenshot saved: {result_screenshot}")

        # Print any visible success text
        try:
            body_text = page.inner_text("body")[:500]
            if "success" in body_text.lower() or "thank" in body_text.lower() or "listed" in body_text.lower():
                print("Possible success message detected in page text.")
            print("Page text snippet:", body_text[:300])
        except:
            pass

        print("\n=== Submission attempt complete ===")
        print("Check the result screenshot and session viewer (if still active).")
        print("If a donation step appeared, you can complete it manually for faster/better listing.")
        print("Next: Update directory-submissions.md with today's date and status.")


if __name__ == "__main__":
    main()
