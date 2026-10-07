"""Saved logins: one site's cookies for one person, encrypted, deletable.

Only cookies. The browser never sees a password from us -- a person signs
in themselves with Take over -- and nothing else from the browser's profile
(local storage, history, form fill) is kept: the box's profile is a tmpfs
that disappears with the box.

**Encrypted or not at all.** The cookies of a signed-in session are as good
as the password for as long as they last, so they are Fernet-encrypted with
``PLATFORM_CREDENTIAL_SECRET``, the key every other secret here uses. With
no key configured nothing is saved, and the panel says so; there is no
plaintext fallback.

**Whose.** A login is saved only when the person who drove the browser said
"keep me signed in" for that site, and loaded only into that same person's
next browser in the same organisation, for the sites that task is about.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from api import constants
from api.db import db_client
from api.services.browser import sites

#: The cookie fields kept; anything else the browser reports is dropped.
FIELDS = (
    "name",
    "value",
    "domain",
    "path",
    "expires",
    "httpOnly",
    "secure",
    "sameSite",
)
MAX_COOKIES_PER_SITE = 200


class LoginsUnavailable(RuntimeError):
    """No key to encrypt with: logins cannot be kept on this deployment."""


def _cipher() -> Fernet:
    secret = constants.PLATFORM_CREDENTIAL_SECRET
    if not secret:
        raise LoginsUnavailable(
            "Logins cannot be kept on this server yet: there is no encryption key."
        )
    try:
        return Fernet(secret.encode())
    except Exception as exc:  # noqa: BLE001
        logger.error("PLATFORM_CREDENTIAL_SECRET is not a Fernet key: {}", exc)
        raise LoginsUnavailable(
            "Logins cannot be kept on this server: its encryption key is invalid."
        ) from exc


def can_keep() -> bool:
    try:
        _cipher()
    except LoginsUnavailable:
        return False
    return True


def for_site(cookies: Iterable[dict[str, Any]], site: str) -> list[dict[str, Any]]:
    """The cookies a site set, in the fields we keep."""
    out: list[dict[str, Any]] = []
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if not domain or not (sites.covers(site, domain) or sites.covers(domain, site)):
            continue
        out.append({k: cookie[k] for k in FIELDS if k in cookie})
        if len(out) >= MAX_COOKIES_PER_SITE:
            break
    return out


def encrypt(cookies: list[dict[str, Any]]) -> bytes:
    return _cipher().encrypt(json.dumps(cookies).encode("utf-8"))


def decrypt(blob: bytes) -> list[dict[str, Any]]:
    try:
        raw = _cipher().decrypt(bytes(blob))
    except InvalidToken:
        logger.error("A saved browser login would not decrypt; ignoring it")
        return []
    value = json.loads(raw.decode("utf-8"))
    return value if isinstance(value, list) else []


async def save(
    *,
    organization_id: int,
    user_id: int,
    site: str,
    cookies: Iterable[dict[str, Any]],
) -> int:
    """Keep ``site``'s cookies for this person. Returns how many were kept."""
    kept = for_site(cookies, site)
    if not kept:
        return 0
    await db_client.save_browser_login(
        organization_id=organization_id,
        user_id=user_id,
        site=site,
        cookies_encrypted=encrypt(kept),
        cookie_count=len(kept),
    )
    return len(kept)


async def load(
    *, organization_id: int, user_id: int, task_sites: Iterable[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """This person's saved cookies for the task's sites, and which sites.
    A task with no sites named gets none: a login is lent to the site it
    was kept for, not to wherever a page leads."""
    wanted = [s for s in task_sites if s]
    if not wanted or not can_keep():
        return [], []
    rows = await db_client.list_browser_logins(
        organization_id=organization_id, user_id=user_id
    )
    cookies: list[dict[str, Any]] = []
    used: list[str] = []
    for row in rows:
        if not any(
            sites.covers(w, row.site) or sites.covers(row.site, w) for w in wanted
        ):
            continue
        cookies.extend(decrypt(row.cookies_encrypted))
        used.append(row.site)
        await db_client.touch_browser_login(row.id)
    return cookies, used
