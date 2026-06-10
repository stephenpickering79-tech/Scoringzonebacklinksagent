#!/usr/bin/env python3
"""
Generic best-effort directory submitter (Steel).

For approved targets that have no dedicated script. Opens the target URL, waits for
the form (including JS-rendered ones), fills the common fields from
submission_profile.PROFILE, and clicks submit — handling iframe-embedded forms,
logo/screenshot upload fields, and simple multi-step wizards. Account-required
sites are handled via Steel's Credentials API: store a login once with
manage_credentials.py and Steel auto-fills + auto-submits the login form
server-side (the password never reaches this process or the screenshots); the
authenticated profile is persisted so later runs skip login entirely.

When a form genuinely can't be completed the run aborts with a specific reason
(login needed / CAPTCHA unsolved / no form / no submit button) so the dashboard
shows exactly why. Screenshots + a Steel session are always recorded.

Usage:
    from submit_generic import submit
    outcome = submit("Some Directory", "https://example.com/submit")
    # → {"outcome": "submitted", "confirmation": ..., "evidence": ...}
    #   | {"outcome": "aborted", "reason": ...} | "dry"

    # CLI: python submit_generic.py "Name" https://example.com/submit [--dry]
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import site_credentials
from steel_utils import (record_session, record_submission, steel_page, wait_for_captchas,
                         detect_submission_confirmation, send_alert, last_session_info)
from submission_profile import (
    COMPANY_NAME, CONTACT_EMAIL, CONTACT_NAME, LONG_DESC, TAGS, WEBSITE, pick_short_desc,
)

MODE = "submit_generic"

# Submit-button selectors, tried in order (same set the per-site scripts use).
_SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'input[type="submit"]',
    'input[type="image"]',
    'button:has-text("Submit")',
    'button:has-text("Add")',
    'button:has-text("List")',
    'button:has-text("Send")',
    'button:has-text("Get Listed")',
    'button:has-text("Launch")',
    'button:has-text("Save")',
    'button:has-text("Apply")',
    'button:has-text("Continue")',
    '[role="button"]:has-text("Submit")',
    'form button',
]

_NAME_SELECTORS = (
    'input[name*="company" i], input[name*="product" i], input[name="name"], '
    'input[id*="company" i], input[id*="product" i], input[name*="title" i], '
    'input[placeholder*="company" i], input[placeholder*="product" i], input[placeholder*="name" i]'
)
_WEBSITE_SELECTORS = (
    'input[name*="website" i], input[name*="url" i], input[type="url"], '
    'input[id*="website" i], input[id*="url" i], input[name*="site" i], input[name*="link" i], '
    'input[placeholder*="website" i], input[placeholder*="url" i]'
)
_EMAIL_SELECTORS = (
    'input[name*="email" i], input[type="email"], input[placeholder*="email" i]'
)

# Brand assets for forms that want a logo/screenshot upload (never gated on).
_ASSETS_DIR = Path(__file__).parent / "assets"

_LOGIN_LINK_SELECTORS = [
    'a[href*="login" i]', 'a[href*="signin" i]', 'a[href*="sign-in" i]',
    'a:has-text("Log in")', 'a:has-text("Login")', 'a:has-text("Sign in")',
]
_LOGIN_TEXT_PATTERNS = ("sign in to", "log in to", "must be logged in", "members only")
_SIGNUP_TEXT_PATTERNS = ("create an account to", "sign up to", "register to", "join to submit")
_LOGIN_URL_RE = re.compile(r"/(login|signin|sign-in|signup|sign-up|register)\b", re.I)
_LOGIN_FAIL_PATTERNS = ("incorrect password", "invalid login", "invalid email or password",
                        "wrong password", "try again")


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text.lower())[:40]


def _aborted(reason: str) -> dict:
    sid = (last_session_info().get("session_id") or "")[:8]
    if sid:
        reason = f"{reason} (Steel session {sid}… — watch the replay at app.steel.dev)"
    print(f"ABORT: {reason}")
    return {"outcome": "aborted", "reason": reason}


def _wait_for_form(page) -> bool:
    """Give JS-rendered pages time to actually mount their form before judging it absent."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    try:
        page.wait_for_selector(
            "form input, form textarea, input[type='email'], input[type='url'], textarea",
            timeout=10000,
        )
        return True
    except Exception:
        return False


