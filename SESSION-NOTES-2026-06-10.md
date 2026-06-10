# Session summary — 2026-06-09/10

Everything built in this session, in order. PRs #23–#25 are merged and live on
Railway; **#26 and #27 are open and awaiting merge**.

---

## PR #23 — Competitor-backlink discovery + wider web search (MERGED)

**Problem found in the audit:** the 2026-06-07 run produced **0 candidates** — only
8 of 31 queries ran per day, 10 results each, and a strict keyword gate dropped
everything without "submit/directory" in the title. No competitor-based discovery
existed at all.

**What shipped:**
- `competitors.json` — 7 golf stat/GPS app competitors (TheGrint, Hole19,
  18Birdies, Golfshot, Shot Scope, Arccos, Clippd).
- `competitor_backlink_candidates.json` — harvested from Ubersuggest backlink-gap
  data (domains linking to competitors but NOT scoringzone.net): ~600 raw rows →
  50 classified prospects with DA, category and replicability reason.
- `research.discover_from_competitor_file()` — feeds these into the daily run
  (15/day, drains as items are approved/dismissed, 45-day staleness warning).
- `.claude/skills/harvest-competitor-backlinks/` — say **"harvest competitor
  backlinks"** in Claude Code to refresh the file (~monthly). The Ubersuggest MCP
  only exists in Claude Code sessions, never on Railway.
- Search widened: 12 queries/day, page-2 fetching for productive queries.
- `DISCOVERY_MIN_SCORE` (default 60): low scorers hidden to `data/rejected_*.json`.

## PR #24 — Submission confirmation + live-link verification (MERGED)

**Part A — did the submission actually go through?**
All three submit scripts now read the post-submit page: success text / error
text / form-gone-or-redirect. Logged as **Submitted — confirmed**,
**Submitted (unconfirmed)** or **Submitted (error on page — review)** with
evidence; an on-page error fires the webhook alert.

**Part B — did the listing go live?** (`backlink_agent/livecheck.py`)
- Pending submissions checked every 3 days (free HTTP first; Steel cheap-scrape
  fallback capped by `LIVECHECK_MAX_STEEL_PER_RUN=5`).
- Found → **Live ✓ (auto-verified)** + dofollow/nofollow note + listing URL +
  webhook alert. Nothing after 60 days → **No link found — review**.
- Live links re-checked weekly; gone on 2 consecutive checks → **Link lost —
  review** + alert.
- Handles redirect-style directories (BetaList links out via /visit interstitials).
- Hard guard: a human-written "Live ✓" can never be downgraded by the checker.
- `POST /api/check-live` + "Check now" buttons.

## PR #25 — Live Links page, Dismiss, favicon, UX polish (MERGED)

- **⛳ Live Links tab**: summary strip (live / watching / need attention) + three
  panels — live backlinks (listing URL, dofollow badge, found date, last checked,
  next check due), watching (days waiting, attempts), needs attention. Data =
  `docs/data/livecheck.json` built by `dashboard.build_livecheck()`.
- **Dismiss ✕** on every new-discovery row (confirm dialog) → permanent exclusion.
  Also fixed: dismissed items used to resurface from the day's shortlist on
  reload — exclusions now applied server-side in `build_proposals()`.
- **Favicon**: inline SVG clay "SZ" mark; no external request.
- Polish: hash routing (tab survives refresh), toast notifications, keyboard
  focus rings, better empty states, clearer disabled buttons.

## PR #26 — Submittable-directories focus, outreach deferred (OPEN)

**Stephen's decision (2026-06-10):** too many article/newsletter suggestions.
Current phase = only targets the agent can submit to itself. Email outreach is a
**later phase**.

- Web search: submission-signal gate reinstated (expanded keywords), article
  paths and newsletter platforms (Substack/Beehiiv/Ghost) blocked, article-bait
  queries removed (competitor reviews/alternatives, roundups, guest-post),
  6 directory-intent queries added.
- Competitor feed filtered by category: only `directory` / `review_site` /
  `resource_page` / `tool` pass (13 targets). The 36 roundup/blog/news/forum
  entries stay in the harvest file as the **outreach-phase seed list**.
