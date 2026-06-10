#!/usr/bin/env python3
"""Deterministic test harness for the autonomous account-creation + email-verification loop.

Exercises ONLY the novel capability — create an account on a signup page and complete
email verification — decoupled from "find a submission form", so a pass is unambiguous.
It drives the real production code path (`submit_generic._auto_register`), so a green run
here means the live submitter's account-creation works too.

Usage:
    python3 test_autonomous_signup.py "<Name>" <signup_url>
    python3 test_autonomous_signup.py --diagnose <url1> <url2> ...   # just probe pages

Requires STEEL_API_KEY and (for the verification stage) IMAP_PASSWORD in the environment.
Reads the contact inbox from submission_profile.CONTACT_EMAIL (scoringzone01@gmail.com).
"""

from __future__ import annotations

import sys
import time

import email_inbox
import site_credentials
from steel_utils import steel_page, wait_for_captchas, last_session_info
from submit_generic import (_detect_auth_wall, _open_signup, _auto_register, _slug,
                            _OAUTH_ONLY_RE)


def _row(ok: bool | None, label: str, detail: str = "") -> None:
    mark = "✅ PASS" if ok else ("— SKIP" if ok is None else "❌ FAIL")
    print(f"  {mark}  {label}" + (f" — {detail}" if detail else ""))


def diagnose(urls: list[str]) -> None:
    """Read-only probe: does each page render an email/password form to Steel?"""
    print("=== Signup-target diagnostic ===")
    for url in urls:
        try:
            with steel_page(solve_captcha=True, api_timeout_ms=120_000) as (pw, b, page, client, sid):
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                wait_for_captchas(client, sid)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                try:
                    page.wait_for_selector('input[type="password"]', timeout=8000)
                except Exception:
                    pass
                low = (page.content() or "").lower()
                email_f = bool(page.query_selector('input[type="email"], input[name*="email" i]'))
                pw_f = bool(page.query_selector('input[type="password"]'))
                blocked = any(b in low for b in ("just a moment", "attention required",
                                                 "cf-chl", "enable javascript and cookies"))
                oauth = bool(_OAUTH_ONLY_RE.search(low)) and not pw_f
                verdict = "GOOD" if (email_f and pw_f and not blocked) else "skip"
                print(f"[{verdict}] {url}\n     email={email_f} password={pw_f} "
                      f"blocked={blocked} oauth_only={oauth} final={page.url[:70]}")
        except Exception as e:
            print(f"[err ] {url} — {type(e).__name__}: {str(e)[:80]}")


def run_test(name: str, url: str) -> bool:
    domain = site_credentials.domain_of(url)
    slug = _slug(name or url)
    print(f"=== Autonomous signup test: {name} → {url} ===")
    print(f"    inbox: {email_inbox._mailbox()}  IMAP configured: {email_inbox.is_configured()}")

    # 15 min: the 180s email wait comes on top of form work + CAPTCHA solves; a 5 min
    # session dies mid-wait and kills the verification stage.
    with steel_page(solve_captcha=True, api_timeout_ms=900_000, persist_profile=True) as (pw, b, page, client, sid):
        screenshots: list[str] = []
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        wait_for_captchas(client, sid)

        wall = _detect_auth_wall(page)
        form_ok = _open_signup(page)
        print("\n--- Stages ---")
        _row(bool(wall), "Auth wall detected", str(wall))
        _row(form_ok, "Signup form loaded (email+password present)")
        if not form_ok:
            _row(False, "Cannot proceed — no signup form rendered")
            return False

        # Drive the REAL production registration path.
        reg = _auto_register(page, url, domain, client, sid, slug, screenshots)

        _row(reg.get("registered"), "Signup submitted", reg.get("reason") or "")
        _row(reg.get("credential_stored"),
             "Credential stored in Steel",
             "verified via credentials.list()" if site_credentials.has_credentials(domain) else "")
        vk = reg.get("verification")
        _row(None if not email_inbox.is_configured() else bool(vk),
             "Verification email read",
             f"{vk} found" if vk else ("IMAP not configured" if not email_inbox.is_configured()
                                       else "no link/code arrived in 180s"))
        _row(reg.get("verified"), "Email verification completed",
             "account marked verified" if reg.get("verified") else "")

        acct = site_credentials._load_accounts().get(domain, {})
        print("\n--- Evidence ---")
        print(f"  Steel session: {last_session_info().get('viewer_url') or sid}")
        print(f"  Stored login for domain in Steel: {site_credentials.has_credentials(domain)}")
        print(f"  data/accounts.json[{domain}]: {acct}")
        print(f"  Screenshots: {screenshots}")

        # Overall: the core capability is account created + credential stored; verification
        # is a PASS when IMAP is configured and it completed (or a SKIP when not configured).
        core_ok = bool(reg.get("registered") and reg.get("credential_stored"))
        verify_ok = (not email_inbox.is_configured()) or reg.get("verified")
        overall = core_ok and verify_ok
        print(f"\n=== RESULT: {'PASS ✅' if overall else 'FAIL ❌'} ===")
        return overall


def main(argv: list[str]) -> None:
    if argv and argv[0] == "--diagnose":
        diagnose(argv[1:])
        return
    if len(argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    ok = run_test(argv[0], argv[1])
    raise SystemExit(0 if ok else 2)


if __name__ == "__main__":
    main(sys.argv[1:])
