#!/usr/bin/env python3
"""Store site logins in Steel's encrypted credential store (never in this repo/env).

Steel injects these into browser sessions automatically: when a login form for the
matching origin appears, Steel fills and submits it server-side with the fields
blurred — the password never reaches the agent, logs, or screenshots.

Usage:
    python3 manage_credentials.py add https://www.producthunt.com
    python3 manage_credentials.py list
    python3 manage_credentials.py delete https://www.producthunt.com

One-time flow for an account-required directory: create the account in your own
browser once, then `add` the login here — the agent handles every submission after.
"""

from __future__ import annotations

import sys
from getpass import getpass
from urllib.parse import urlparse

from steel import Steel

from steel_utils import steel_api_key


def _origin(url: str) -> str:
    p = urlparse(url if "://" in url else f"https://{url}")
    if not p.netloc:
        raise SystemExit(f"Not a valid site URL: {url!r}")
    return f"https://{p.netloc}"


def cmd_add(client: Steel, url: str) -> None:
    origin = _origin(url)
    username = input(f"Username/email for {origin}: ").strip()
    password = getpass(f"Password for {origin} (hidden): ")
    if not username or not password:
        raise SystemExit("Both username and password are required.")
    client.credentials.create(
        origin=origin,
        value={"username": username, "password": password},
        label=urlparse(origin).netloc,
    )
    print(f"Stored credential for {origin} in Steel (encrypted, org-scoped).")


def cmd_list(client: Steel) -> None:
    resp = client.credentials.list()
    creds = getattr(resp, "credentials", None) or []
    if not creds:
        print("No credentials stored in Steel.")
        return
    for c in creds:
        print(f"  {getattr(c, 'origin', '?')}  (label={getattr(c, 'label', '')}, "
              f"updated={getattr(c, 'updated_at', '')})")


def cmd_delete(client: Steel, url: str) -> None:
    origin = _origin(url)
    client.credentials.delete(origin=origin)
    print(f"Deleted credential for {origin}.")


def main(argv: list[str]) -> None:
    if len(argv) < 1 or argv[0] not in ("add", "list", "delete"):
        print(__doc__)
        raise SystemExit(1)
    client = Steel(steel_api_key=steel_api_key())
    if argv[0] == "list":
        cmd_list(client)
        return
    if len(argv) < 2:
        raise SystemExit(f"Usage: python3 manage_credentials.py {argv[0]} <site-url>")
    (cmd_add if argv[0] == "add" else cmd_delete)(client, argv[1])


if __name__ == "__main__":
    main(sys.argv[1:])
