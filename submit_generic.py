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
from datetime import datetime, timezone
from pathlib import Path

import email_inbox
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
    'input[name*="email" i], input[type="email"], input[placeholder*="email" i], '
    'input[name="user[email]"], input[id*="email" i]'
)

# Brand assets for forms that want a logo/screenshot upload (never gated on).
_ASSETS_DIR = Path(__file__).parent / "assets"

_LOGIN_LINK_SELECTORS = [
    'a[href*="login" i]', 'a[href*="signin" i]', 'a[href*="sign-in" i]', 'a[href*="sign_in" i]',
    'a:has-text("Log in")', 'a:has-text("Login")', 'a:has-text("Sign in")',
]
_SIGNUP_LINK_SELECTORS = [
    'a[href*="signup" i]', 'a[href*="sign-up" i]', 'a[href*="sign_up" i]', 'a[href*="register" i]',
    'a[href*="join" i]', 'a:has-text("Sign up")', 'a:has-text("Sign Up")',
    'a:has-text("Register")', 'a:has-text("Create account")', 'a:has-text("Get started")',
    'a:has-text("Join")',
]
_LOGIN_TEXT_PATTERNS = ("sign in to", "log in to", "must be logged in", "members only")
_SIGNUP_TEXT_PATTERNS = ("create an account to", "sign up to", "register to", "join to submit")
_LOGIN_URL_RE = re.compile(r"/(login|signin|sign-in|sign_in|signup|sign-up|sign_up|register)\b", re.I)
_LOGIN_FAIL_PATTERNS = ("incorrect password", "invalid login", "invalid email or password",
                        "wrong password", "try again")
# OAuth-only gates we cannot self-register through (no email/password form).
# Any "<verb> with <provider>" is an OAuth button — a provider whitelist kept missing
# entries (dev.to has "Continue with MyMLH"). Only "with email/password" stays clickable.
_OAUTH_ONLY_RE = re.compile(
    r"(sign\s*(in|up)|log\s*in|continue|register)\s+with\s+(?!e-?mail|password|username)\S+",
    re.IGNORECASE)
# Ordered most-specific first. "Create account"/submit types before the generic
# "Sign up" text (which also matches OAuth "Sign up with Google" buttons — those are
# additionally filtered out by the OAuth-text guard in _click_signup_submit()).
_SIGNUP_SUBMIT_SELECTORS = [
    'button:has-text("Create account")', 'button:has-text("Create Account")',
    'button:has-text("Create my account")', 'button:has-text("Create free account")',
    'button[type="submit"]', 'input[type="submit"]',
    'button:has-text("Sign up")', 'button:has-text("Sign Up")',
    'button:has-text("Register")', 'button:has-text("Join")',
    'button:has-text("Get started")', 'button:has-text("Continue")', 'form button',
]
_PASSWORD_SELECTORS = ('input[type="password"]:not([name*="confirm" i]):not([id*="confirm" i])'
                       ':not([placeholder*="confirm" i]):not([name*="repeat" i])')
_CONFIRM_PW_SELECTORS = ('input[type="password"][name*="confirm" i], input[type="password"][id*="confirm" i], '
                         'input[type="password"][placeholder*="confirm" i], input[type="password"][name*="repeat" i]')
# name$="[name]" catches Rails-nested fields (user[name]) that the :not([name*="user"])
# guard would otherwise exclude; it cannot match user[username] (no "[name]" substring).
_FULLNAME_SELECTORS = ('input[name$="[name]" i], '
                       'input[name*="name" i]:not([name*="company" i]):not([name*="user" i]), '
                       'input[id*="fullname" i], input[placeholder*="full name" i], '
                       'input[placeholder*="your name" i], input[autocomplete="name"]')
# Precise username matches only — a bare name*="user" matches EVERY field of a
# Rails-style form (user[name], user[password], ...) and filled the wrong one.
_USERNAME_SELECTORS = ('input[name*="username" i]:not([type="email"]), '
                       'input[id*="username" i], input[autocomplete="username"]:not([type="email"])')


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


_SIGNUP_URL_RE = re.compile(r"/(signup|sign-up|sign_up|register|join|create-account)\b", re.I)


