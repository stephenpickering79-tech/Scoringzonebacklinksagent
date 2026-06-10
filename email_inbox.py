"""Read the agent's contact inbox to complete email-verification steps autonomously.

Directories almost always send a "confirm your email / activate your account" link
after signup. Without clicking it the listing never goes live. This module connects
to Gmail over IMAP (one app-password secret, set once as IMAP_PASSWORD on Railway —
NOT per-site credentials) and returns the verification link for a given site so the
submitter can open it in the same Steel session.

Env:
    IMAP_PASSWORD   Gmail app password (myaccount.google.com/apppasswords). Required.
    IMAP_EMAIL      mailbox address (default: submission_profile.CONTACT_EMAIL).
    IMAP_HOST       default imap.gmail.com.

Nothing here ever logs the password or full email bodies.
"""

from __future__ import annotations

import email
import imaplib
import os
import re
import time
from email.header import decode_header
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

try:  # ensure .env is loaded even if this module is used standalone
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")

# Links worth clicking — verification/activation flows use these tokens in the path
# or query. Used to rank candidate links found in an email body.
_VERIFY_HINTS = ("verify", "confirm", "activate", "validate", "verification",
                 "confirmation", "activation", "verify-email", "email-confirm",
                 "token=", "code=", "auth", "magiclink", "magic-link")
_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_URL_RE = re.compile(r'https?://[^\s"\'<>)]+', re.IGNORECASE)
# Verification codes ("Enter the following code: 305037", "Your code is 123456",
# "verification code: A1B2C3"). We locate a code keyword, then take the first
# code-shaped token in the ~40 chars after it. A valid token is 4-8 chars, all
# DIGITS or UPPERCASE-alphanumeric-with-a-digit (so the English word "code" or
# "website" can't be mistaken for a code).
_CODE_KEYWORD_RE = re.compile(r'(code|otp|\bpin\b|one[\s-]?time\s*(?:code|password|pin)?)',
                              re.IGNORECASE)
_CODE_TOKEN_RE = re.compile(r'\b([0-9]{4,8}|[A-Z0-9]{4,8})\b')


def _looks_like_code(tok: str) -> bool:
    if not (4 <= len(tok) <= 8):
        return False
    if tok.isdigit():
        return True
    # Alphanumeric: must be all-uppercase-or-digit AND contain at least one digit
    # (rules out plain words like CODE, EMAIL).
    return tok == tok.upper() and any(c.isdigit() for c in tok) and tok.isalnum()
# Links to ignore even if they sit in the email (footers, social, unsubscribe).
_LINK_BLOCKLIST = ("unsubscribe", "/privacy", "/terms", "twitter.com", "x.com",
                   "facebook.com", "linkedin.com", "instagram.com", "youtube.com",
                   "apple.com", "play.google.com", "mailto:", "support@", "/help")


def imap_password() -> str:
    # Gmail shows app passwords as "abcd efgh ijkl mnop"; the real value is the 16
    # chars without spaces — strip ALL whitespace so a pasted-with-spaces value works.
    pw = re.sub(r"\s+", "", os.getenv("IMAP_PASSWORD", ""))
    if not pw:
        raise RuntimeError(
            "IMAP_PASSWORD is not set — the agent can't read verification emails. "
            "Generate a Gmail app password at myaccount.google.com/apppasswords and "
            "set IMAP_PASSWORD (locally in .env, and on Railway).")
    return pw


def _mailbox() -> str:
    addr = os.getenv("IMAP_EMAIL", "").strip()
    if addr:
        return addr
    try:
        from submission_profile import CONTACT_EMAIL
        return CONTACT_EMAIL
    except Exception:
        return ""


def is_configured() -> bool:
    return bool(os.getenv("IMAP_PASSWORD", "").strip())


# Second-level public suffixes ('scoringzone.co.uk' style) — the label is one left of these.
_TLD2 = ("co", "com", "net", "org", "ac", "gov", "edu")


def _registrable_label(domain: str) -> str:
    """'www.producthunt.com' -> 'producthunt' — the word that ties an email to a site.
    Strips by POSITION (the TLD is the last part), never by token value: filtering any
    part that merely looks like a TLD ate the site name on short domains ('dev.to'
    became 'to')."""
    host = (domain or "").lower().split("/")[0]
    parts = [p for p in host.split(".") if p and p != "www"]
    if not parts:
        return host
    if len(parts) >= 3 and parts[-2] in _TLD2:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


def _decode(s) -> str:
    if not s:
        return ""
    out = []
    for chunk, enc in decode_header(s):
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _body_text(msg) -> str:
    """Concatenate text/plain + text/html parts (decoded best-effort)."""
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "text/html"):
                try:
                    payload = part.get_payload(decode=True) or b""
                    parts.append(payload.decode(part.get_content_charset() or "utf-8",
                                                errors="replace"))
                except Exception:
                    continue
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            parts.append(payload.decode(msg.get_content_charset() or "utf-8",
                                        errors="replace"))
        except Exception:
            pass
    return "\n".join(parts)