def _detect_auth_wall(page) -> str | None:
    """Return "login" / "signup" when the page demands an account, else None."""
    try:
        if _LOGIN_URL_RE.search(page.url or ""):
            return "login"
        txt = (page.inner_text("body")[:3000] or "").lower()
        if any(p in txt for p in _SIGNUP_TEXT_PATTERNS):
            return "signup"
        if any(p in txt for p in _LOGIN_TEXT_PATTERNS):
            return "login"
        if page.query_selector('input[type="password"]'):
            return "login"
    except Exception:
        pass
    return None


def _fill_form(target, desc_seed: str) -> tuple[dict, str]:
    """Fill the common fields on `target` (a Page or Frame). `desc_seed` is the target
    URL — it seeds the rotated short description (footprint diversity).

    Returns (filled-core-fields dict, last selector that filled — for the Enter fallback).
    """
    last_filled = ""

    def try_fill(selector: str, value: str, *, timeout: int = 4000) -> bool:
        nonlocal last_filled
        try:
            target.fill(selector, value, timeout=timeout)
            last_filled = selector
            return True
        except Exception:
            return False

    # Core fields (we refuse to submit unless at least one of these landed).
    filled = {
        "name": try_fill(_NAME_SELECTORS, COMPANY_NAME),
        "website": try_fill(_WEBSITE_SELECTORS, WEBSITE),
        "email": try_fill(_EMAIL_SELECTORS, CONTACT_EMAIL),
    }

    # Descriptions: first textarea = short, second = long (best-effort). The short text is
    # rotated per target (footprint diversity — avoid identical copy on every directory).
    short_desc = pick_short_desc(desc_seed)
    try:
        textareas = target.query_selector_all("textarea")
    except Exception:
        textareas = []
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

    # Logo/screenshot uploads — best-effort with committed brand assets.
    logo = _ASSETS_DIR / "logo.png"
    shot = _ASSETS_DIR / "screenshot.png"
    try:
        uploads = target.query_selector_all('input[type="file"]')
        for i, inp in enumerate(uploads[:2]):
            asset = logo if i == 0 else shot
            if asset.exists():
                inp.set_input_files(str(asset))
                print(f"Uploaded {asset.name} to file input {i}")
    except Exception:
        pass

    return filled, last_filled


def _form_target(page):
    """The Page itself, or the first child iframe that actually contains form inputs
    (Typeform/Tally/Google Forms embeds)."""
    try:
        if page.query_selector("form input, form textarea, input[type='email'], input[type='url']"):
            return page
        for frame in page.frames[1:]:
            try:
                if frame.query_selector("form input, form textarea, input[type='email']"):
                    print(f"Form found inside iframe: {frame.url[:80]}")
                    return frame
            except Exception:
                continue
    except Exception:
        pass
    return page


def _click_submit(target) -> str:
    """Click the first visible submit control. Returns the selector used, or ""."""
    for sel in _SUBMIT_SELECTORS:
        try:
            btn = target.query_selector(sel)
            if btn and btn.is_visible():
                print(f"Clicking submit: {sel}")
                btn.click()
                return sel
        except Exception:
            continue
    return ""


def _looks_logged_in(page) -> bool:
    return _detect_auth_wall(page) is None


def _attempt_login(page, client, sid: str, slug: str, screenshots: list) -> bool:
    """Get Steel's credential injection a login form to work with, then wait it out.

    Steel detects the form and fills + submits the stored credential server-side
    (fields blurred, values never exposed here). We just navigate and poll.
    """
    if not page.query_selector('input[type="password"]'):
        for sel in _LOGIN_LINK_SELECTORS:
            try:
                link = page.query_selector(sel)
                if link and link.is_visible():
                    print(f"Opening login page via: {sel}")
                    link.click()
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    break
            except Exception:
                continue

    wait_for_captchas(client, sid)
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        body = ""
        try:
            body = (page.inner_text("body")[:2000] or "").lower()
        except Exception:
            pass
        if any(p in body for p in _LOGIN_FAIL_PATTERNS):
            break
        if _looks_logged_in(page):
            print("Login looks successful.")
            try:
                page.screenshot(path=f"generic_{slug}_loggedin.png", full_page=False)
                screenshots.append(f"generic_{slug}_loggedin.png")
            except Exception:
                pass
            return True
        time.sleep(2)
    print("Login did not complete.")
    return False