- Scoring: LLM told articles/newsletters → score ≤40, "Outreach — later phase";
  orchestrator hides email/none discoveries (`SUBMITTABLE_ONLY=true`, flip to
  `false` on Railway when outreach starts).
- **TODO.md** created — records curated email outreach as future work.
- Bonus fix: Serper free tier 400s on `num>10` with quoted queries —
  `serper_search` pinned to num=10 (page-2 depth kept).

Verified: full run produced 13 competitor directory/review candidates + 27 search
results with **zero** article-style hits (hosts like directory.pga.org,
golfcoursetrades.com, chamber directories).

## PR #27 — Uniform proposal columns (OPEN)

Curated & approved vs New discoveries looked like different formats because each
group is its own table computing column widths from content (Target 144px vs
233px on the live site). One shared fixed column grid now keeps every column at
identical positions. CSS-only.

---

## How the pipeline works now (once #26/#27 merge)

1. **Daily 09:00 (Railway)**: research = curated list + competitor directories
   (from harvest file) + submission-intent web search → LLM scores → sub-60 and
   outreach-type discoveries hidden to `data/rejected_*.json` → dashboard.
2. **You review** the Output tab: Approve (queues / auto-submits allowlisted
   sites) or ✕ Dismiss (permanent exclusion).
3. **After submit**: confirmation detection logs whether the form actually went
   through; the live-checker then watches for the backlink to appear and flips
   status to Live ✓ (or flags lost/never-found). Watch it on the Live Links tab.

## Env vars on Railway (all defaults fine, no action needed)

| Var | Default | Purpose |
|-----|---------|---------|
| `DISCOVERY_MIN_SCORE` | 60 | hide discoveries scoring below this |
| `SUBMITTABLE_ONLY` | true | hide outreach-type (email/none) discoveries |
| `LIVECHECK_MAX_STEEL_PER_RUN` | 5 | cap Steel fallbacks per live-check sweep |

## Future work (see TODO.md)

- **Curated email outreach** (later phase): seed list = roundup/blog/news entries
  in `competitor_backlink_candidates.json` + accumulating `data/rejected_*.json`.
- Per-site submit scripts beyond Eat Sleep Golf / Tinylaunch.
- GitHub Actions fallback scheduler.

## Maintenance reminders

- Re-run the harvest (~monthly): say "harvest competitor backlinks" in Claude
  Code; agent logs warn after 45 days.
- Old article-style suggestions on the dashboard disappear at the next daily run
  after #26 merges (or ✕ them now).

---

# Session 2 — 2026-06-10 (afternoon/evening): full autonomy + live-test debugging

## Shipped & MERGED this session (PRs #28–#32)

- **#28 Approval = authorization.** Removed the auto-submit allowlist gate in
  `submitter.dispatch` — every approved target is now attempted (scripted module or
  generic submitter). Safety = human approval + APPROVE_TOKEN + MAX_AUTOSUBMITS_PER_DAY
  + AUTO_SUBMIT_DRY_RUN. Manual fallback now records a *specific* reason + the Steel
  session id (watch the replay). Deleted `auto_submit_allowlist.json`.
- **#29 Botwall live-check.** Livecheck escalates requests → cheap_scrape → full Steel
  browser; and verifies hard-walled listings (Crunchbase) via the Google index
  (`site:domain "scoring zone"`). Blocked ≠ missing (never downgrades on a bot wall).
- **#30 URL-echo false positives.** Bot-wall/challenge + SPA-404 pages echo the slug in
  script JSON → were wrongly read as "live". Now: skip evidence on blocked pages, match
  name only in visible markup (scripts stripped), require HTTP 200 for the weak signal.
- **#31 Autonomous account creation.** New `email_inbox.py` (IMAP reader, link OR code),
  `site_credentials` account helpers, `submit_generic._auto_register`. No per-site
  credentials. **One secret: `IMAP_PASSWORD`** (Gmail app password).
- **#32 Lost-row recovery.** Livecheck re-checks "Link lost" rows so a wrongly-downgraded
  listing auto-recovers (Crunchbase had got stuck).