def _candidate_links(body: str) -> list[str]:
    links = _HREF_RE.findall(body) + _URL_RE.findall(body)
    seen, out = set(), []
    for raw in links:
        u = raw.strip().rstrip(".,)\"'>")
        low = u.lower()
        if not low.startswith("http"):
            continue
        if any(b in low for b in _LINK_BLOCKLIST):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _best_link(body: str, domain_label: str) -> str | None:
    """Pick the most likely verification link: prefer ones with a verify-style token,
    then ones pointing at the same site."""
    links = _candidate_links(body)
    if not links:
        return None

    def score(u: str) -> int:
        low = u.lower()
        s = 0
        if any(h in low for h in _VERIFY_HINTS):
            s += 10
        if domain_label and domain_label in low:
            s += 3
        # A long token-bearing query is a strong signal.
        if len(urlparse(u).query) > 20:
            s += 2
        return s

    ranked = sorted(links, key=score, reverse=True)
    return ranked[0] if score(ranked[0]) > 0 else None


def _best_code(subject: str, body: str) -> str | None:
    """Extract a verification code (e.g. 2Captcha's 'Enter the following code: 305037').
    Looks for a code-shaped token in the window after a code keyword."""
    for text in (subject, body):
        if not text:
            continue
        for kw in _CODE_KEYWORD_RE.finditer(text):
            window = text[kw.end(): kw.end() + 40]
            for tok in _CODE_TOKEN_RE.findall(window):
                if _looks_like_code(tok):
                    return tok
    return None


def _email_matches_site(from_addr: str, subject: str, body: str, domain_label: str) -> bool:
    """Is this message plausibly the verification email for `domain_label`? Match on
    sender/subject/body mention, or any in-body link pointing at the site. Generic
    'verify your email' subjects from a transactional sender still match via the link."""
    hay = f"{from_addr}\n{subject}\n{body[:4000]}".lower()
    if domain_label and domain_label in hay:
        return True
    subj = subject.lower()
    return any(h in subj for h in ("verify", "confirm", "activate", "verification",
                                   "confirm your email", "activate your account"))


def find_verification(site_url_or_domain: str, *, since: datetime | None = None,
                      timeout_sec: int = 180, poll_sec: int = 12) -> dict:
    """Poll the inbox for the verification email tied to `site_url_or_domain`.

    `since` bounds the search to mail received around/after signup (defaults to the
    last 30 min) so an old email can't be re-used. Returns {"link": str|None,
    "code": str|None} — sites use one or the other. Never raises (logs and returns
    empties) except for a missing IMAP_PASSWORD.
    """
    pw = imap_password()
    mailbox = _mailbox()
    if not mailbox:
        print("[email_inbox] no mailbox address (set IMAP_EMAIL or submission_profile.CONTACT_EMAIL)")
        return {"link": None, "code": None}
    domain = urlparse(site_url_or_domain if "://" in site_url_or_domain
                      else f"https://{site_url_or_domain}").netloc or site_url_or_domain
    label = _registrable_label(domain)
    if since is None:
        since = datetime.now(timezone.utc) - timedelta(minutes=30)
    # 2-min grace before the per-message datetime filter (_scan_once) so a small
    # clock/timezone/delivery skew can't drop a genuinely-new verification email.
    since = since - timedelta(minutes=2)
    since_str = since.strftime("%d-%b-%Y")

    deadline = time.time() + timeout_sec
    print(f"[email_inbox] waiting for {label!r} verification email (up to {timeout_sec}s)…")
    while time.time() < deadline:
        try:
            found = _scan_once(mailbox, pw, since_str, since, label) or {}
            if found.get("link") or found.get("code"):
                print(f"[email_inbox] verification found for {label} "
                      f"({'link' if found.get('link') else 'code'})")
                return found
        except Exception as e:
            print(f"[email_inbox] scan error: {type(e).__name__}: {e}")
        time.sleep(poll_sec)
    print(f"[email_inbox] no verification email for {label} within {timeout_sec}s")
    return {"link": None, "code": None}


def find_verification_link(site_url_or_domain: str, **kwargs) -> str | None:
    """Convenience wrapper returning just the link (backwards-compatible)."""
    return find_verification(site_url_or_domain, **kwargs).get("link")


def _scan_once(mailbox: str, pw: str, since_str: str, since_dt: datetime,
               label: str) -> dict:
    conn = imaplib.IMAP4_SSL(_HOST)
    try:
        conn.login(mailbox, pw)
        conn.select("INBOX")
        typ, data = conn.search(None, "SINCE", since_str)
        if typ != "OK" or not data or not data[0]:
            return {"link": None, "code": None}
        ids = data[0].split()
        # newest first
        for mid in reversed(ids[-40:]):
            typ, msg_data = conn.fetch(mid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            # Skip messages older than the signup window (SINCE is day-granular).
            try:
                msg_dt = parsedate_to_datetime(msg.get("Date"))
                if msg_dt and msg_dt.tzinfo and msg_dt < since_dt:
                    continue
            except Exception:
                pass
            from_addr = _decode(msg.get("From"))
            subject = _decode(msg.get("Subject"))
            body = _body_text(msg)
            if not _email_matches_site(from_addr, subject, body, label):
                continue
            link = _best_link(body, label)
            code = _best_code(subject, body)
            if link or code:
                try:  # mark read so we don't re-open it on a later poll
                    conn.store(mid, "+FLAGS", "\\Seen")
                except Exception:
                    pass
                return {"link": link, "code": code}
        return {"link": None, "code": None}
    finally:
        try:
            conn.logout()
        except Exception:
            pass
