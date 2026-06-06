"""
Steel.dev utilities for the Scoring Zone Backlinks project.

Purpose: Reliable browser automation that bypasses CAPTCHAs and anti-bot protections.
Use for:
- Directory form submissions (Eat Sleep Golf, BetaList, F6S, golf roundups, etc.)
- Scraping "best golf apps" lists and resource pages to discover new backlink targets
- Verifying that listings/backlinks are live
- Filling contact / journalist / coach outreach forms
- Large-scale discovery of submission opportunities

This is modeled directly on the proven pattern from Documents/Grok/Competitions/steel_browser.py.

Setup:
1. pip install steel-sdk playwright  (playwright may already be available)
2. Set STEEL_API_KEY in your environment (or .env)
   - Get / manage keys at https://app.steel.dev
3. The provided key (ste-...) should be placed in STEEL_API_KEY.

Usage example (context manager):
    from steel_utils import steel_page, wait_for_captchas

    with steel_page(solve_captcha=True) as (playwright, browser, page, client, session_id):
        page.goto("https://www.eatsleepgolf.net/get-listed")
        # ... interact, fill forms, etc.
        # Steel auto-solves most CAPTCHAs (ReCAPTCHA v2/v3, Cloudflare Turnstile, ImageToText, AWS WAF)
        wait_for_captchas(client, session_id)

You get a live session viewer URL printed for debugging (open it in a browser).

Other Steel capabilities you can use from the client:
- client.scrape(url=...)          → cheap one-shot extraction (html, markdown, cleaned, readability, links) without full session
- client.sessions.captchas.solve(...) for manual control
- Profiles for persistent cookies/auth across sessions (great if you need to stay logged into directories)
- Files API, Extensions, mobile emulation, etc.

See:
- https://docs.steel.dev
- https://github.com/steel-dev/steel-cookbook for agent + Playwright examples
- Captcha docs: https://docs.steel.dev/overview/stealth/captcha-solving
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Generator, Optional
from urllib.parse import urlencode

from dotenv import load_dotenv
from steel import Steel

# NOTE: playwright is imported lazily inside steel_page() (the only place that
# launches a browser). This keeps research / cheap_scrape (used by the daily
# "propose" run) working with just steel-sdk installed — no playwright needed.
# Type names in annotations below are strings thanks to `from __future__ import
# annotations`, so they are never evaluated at import time.

load_dotenv()


def steel_api_key() -> str:
    """Fetch the Steel API key from environment.

    Recommended: export STEEL_API_KEY=ste-... (or put in .env)
    The key you provided can be used directly here.
    """
    key = os.environ.get("STEEL_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "STEEL_API_KEY is missing. "
            "Set it in your environment or .env file "
            "(copy .env.example to .env and fill in your key). "
            "Manage keys at https://app.steel.dev"
        )
    return key


@contextmanager
def steel_page(
    *,
    solve_captcha: bool = True,
    api_timeout_ms: int = 300_000,
    use_proxy: bool = True,
    headless: bool = True,  # Steel sessions are cloud anyway; this affects the local Playwright side
) -> Generator[tuple[Playwright, Browser, Page, Steel, str], None, None]:
    """Context manager that gives you a fresh Steel cloud browser session with CAPTCHA solving.

    Yields: (playwright, browser, page, client, session_id)

    Automatically releases the session on exit (important for credit usage).

    Example:
        with steel_page() as (pw, browser, page, client, sid):
            page.goto("https://example.com")
            # do work
    """
    client = Steel(steel_api_key=steel_api_key())

    session = client.sessions.create(
        solve_captcha=solve_captcha,
        api_timeout=api_timeout_ms,
        use_proxy=use_proxy,
    )
    session_id = session.id

    # The existing pattern prints the viewer URL — extremely useful for debugging submissions
    viewer_url = getattr(session, "session_viewer_url", None)
    if viewer_url:
        print(f"Steel session viewer (open this to watch live): {viewer_url}")
    print(f"Steel session id: {session_id}")

    # Imported here (not at module top) so non-browser flows don't require playwright.
    from playwright.sync_api import sync_playwright

    playwright: Optional["Playwright"] = None
    browser: Optional["Browser"] = None

    try:
        api_key = steel_api_key()
        # Build the CDP WebSocket URL exactly as the proven Competitions pattern does
        ws_url = f"{session.websocket_url}&{urlencode({'apiKey': api_key})}"

        playwright = sync_playwright().start()
        browser = playwright.chromium.connect_over_cdp(ws_url)

        # Get or create a context + page (Steel usually gives one ready context)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()

        yield playwright, browser, page, client, session_id

    finally:
        # Clean up in reverse order
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        if playwright:
            try:
                playwright.stop()
            except Exception:
                pass
        try:
            client.sessions.release(session_id)
            print(f"Released Steel session {session_id}")
        except Exception as e:
            print(f"Warning: could not release session {session_id}: {e}")


def wait_for_captchas(client: Steel, session_id: str, timeout_sec: int = 120) -> None:
    """Poll until Steel reports no active CAPTCHA solving tasks.

    Call this after navigation or actions that are likely to trigger CAPTCHAs.
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            status = client.sessions.captchas.status(session_id)
        except Exception:
            return  # If we can't query, assume we're good or the session is gone

        active = [
            s
            for s in (status or [])
            if getattr(s, "is_solving_captcha", False)
        ]
        if not active:
            return
        time.sleep(1.5)

    print(f"Warning: still had active CAPTCHAs after {timeout_sec}s on session {session_id}")


# ---------------------------------------------------------------------------
# Convenience helpers for the backlinks project
# ---------------------------------------------------------------------------

def cheap_scrape(url: str, *, extract_links: bool = True) -> dict:
    """Use Steel's lightweight scrape endpoint (no full browser session, cheaper).

    Great for initial reconnaissance of many "best golf apps" pages or directory listings
    before deciding to spin up an expensive interactive session.
    """
    client = Steel(steel_api_key=steel_api_key())
    result = client.scrape(url=url)

    data = {
        "url": url,
        "html": getattr(result.content, "html", None),
        "markdown": getattr(result.content, "markdown", None),
        "cleaned_html": getattr(result.content, "cleaned_html", None),
        "readability": getattr(result.content, "readability", None),
        "title": getattr(result.metadata, "title", None),
        "status": getattr(result.metadata, "status_code", None),
    }
    if extract_links and hasattr(result, "links"):
        data["links"] = result.links
    return data


def create_stealth_session(**kwargs):
    """Thin wrapper if you just want the raw Steel session object + client."""
    client = Steel(steel_api_key=steel_api_key())
    session = client.sessions.create(solve_captcha=True, **kwargs)
    return client, session
