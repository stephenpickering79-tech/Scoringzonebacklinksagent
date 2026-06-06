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

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Session recording (for the dashboard)
# ---------------------------------------------------------------------------

_SESSIONS_LOG = Path(__file__).parent / "data" / "sessions.jsonl"


def record_session(
    session_id: str,
    *,
    viewer_url: str | None = None,
    target: str | None = None,
    mode: str | None = None,
    outcome: str = "started",
    screenshots: list | None = None,
) -> None:
    """Append one JSON line recording a Steel session to data/sessions.jsonl.

    Best-effort: never raises (a logging failure must not break a submission).
    The dashboard generator (backlink_agent/dashboard.py) reads this file,
    collapses lines by session_id, and redacts viewer_url / truncates the id
    before publishing to the public docs/data/sessions.json.

    outcome ∈ {"started", "submitted", "aborted", "error"}.
    """
    try:
        _SESSIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "session_id": session_id,
            "viewer_url": viewer_url,
            "target": target,
            "mode": mode,
            "timestamp": datetime.now().isoformat(),
            "screenshots": screenshots or [],
            "outcome": outcome,
        }
        with open(_SESSIONS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:  # pragma: no cover - logging must never break a run
        print(f"Warning: could not record session {session_id}: {e}")


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

    # Record the session opening so even a crashed/aborted run leaves a trail.
    # Callers (submit_*.py) enrich this with target/mode/outcome at the end.
    record_session(session_id, viewer_url=viewer_url, outcome="started")

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