def _detect_auth_wall(page) -> str | None:
    """Return "login" / "signup" when the page demands an account, else None.

    A visible email+password pair (or two password fields) is treated as a signup
    form even when the page has no keyword text — this is what makes JS-rendered
    Rails/Devise signup pages reliably trigger self-registration."""
    try:
        url = page.url or ""
        # A rendered signup form: email + password, or password + confirm-password.
        has_pw = bool(page.query_selector('input[type="password"]'))
        n_pw = len(page.query_selector_all('input[type="password"]'))
        has_email = bool(page.query_selector('input[type="email"], input[name*="email" i]'))
        if (has_pw and has_email and _SIGNUP_URL_RE.search(url)) or n_pw >= 2:
            return "signup"

        if _SIGNUP_URL_RE.search(url):
            return "signup"
        if _LOGIN_URL_RE.search(url):
            return "login"

        txt = (page.inner_text("body")[:3000] or "").lower()
        if any(p in txt for p in _SIGNUP_TEXT_PATTERNS):
            return "signup"
        if any(p in txt for p in _LOGIN_TEXT_PATTERNS):
            return "login"
        if has_pw and has_email:
            return "signup"
        if has_pw:
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


def _has_password_field(page, *, wait_ms: int = 0) -> bool:
    """Password field present now, optionally waiting up to wait_ms for it to mount
    (JS-rendered signup forms often appear a beat after load)."""
    if wait_ms:
        try:
            page.wait_for_selector('input[type="password"]', timeout=wait_ms)
        except Exception:
            pass
    try:
        return bool(page.query_selector('input[type="password"]'))
    except Exception:
        return False


def _open_signup(page) -> bool:
    """Navigate to a signup form if we're not already on one. Returns True once a
    password field is present (i.e. a self-serve email/password signup)."""
    # Wait for a JS-rendered form on the current page before deciding to navigate.
    if _has_password_field(page, wait_ms=8000):
        return True
    for sel in _SIGNUP_LINK_SELECTORS:
        try:
            link = page.query_selector(sel)
            if link and link.is_visible():
                print(f"Opening signup via: {sel}")
                link.click()
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                if _has_password_field(page, wait_ms=8000):
                    return True
        except Exception:
            continue
    return _has_password_field(page, wait_ms=3000)


def _click_signup_submit(page) -> bool:
    """Click the real account-creation button, skipping OAuth ("...with Google") buttons
    that also match a 'Sign up' text selector. Returns True if a button was clicked.

    First pass is scoped to the form holding the password field — pages also carry
    search/newsletter forms whose submit buttons must never be the click target."""
    for scope in ('form:has(input[type="password"]) ', ''):
        for sel in _SIGNUP_SUBMIT_SELECTORS:
            try:
                for btn in page.query_selector_all(scope + sel):
                    if not btn.is_visible():
                        continue
                    # input[type=submit] has no inner text — its label is the value attr.
                    txt = ((btn.inner_text() or "").strip()
                           or (btn.get_attribute("value") or "").strip())
                    if _OAUTH_ONLY_RE.search(txt):  # "Sign up/Continue with Google/Apple…"
                        continue
                    print(f"Submitting signup: {scope + sel} ({txt[:30]!r})")
                    btn.click()
                    return True
            except Exception:
                continue
    return False


_CONSENT_RE = re.compile(r"term|agree|privacy|accept|consent|polic|tos|rules|gdpr", re.I)


def _checkbox_context_text(box) -> str:
    """name/id/aria-label PLUS the associated label / wrapper text — custom-styled
    consent boxes rarely put 'terms' in the input's own attributes."""
    try:
        return (box.evaluate(
            """el => {
                const bits = [el.name || '', el.id || '', el.getAttribute('aria-label') || ''];
                if (el.id) {
                    const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
                    if (lab) bits.push(lab.innerText || '');
                }
                const wrap = el.closest('label') || el.parentElement;
                if (wrap) bits.push((wrap.innerText || '').slice(0, 200));
                return bits.join(' ');
            }""") or "")
    except Exception:
        return ""


def _tick_checkbox(box) -> bool:
    """Tick a checkbox, surviving the custom-styled pattern where the real input is
    hidden behind a span (check() fails on invisible elements) — click its label or
    force the state + change event instead."""
    try:
        if box.is_checked():
            return True
    except Exception:
        pass
    try:
        box.check(timeout=2000)
        return True
    except Exception:
        pass
    try:
        box.evaluate(
            """el => {
                const lab = (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'))
                            || el.closest('label');
                if (lab) lab.click(); else el.click();
                if (!el.checked) {
                    el.checked = true;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
            }""")
        return bool(box.is_checked())
    except Exception:
        return False