## Live-site fix (done directly, no deploy)
- Crunchbase was stuck **"Link lost — review"** on the Railway dashboard (a pre-fix
  bot-walled sweep had written it to the volume; "lost" = bucket "other" = never
  re-checked). Triggered `POST /api/check-live` on the live site → search-index verified →
  now **Live ✓ (auto-verified)**. Live dashboard: 8 live incl. Crunchbase.

## Watching-sweep verdicts (2026-06-10)
- **AppSumo** — soft-404, nothing indexed, 62 days → removed (PR #33; too expensive).
- **Bright Data** — no listing URL ever recorded, nothing indexed → "No link found — review".
- **Product Hunt** — bot-walled to all checks + NOT in Google index 63 days after submit →
  likely never went live; verify manually.
- **Tiny Startups / F6S** — genuinely still pending.

## Config / account changes
- **#33 (OPEN)** — remove AppSumo via new committed `removed_submissions.json` filter in
  `dashboard.build_submissions` (drops it even though Railway's volume log re-adds it).
- **#34 (OPEN)** — agent now uses **scoringzone01@gmail.com** (submission_profile.CONTACT_EMAIL
  + the two scripted submitters). This is the inbox the IMAP reader watches.
- IMAP_PASSWORD app password generated for scoringzone01 (2-Step Verification on) and put in
  **local `.env`** — IMAP login verified (34 messages). **NOT yet on Railway.**

## Autonomous-signup live test — status (the thing left to finish)

Built `test_autonomous_signup.py` (a `--diagnose` mode + a 5-stage PASS/FAIL harness that
drives the real `_auto_register`). Audited and fixed four bugs that caused the
"no form fields found" aborts:
1. `email_inbox._scan_once` returned `None` on an empty inbox → caller crashed (caught) →
   verification *never* succeeded. Now returns a dict. **(the big unblocker)**
2. `_LOGIN_URL_RE` / signup link selectors didn't match underscore routes (`/sign_up`). Added.
3. `_detect_auth_wall` now treats a visible email+password pair as a signup form (JS pages).
4. `_open_signup` waits for the form to mount before judging it absent.
Plus: `_auto_register` returns a structured dict; `_click_signup_submit` skips OAuth
("Sign up with Google") buttons and clicks the real "Create account"; post-submit evidence
capture added.

**Live results:** detection → form load → fill → correct submit click → Steel credential
store all **PASS**. Steel CAPTCHA solving confirmed active. **BLOCKER:** the signup is
**rejected after the click** — no verification email ever arrives (tested 2Captcha + BetaList).
Suspect terms checkbox not ticked, or an on-form hCaptcha that needs solve-then-resubmit.
The just-added post-submit screenshot/error capture **needs one more run** to reveal the
exact cause. **Full remaining plan: see TODO.md "IN PROGRESS" section.**

## RESOLVED (late evening session): autonomous signup PROVEN end-to-end

5/5 PASS on dev.to: signup → fill → submit → Steel credential → verification email
→ link clicked → `accounts.json["dev.to"].verified == true`. The 2Captcha blocker
was the "Agreement" terms checkbox (fixed: label-text consent matching + tick-all
fallback + one post-rejection retry); 2Captcha itself is unusable as a target (its
own proprietary puzzle captcha). Additional fixes: generalized OAuth-button guard
("Continue with MyMLH"), submit click scoped to the password form, 15-min Steel
sessions (5-min died during the email wait), `_registrable_label` TLD-by-position
('dev.to' no longer searches the inbox for "to"), Rails-nested field selectors,
credentials.update fallback on 409. Product Hunt marked "Listing expired" (Stephen:
it WAS live; PH rotates listings off). 2Captcha test account cleaned from Steel.

## State to remember next session
- Uncommitted fixes live on branch **`chore/agent-email-scoringzone01`** (email_inbox,
  submit_generic, test_autonomous_signup) — NOT committed yet.
- Open PRs: **#33** (AppSumo), **#34** (email). #8 is an old unrelated project.md PR.
- Junk test accounts (betalist.com, 2captcha.com) were cleaned out of Steel + accounts.json.
- After the signup-submit blocker is fixed and green: commit branch → merge #33/#34 →
  set IMAP_PASSWORD on Railway.