def submit(name: str, url: str, *, dry_run: bool = False):
    """Best-effort submit `name` (Scoring Zone) to the directory at `url`.

    Returns {"outcome": "submitted", "confirmation", "evidence"} on a submit,
    {"outcome": "aborted", "reason"} when the form can't be completed, or "dry".
    Raises only on hard failures (e.g. no STEEL_API_KEY), which the caller
    catches and records as an error.
    """
    print(f"=== Generic submit: {name} → {url} (dry_run={dry_run}) ===")
    slug = _slug(name or url)
    domain = site_credentials.domain_of(url)

    # Session setup: reuse an authenticated profile when we have one; otherwise, if a
    # login is stored in Steel for this domain, enable credential injection and persist
    # the profile so the login only ever happens once.
    profile_id = site_credentials.get_profile_id(domain)
    has_creds = profile_id is not None or site_credentials.has_credentials(domain)
    session_opts: dict = {}
    if profile_id:
        session_opts["profile_id"] = profile_id
        print(f"Reusing authenticated Steel profile for {domain}")
    elif has_creds:
        session_opts["credentials"] = {}
        session_opts["persist_profile"] = True
        print(f"Steel credential injection enabled for {domain}")

    with steel_page(solve_captcha=True, api_timeout_ms=300_000, **session_opts) as (pw, browser, page, client, sid):
        screenshots: list[str] = []

        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        form_found = _wait_for_form(page)
        time.sleep(2)  # let dynamic content / anti-bot settle
        if not wait_for_captchas(client, sid):  # let Steel solve any load-time CAPTCHA
            if not wait_for_captchas(client, sid, timeout_sec=60):  # one retry
                record_session(sid, target=url, mode=MODE, outcome="aborted", screenshots=screenshots)
                return _aborted("CAPTCHA not solved within timeout — submit manually")

        before = Path(f"generic_{slug}_before.png")
        try:
            page.screenshot(path=str(before), full_page=True)
            screenshots.append(before.name)
        except Exception:
            pass

        # Account wall? Log in via Steel's injection if a credential is stored.
        wall = _detect_auth_wall(page)
        logged_in = False
        if wall and has_creds:
            logged_in = _attempt_login(page, client, sid, slug, screenshots)
            if logged_in:
                new_profile = last_session_info().get("profile_id")
                if new_profile and not profile_id:
                    site_credentials.save_profile_id(domain, new_profile)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                form_found = _wait_for_form(page)
                wait_for_captchas(client, sid)

        target = _form_target(page)
        filled, last_filled = _fill_form(target, url)

        try:
            page.screenshot(path=f"generic_{slug}_filled.png", full_page=True)
            screenshots.append(f"generic_{slug}_filled.png")
        except Exception:
            pass

        core_ok = sum(filled.values())
        print(f"Core fields filled: {filled} ({core_ok}/3)")

        if core_ok == 0:
            # Be honest about why we bowed out (rather than posting partial data).
            wall = wall or _detect_auth_wall(page)
            if wall and has_creds and not logged_in:
                reason = f"login failed for {domain} with stored credentials — check Steel credentials / replay"
            elif wall == "signup":
                reason = f"account/signup required — no credentials stored for {domain} (create the account once, then manage_credentials.py add)"
            elif wall:
                reason = f"account required — no credentials stored for {domain} (add via manage_credentials.py)"
            elif not form_found:
                reason = "no form fields found (page may be JS-rendered or not a submission page)"
            else:
                reason = "no core field matched — not a simple submit form"
            record_session(sid, target=url, mode=MODE, outcome="aborted", screenshots=screenshots)
            return _aborted(reason)

        if dry_run:
            print("DRY RUN: filled the form but NOT clicking submit.")
            record_session(sid, target=url, mode=MODE, outcome="dry", screenshots=screenshots)
            return "dry"

        # Click submit — walking simple multi-step wizards (next → next → submit).
        url_before = page.url
        submitted = False
        steps = 0
        captcha_warn = False
        for step in range(4):
            sel = _click_submit(target)
            if not sel and step == 0 and last_filled:
                # No recognizable button — try submitting the form from the last field.
                try:
                    print("No submit button matched — pressing Enter in last filled field.")
                    target.press(last_filled, "Enter")
                    sel = "<Enter key>"
                except Exception:
                    pass
            if not sel:
                break
            submitted = True
            steps += 1
            if not wait_for_captchas(client, sid):  # solve any CAPTCHA the click triggered
                captcha_warn = True
            time.sleep(5)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=30000)
            except Exception:
                pass
            # Another wizard step? Only continue when a genuinely NEW form appeared:
            # our website value must be gone (same form still showing it = the click
            # didn't advance — re-clicking would risk duplicate submissions), and the
            # refill must land substantial fields (website, or name+email) so footer
            # newsletter boxes don't masquerade as a step.
            target = _form_target(page)
            try:
                existing = target.query_selector(_WEBSITE_SELECTORS)
                if existing and (existing.input_value() or "").strip() == WEBSITE:
                    break
            except Exception:
                pass
            more, last_filled = _fill_form(target, url)
            if not (more["website"] or (more["name"] and more["email"])):
                break
            print(f"Multi-step form: step {steps + 1} filled, continuing.")

        result = Path(f"generic_{slug}_result_{int(time.time())}.png")
        try:
            page.screenshot(path=str(result), full_page=True)
            screenshots.append(result.name)
        except Exception:
            pass

        if not submitted:
            record_session(sid, target=url, mode=MODE, outcome="aborted", screenshots=screenshots)
            return _aborted(f"form filled ({core_ok}/3 core fields) but no submit button matched")

        record_session(sid, target=url, mode=MODE, outcome="submitted", screenshots=screenshots)
        # Best-effort: if our backlink is already visible on the result page, record whether
        # it's dofollow/nofollow (disavow-readiness). Usually the listing is reviewed later, so
        # this is often "not yet visible" — the livecheck pass verifies it properly later.
        rel_note = "link not yet visible (listing likely pending review)"
        try:
            link = page.query_selector('a[href*="scoringzone"]')
            if link:
                rel = (link.get_attribute("rel") or "").lower()
                rel_note = "nofollow link" if "nofollow" in rel else "dofollow link"
        except Exception:
            pass
        confirmation, evidence = detect_submission_confirmation(page, url_before=url_before)
        print(f"Confirmation check: {confirmation} ({evidence[:120]})")
        if confirmation == "confirmed":
            status = "Submitted — confirmed"
            conf_note = f"Confirmation: {evidence[:120]}"
        elif confirmation == "error":
            status = "Submitted (error on page — review)"
            conf_note = f"Page showed: {evidence[:120]}"
            send_alert(f"Generic submit to {name} may have FAILED — page showed an error: {evidence[:200]}")
        else:
            status = "Submitted (unconfirmed)"
            conf_note = "No on-page confirmation detected."
        if captcha_warn:
            conf_note += " WARNING: a CAPTCHA was still unsolved after submit — verify via the replay."
        if steps > 1:
            conf_note += f" (multi-step form, {steps} steps)"
        record_submission(name, url, status=status, method="auto",
                          notes=f"Auto-submitted via generic Steel submitter. {rel_note}. {conf_note}",
                          confirmation=confirmation, evidence=evidence[:200])
        print(f"=== Generic submit submitted: {name} ===")
        return {"outcome": "submitted", "confirmation": confirmation, "evidence": evidence[:200]}


def main():
    args = [a for a in sys.argv[1:] if a != "--dry"]
    dry = "--dry" in sys.argv
    if len(args) < 2:
        print('Usage: python submit_generic.py "Name" https://example.com/submit [--dry]')
        return
    print(submit(args[0], args[1], dry_run=dry))


if __name__ == "__main__":
    main()