def _accept_signup_checkboxes(page, require_keyword: bool = True) -> int:
    """Tick consent checkboxes; returns how many were ticked. With
    require_keyword=False every unchecked box on the form is ticked — the
    rejected-signup fallback (an extra newsletter opt-in beats a failed signup)."""
    ticked = 0
    try:
        boxes = page.query_selector_all('input[type="checkbox"]')
    except Exception:
        return 0
    for box in boxes:
        try:
            if box.is_checked():
                continue
            if require_keyword and not _CONSENT_RE.search(_checkbox_context_text(box)):
                continue
            if _tick_checkbox(box):
                ticked += 1
        except Exception:
            continue
    return ticked


def _signup_rejection(page) -> str:
    """'' if the signup looks accepted; otherwise why it appears rejected."""
    try:
        err_el = page.query_selector(
            '[class*="error" i], [class*="invalid" i], [role="alert"], .field_with_errors')
        if err_el and err_el.is_visible():
            etext = (err_el.inner_text() or "").strip()[:160]
            if etext:
                return f"signup rejected: {etext}"
    except Exception:
        pass
    try:
        if page.query_selector('input[type="password"]'):
            return "signup form still present after submit — registration not accepted"
    except Exception:
        pass
    return ""


def _auto_register(page, url: str, domain: str, client, sid: str, slug: str,
                   screenshots: list) -> dict:
    """Autonomously create an account: generate a password, fill + submit the signup
    form, store the login in Steel, complete any email verification, and end logged in.
    The account password is only ever held by Steel.

    Returns a result dict: {logged_in, registered, credential_stored, verification
    ("link"|"code"|None), verified, reason}."""
    result = {"logged_in": False, "registered": False, "credential_stored": False,
              "verification": None, "verified": False, "reason": ""}
    # OAuth-only walls (no email/password form) can't be self-registered.
    try:
        body = (page.inner_text("body")[:3000] or "")
        if _OAUTH_ONLY_RE.search(body) and not page.query_selector('input[type="password"]') \
                and not _open_signup(page):
            print("Signup is OAuth-only — cannot self-register.")
            result["reason"] = "OAuth-only signup (no email/password form)"
            return result
    except Exception:
        pass

    if not _open_signup(page):
        print("No email/password signup form found.")
        result["reason"] = "no email/password signup form found"
        return result

    password = site_credentials.generate_password()
    signup_started = datetime.now(timezone.utc)

    def fill(sel, val):
        try:
            page.fill(sel, val, timeout=4000)
            return True
        except Exception:
            return False

    fill(_EMAIL_SELECTORS, CONTACT_EMAIL)
    fill(_FULLNAME_SELECTORS, CONTACT_NAME)
    fill(_USERNAME_SELECTORS, "scoringzone")
    fill(_PASSWORD_SELECTORS, password)
    fill(_CONFIRM_PW_SELECTORS, password)
    # Accept terms / consent checkboxes — required to submit on many signups.
    if not _accept_signup_checkboxes(page):
        # No box matched a consent keyword: tick whatever is there rather than
        # submit with a required agreement unticked.
        _accept_signup_checkboxes(page, require_keyword=False)

    try:
        page.screenshot(path=f"generic_{slug}_signup.png", full_page=True)
        screenshots.append(f"generic_{slug}_signup.png")
    except Exception:
        pass

    clicked = _click_signup_submit(page)
    if not clicked:
        print("No signup submit button found.")
        result["reason"] = "filled signup form but no submit button matched"
        return result

    result["registered"] = True
    if not wait_for_captchas(client, sid):  # an on-form CAPTCHA may block the submit
        wait_for_captchas(client, sid, timeout_sec=60)
    time.sleep(4)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    # Capture the post-submit state + any visible validation error (a signup is often
    # rejected here for an unticked terms box, weak password, or an unsolved on-form CAPTCHA).
    try:
        page.screenshot(path=f"generic_{slug}_postsubmit.png", full_page=True)
        screenshots.append(f"generic_{slug}_postsubmit.png")
    except Exception:
        pass
    result["reason"] = _signup_rejection(page)
    if result["reason"]:
        # One retry: a rejected signup that left the form on screen is usually an
        # unticked consent box, or an on-form CAPTCHA solved *after* the click.
        # Tick everything, re-fill anything the site blanked, re-click once.
        print(f"[auto_register] {result['reason']} — retrying once")
        _accept_signup_checkboxes(page, require_keyword=False)
        fill(_EMAIL_SELECTORS, CONTACT_EMAIL)
        fill(_PASSWORD_SELECTORS, password)
        fill(_CONFIRM_PW_SELECTORS, password)
        if _click_signup_submit(page):
            wait_for_captchas(client, sid)
            time.sleep(4)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            result["reason"] = _signup_rejection(page)
            try:
                page.screenshot(path=f"generic_{slug}_postsubmit2.png", full_page=True)
                screenshots.append(f"generic_{slug}_postsubmit2.png")
            except Exception:
                pass
    if result["reason"]:
        print(f"[auto_register] {result['reason']}")

    # Store the login in Steel immediately — even if verification is still pending,
    # the credential is now reusable and Steel can auto-inject it on future logins.
    result["credential_stored"] = site_credentials.store_credential(
        url, CONTACT_EMAIL, password, client=client)
    site_credentials.record_account(url, CONTACT_EMAIL, verified=False)

    # Email verification, if the inbox secret is configured. Sites use either a
    # click-link or a numeric code typed back into the page — handle both.
    if email_inbox.is_configured():
        found = email_inbox.find_verification(url, since=signup_started, timeout_sec=180)
        link, code = found.get("link"), found.get("code")
        # The session may have hit its timeout during the email wait — a dead page
        # must degrade to "verification incomplete", never crash the whole submit.
        try:
            code_field = page.query_selector(
                'input[name*="code" i], input[id*="code" i], input[name*="otp" i], '
                'input[autocomplete="one-time-code"], input[placeholder*="code" i]')
        except Exception as e:
            print(f"[auto_register] page gone after email wait ({type(e).__name__}) — "
                  "cannot finish on-page verification.")
            code_field = None
        if code and code_field:
            try:
                print("Entering verification code from email.")
                result["verification"] = "code"
                code_field.fill(code, timeout=4000)
                for sel in ("button:has-text('Verify')", "button:has-text('Confirm')",
                            "button:has-text('Submit')", 'button[type="submit"]', "form button"):
                    btn = page.query_selector(sel)
                    if btn and btn.is_visible():
                        btn.click()
                        break
                wait_for_captchas(client, sid)
                time.sleep(3)
                site_credentials.mark_verified(url)
                result["verified"] = True
            except Exception as e:
                print(f"Could not enter verification code: {e}")
        elif link:
            try:
                print("Visiting verification link.")
                result["verification"] = "link"
                page.goto(link, wait_until="domcontentloaded", timeout=45000)
                wait_for_captchas(client, sid)
                time.sleep(3)
                site_credentials.mark_verified(url)
                result["verified"] = True
            except Exception as e:
                print(f"Could not open verification link: {e}")
        else:
            print("[auto_register] no verification link/code arrived in time.")
    else:
        print("[auto_register] IMAP_PASSWORD not set — skipping email verification step.")

    # Back to the site; consider it done if we're no longer behind an auth wall.
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        _wait_for_form(page)
    except Exception:
        pass
    try:
        result["logged_in"] = _looks_logged_in(page)
    except Exception:
        result["logged_in"] = False
    print(f"Auto-register {'succeeded' if result['logged_in'] else 'incomplete (may need email verification)'}.")
    return result


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
    else:
        # No login yet — persist the profile so an auto-created account stays logged in.
        session_opts["persist_profile"] = True

    # 15 min: auto-registration alone can spend ~5 (form + CAPTCHAs + 180s email wait)
    # and the submission itself still has to happen in the same session.
    with steel_page(solve_captcha=True, api_timeout_ms=900_000, **session_opts) as (pw, browser, page, client, sid):
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

        # Account wall? Log in with a stored credential, or autonomously create an
        # account (generate password → register → store in Steel → verify email).
        wall = _detect_auth_wall(page)
        logged_in = False
        if wall and has_creds:
            logged_in = _attempt_login(page, client, sid, slug, screenshots)
        elif wall and not dry_run:
            reg = _auto_register(page, url, domain, client, sid, slug, screenshots)
            logged_in = reg.get("logged_in", False)
            if reg.get("registered"):
                has_creds = True  # account now exists for the abort-reason logic below
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
            if wall and dry_run:
                reason = f"account required for {domain} — would auto-create an account on a live (non-dry) run"
            elif wall and not logged_in and not has_creds:
                reason = (f"account required for {domain} — could not self-register "
                          "(OAuth-only signup, or signup form not automatable / email "
                          "verification didn't arrive); handle manually")
            elif wall and not logged_in:
                reason = f"login failed for {domain} with stored credentials — check Steel credentials / replay"
            elif wall:
                reason = (f"created/logged into an account for {domain} but the submission form "
                          "still didn't load — may need email verification or manual review")
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
