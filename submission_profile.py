#!/usr/bin/env python3
"""
Shared Scoring Zone submission profile.

One place for the company/product details the agent fills into directory forms.
Used by the generic best-effort submitter (submit_generic.py); the per-site
scripts (submit_eatsleepgolf.py / submit_tinylaunch.py) keep their own tuned
copies. Edit here to change what gets submitted everywhere generic.
"""

from __future__ import annotations

COMPANY_NAME = "Scoring Zone"
WEBSITE = "https://www.scoringzone.net"
CONTACT_NAME = "Stephen Pickering"
CONTACT_EMAIL = "stephenpickering79@gmail.com"

SHORT_DESC = (
    "Scoring Zone is the dedicated short-game performance app for golfers — scored drills, "
    "pressure tests and XP-driven practice for putting, chipping, pitching and bunker play "
    "(the 60% of shots inside 100 yards). Calculates a Short Game Handicap and Putting Handicap "
    "from a 60-shot assessment. Free during early access. PWA — no hardware required."
)

LONG_DESC = (
    "Scoring Zone turns short game practice into a scored, gamified system with real feedback.\n\n"
    "Key features:\n"
    "- 50+ structured drills across putting, chipping, pitching, bunkers and distance wedges\n"
    "- Performance Hub: one 60-shot session gives a Short Game Handicap + Putting Handicap plus a "
    "PDF report showing exactly where you're losing strokes\n"
    "- Pressure Mode with timers, streaks and elimination to simulate tournament conditions\n"
    "- XP and progression — drills unlock as you level up\n"
    "- AI Practice Assistant, clock-system wedge calculator, practice notepad and pre-round warm-ups\n"
    "- Round stats logging with strokes-gained style insights benchmarked to your level\n\n"
    "Around 60% of golf shots happen inside 100 yards, yet most amateurs spend 80%+ of practice on "
    "the full swing. Scoring Zone forces focus on the scoring shots with objective benchmarks. "
    "Currently free in early access (no credit card). Built by Stephen Pickering, a 3-handicap "
    "from Northern Ireland based in Dubai."
)

TAGS = "Golf App, Short Game Training, Putting Practice, Golf Practice Tools, Golf Improvement, Pressure Training"

# Convenience dict for callers that prefer keyed access.
PROFILE = {
    "company": COMPANY_NAME,
    "website": WEBSITE,
    "email": CONTACT_EMAIL,
    "contact": CONTACT_NAME,
    "short_desc": SHORT_DESC,
    "long_desc": LONG_DESC,
    "tags": TAGS,
}
