# Scoring Zone — Pure Backlink Targets (Directories & Listings Focus)

**Current mode:** Backlinks only via directories, listings, and form-based submissions.  
No email outreach, journalist pitches, or editorial content until told otherwise.

**Goal:** Acquire relevant golf-niche and app directory backlinks that drive targeted traffic and free signups to scoringzone.net.

**Master Prioritized List (Updated June 2026)**

## Tier 1 – Immediate (Highest Impact for Niche Traffic)
1. **Eat Sleep Golf – Get Listed**  
   URL: https://www.eatsleepgolf.net/get-listed  
   Why: Top golf-specific directory. High relevance to golfers and coaches. Permanent listing.  
   How: Simple form. Optional small donation ($5-20) to Caddy for a Cure / Cure Alzheimer's Fund.  
   Assets ready: `eatsleepgolf-submission.txt` (short + long description), logo, app-home-screenshot.png, ipad-iphone-mockup.webp, drill UI images, QR code.  
   Steel automation candidate: Yes (completed 2026-06-06 via `submit_eatsleepgolf.py`). Screenshots saved. Check confirmation/donation step.  
   Status: Submitted 2026-06-06 (via Steel). Priority: **#1** (monitor for live in 7-10 days)

2. **Eat Sleep Golf – Main Directory**  
   URL: https://www.eatsleepgolf.net/directory  
   Why: Core golf directory page. Good visibility.  
   Status: Covered by Get Listed submission (2026-06-06 via Steel). Monitor for appearance in directory.

3. **Eat Sleep Golf – Flyover Directory** (secondary)  
   URL: https://www.eatsleepgolf.net/flyoverdirectory  
   Notes: Course-focused but part of the same site.

## Tier 2 – Quick & Easy Wins
4. **Tinylaunch**  
   Search for current submit URL.  
   Free, low-effort app launch/listing platform. DR ~60. Good for early visibility and backlinks.

5. **Remaining easy SaaS / app directories from original queue** (re-check and complete):
   - Tiny Startups (free)
   - Re-verify F6S status
   - BetaList (pending review)
   - Any others from the Live/Submitted table that are still open.

## Tier 3 – Community & Profile Backlinks (Value-First)
6. **Indie Hackers**  
   Profile + launch post.  
   Stephen already human-verified. High-intent maker audience. Can deliver profile backlink + targeted traffic/signups.  
   **Action taken:** Polished ready-to-post draft in `indie-hackers-launch-draft.md`. Post it manually (value-first + metrics + link). Then update tracker with date + any backlink URL.

(Reddit value seeding removed per request — handling manually.)

## Already Live / High-Value Baseline
(See full table in `directory-submissions.md` for status)
- Crunchbase (strong DR + credibility)
- SaasHunt (#1 Project of the Day badge)
- SaaSHub, PeerPush, Turbo0, Toolfio
- Medium founder story (self-published but topical)

## How to Action These (Backlinks Focus)
- Use prepared text in `eatsleepgolf-submission.txt` and `pitch-paragraph.md` (the directory version) for form submissions.
- Attach visuals from this folder: new-scoring-zone-logo.png, app-home-screenshot.png, ipad-iphone-mockup.webp, drill UI jpgs, QR code.
- For any form that has CAPTCHA or anti-bot: Use `steel_utils.py` (the `steel_page(solve_captcha=True)` context manager + Playwright).
- Log every submission and live result back into `directory-submissions.md` (Live/Submitted table).
- Add UTM parameters to links where possible for traffic tracking: `?utm_source=eatsleepgolf&utm_medium=referral&utm_campaign=backlinks`

## Notes
- Niche relevance > raw DR. Eat Sleep Golf is the standout for actual golfer traffic and signups.
- The Steel discovery (June 2026) confirmed Eat Sleep Golf as the strongest new pure directory opportunity. Most "best apps" pages are review roundups rather than self-serve listing directories.
- Keep expanding this list via targeted Steel scraping of golf tool / app resource pages when needed.

**Source files:**
- `directory-submissions.md` (full tracker with Live table)
- `backlink-strategy.md` (overall plan)
- `eatsleepgolf-submission.txt` (ready copy for #1)
- `steel_utils.py` + `discover_targets.py` (automation & discovery tools)

Update this file and the main tracker as submissions are made or new pure directory targets are found.
