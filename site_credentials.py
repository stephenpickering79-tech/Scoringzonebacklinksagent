"""Helpers around Steel's Credentials + Profiles APIs for account-required sites.

Passwords live only in Steel's encrypted credential store (added via
manage_credentials.py) — never in this repo, env vars, logs, or screenshots.
This module only answers "do we have a login for this domain?" and keeps the
non-secret domain → Steel profile_id map so a site is logged into once and the
authenticated browser profile is reused on every later submission.
"""

from __future__ import annotations

import json
import secrets
import string
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent
# Profile ids are opaque references to Steel-side browser profiles — safe to
# persist here (gitignored data/), but never copy into docs/data (published).
_PROFILES_FILE = BASE_DIR / "data" / "steel_profiles.json"
# Audit log of accounts the agent created itself (domain/email/created/verified —
# NEVER the password; that lives only in Steel's encrypted credential store).
_ACCOUNTS_FILE = BASE_DIR / "data" / "accounts.json"


def domain_of(url_or_domain: str) -> str:
    """Normalize a URL or bare domain to a lowercase host without www."""
    s = (url_or_domain or "").strip()
    if not s:
        return ""
    try:
        host = urlparse(s if "://" in s else f"https://{s}").netloc.lower()
    except Exception:
        host = ""
    return host[4:] if host.startswith("www.") else host


def stored_credential_domains(client=None) -> list[str]:
    """Domains that have a login stored in Steel's credential store (origins only)."""
    if client is None:
        from steel import Steel
        from steel_utils import steel_api_key
        client = Steel(steel_api_key=steel_api_key())
    try:
        resp = client.credentials.list()
        creds = getattr(resp, "credentials", None) or []
        return sorted({domain_of(getattr(c, "origin", "") or "") for c in creds} - {""})
    except Exception as e:
        print(f"[site_credentials] could not list Steel credentials: {e}")
        return []


def has_credentials(url_or_domain: str, client=None) -> bool:
    """True if Steel has a stored login whose origin matches this domain (or a parent)."""
    dom = domain_of(url_or_domain)
    if not dom:
        return False
    for stored in stored_credential_domains(client):
        if dom == stored or dom.endswith("." + stored) or stored.endswith("." + dom):
            return True
    return False


# ---------------------------------------------------------------------------
# Profile bookkeeping (domain → Steel profile_id)
# ---------------------------------------------------------------------------

def _load_profiles() -> dict:
    try:
        if _PROFILES_FILE.exists():
            data = json.loads(_PROFILES_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"[site_credentials] could not read {_PROFILES_FILE.name}: {e}")
    return {}


def origin_of(url_or_domain: str) -> str:
    """scheme://host for Steel's credential store (origin-bound injection)."""
    s = (url_or_domain or "").strip()
    p = urlparse(s if "://" in s else f"https://{s}")
    return f"https://{p.netloc}" if p.netloc else ""


def get_profile_id(url_or_domain: str) -> str | None:
    """Steel profile_id of an already-logged-in browser profile for this domain, if any."""
    return _load_profiles().get(domain_of(url_or_domain)) or None


def save_profile_id(url_or_domain: str, profile_id: str) -> None:
    """Remember that `profile_id` holds an authenticated session for this domain."""
    dom = domain_of(url_or_domain)
    if not dom or not profile_id:
        return
    profiles = _load_profiles()
    profiles[dom] = profile_id
    _PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROFILES_FILE.write_text(json.dumps(profiles, indent=2) + "\n", encoding="utf-8")
    print(f"[site_credentials] saved Steel profile for {dom}")


# ---------------------------------------------------------------------------
# Autonomous account creation: generate a password, store the login in Steel's
# encrypted store, and keep a (passwordless) audit trail.
# ---------------------------------------------------------------------------

def generate_password(length: int = 16) -> str:
    """A strong password that satisfies typical signup rules (upper/lower/digit/symbol)."""
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(max(8, length - 4)))
    # Guarantee one of each required class.
    return (secrets.choice(string.ascii_uppercase) + secrets.choice(string.ascii_lowercase)
            + secrets.choice(string.digits) + secrets.choice("!@#$%*?-_") + core)


def store_credential(url_or_domain: str, username: str, password: str, client=None) -> bool:
    """Save a login in Steel's encrypted Credentials API (origin-bound). The password
    never touches disk here — only Steel stores it. Returns True on success."""
    origin = origin_of(url_or_domain)
    if not origin:
        return False
    if client is None:
        from steel import Steel
        from steel_utils import steel_api_key
        client = Steel(steel_api_key=steel_api_key())
    try:
        client.credentials.create(
            origin=origin,
            value={"username": username, "password": password},
            label=domain_of(origin),
        )
        print(f"[site_credentials] stored login in Steel for {origin}")
        return True
    except Exception as e:
        print(f"[site_credentials] could not store credential for {origin}: {e}")
        return False


def _load_accounts() -> dict:
    try:
        if _ACCOUNTS_FILE.exists():
            data = json.loads(_ACCOUNTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"[site_credentials] could not read {_ACCOUNTS_FILE.name}: {e}")
    return {}


def record_account(url_or_domain: str, email: str, *, verified: bool = False) -> None:
    """Append/update the passwordless audit record for an agent-created account."""
    dom = domain_of(url_or_domain)
    if not dom:
        return
    accounts = _load_accounts()
    now = datetime.now().isoformat(timespec="seconds")
    rec = accounts.get(dom, {})
    rec.update({"domain": dom, "email": email, "verified": verified,
                "created_at": rec.get("created_at", now), "updated_at": now})
    accounts[dom] = rec
    _ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2) + "\n", encoding="utf-8")
    print(f"[site_credentials] recorded account for {dom} (verified={verified})")


def mark_verified(url_or_domain: str) -> None:
    rec = _load_accounts().get(domain_of(url_or_domain))
    if rec:
        record_account(url_or_domain, rec.get("email", ""), verified=True)
