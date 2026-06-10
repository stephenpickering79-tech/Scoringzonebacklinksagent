# Backlink agent — future work

## Later phase: curated email outreach (NOT yet — Stephen decides when to start)
Current phase is self-serve submission only: the agent pursues directories and
listing pages it can submit to without human intervention. Articles, roundups,
newsletters and editorial coverage need personal outreach — deferred to here.

- **Seed list already exists**: `competitor_backlink_candidates.json` entries with
  category `roundup` / `blog` / `news` (~30 classified targets: best-golf-apps
  roundups like golf.com and The Manual, golf newsletters like Golfer's Odyssey
  and Caddyshanks, golf media). Research currently skips these
  (`research.SUBMITTABLE_CATEGORIES`); the rejected logs (`data/rejected_*.json`)
  also accumulate LLM-flagged "Outreach — later phase" finds.
- **To build when starting**: an outreach queue page on the dashboard, per-target
  pitch drafts (use the `sz-cold-email` skill), reply/result tracking in the
  submissions log. Keep sending manual — no automated mass email.
- Flip `SUBMITTABLE_ONLY=false` on Railway to let outreach-type discoveries
  surface again when ready.

## DONE 2026-06-10 — Autonomous account creation + email verification (PROVEN END-TO-END)

When the agent hits a signup/login wall, it creates its own account (no
per-site credentials), reads the verification email from scoringzone01@gmail.com
over IMAP, completes it, then submits. See `email_inbox.py`, `site_credentials.py`
(generate_password/store_credential), `submit_generic._auto_register`, and the
test harness `test_autonomous_signup.py`.

**PROVEN 2026-06-10 (late evening): 5/5 PASS on dev.to** — signup detected → form
filled (Rails-nested `user[name]`/`user[username]` fields) → "Sign up" clicked inside
the password form → credential stored in Steel → verification email arrived →
link visited → `data/accounts.json["dev.to"].verified == true`. The dev.to account
is real and kept (useful for founder posts). Remaining ops step: set `IMAP_PASSWORD`
on Railway (see below).

**Working / proven in live tests:**
- Auth-wall detection (incl. JS-rendered + underscore `/sign_up` URLs).
- Signup form load + fill (email/password/confirm/name) + terms-checkbox best effort.
- Submit-button click now skips OAuth ("Sign up with Google") and clicks the real
  "Create account" button.
- Steel solves CAPTCHAs (solve_captcha=True + wait_for_captchas) throughout.
- Credential stored in Steel's encrypted Credentials API; account logged (passwordless)
  in `data/accounts.json`; Steel profile persisted in `data/steel_profiles.json`.
- IMAP login to scoringzone01 works (app password set in local `.env`, NOT yet Railway).
- Email link/code parser verified against real Serper (link) + 2Captcha (code) emails.

**Update 2026-06-10 (evening): rejection diagnosed and fixed; 2Captcha is a dead end.**
The diagnostic run revealed the rejection: *"Field «Agreement» must be accepted"* —
the terms checkbox (suspect (a)). Fixed and proven live:
- Consent-checkbox detection now matches label/wrapper text (not just the input's
  name/id), handles custom-styled hidden inputs (click the label / force + change
  event), and falls back to ticking every unchecked box when nothing keyword-matches.
- One automatic retry after a rejected submit: tick all boxes, re-fill blanked
  fields, re-click (`_signup_rejection` helper + `generic_<slug>_postsubmit2.png`).
- `store_credential` now falls back to `credentials.update` on a 409 (stale
  credential from an earlier attempt no longer wedges the new password).
- Steel sessions for auto-register bumped to 15 min (`api_timeout_ms=900_000`) — the
  5-min session died during the 180s email wait; dead-page paths hardened to degrade
  instead of crash (`TargetClosedError`).
- After the fix the Agreement error is gone, BUT 2Captcha then shows its own
  **proprietary puzzle CAPTCHA** ("assemble the code from elements") post-submit —
  not hCaptcha/reCAPTCHA, Steel can't solve it. **2Captcha is unusable as a test
  target.** No verification email can ever arrive there.

**Further fixes proven on the way to the dev.to PASS (all in `submit_generic.py` /
`email_inbox.py`):**
- OAuth-button guard generalized: any "sign up / log in / continue **with <X>**" is
  skipped unless X is email/password — provider whitelists kept missing entries
  (dev.to's "Continue with MyMLH"); `input[type=submit]` labels read from `value`.
- Submit click scoped to `form:has(input[type="password"])` first so header
  search/newsletter forms can never receive the click.
- `email_inbox._registrable_label` strips the TLD by POSITION — token-based
  stripping ate short domains ('dev.to' → searched the inbox for "to").
- Rails-nested field names handled: `user[name]` (fullname), `user[username]`
  (new `_USERNAME_SELECTORS` — the old `name*="user"` matched every field).
- 2Captcha test account cleaned out of Steel + accounts.json.

**Reliability notes (keep):**
- `_looks_logged_in()` (no auth-wall = "logged in") gave a false positive on
  BetaList — verification + stored credential are the real success signals.
- BetaList is paid (don't use as test). 2Captcha = proprietary captcha (dead end).
  IndieHackers is multi-step (email first, password next screen) — needs the
  multi-step handling listed under deferred items.
- Watch out for login vs signup forms that look identical (dev.to's
  `?state=email-signup` (hyphen) renders LOGIN; signup is `?state=email_signup`).

## Other deferred items
- GitHub Actions fallback scheduler (Railway is the only scheduler today).
- Live-link verification of email-confirmed listings (now partly unblocked once the
  verification reader above is proven — listing URLs that arrive by email could be
  opened the same way).
- IndieHackers-style multi-step signups (email → next screen → password): the
  current `_auto_register` fills one screen; would need a "fill, advance, fill" loop.
