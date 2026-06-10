"""Helpers around Steel's Credentials + Profiles APIs for account-required sites.

Passwords live only in Steel's encrypted credential store (added via
manage_credentials.py) — never in this repo, env vars, logs, or screenshots.
This module only answers "do we have a login for this domain?" and keeps the
non-secret domain → Steel profile_id map so a site is logged into once and the
authenticated browser profile is reused on every later submission.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).parent
# Profile ids are opaque references to Steel-side browser profiles — safe to
# persist here (gitignored data/), but never copy into docs/data (published).
_PROFILES_FILE = BASE_DIR / "data" / "steel_profiles.json"


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
