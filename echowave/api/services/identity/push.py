"""Web push to a person's own browsers and phones (screen 21).

Encrypted per RFC 8291 and signed with the operator's VAPID keys by
``pywebpush``. Without keys push is "needs setup" -- never generated on the
fly, because a key that changes on restart silently orphans every
subscription.

Subscriptions are accepted only for the browsers' own push services, so a
subscription can never make the server send requests to an address of the
caller's choosing (an allowlist in the safe direction: the worst case is a
new browser's push service refused, which somebody sees and reports).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from api import constants

#: The push services browsers use. Exact hosts, or a suffix starting with a
#: dot for services that shard by subdomain.
PUSH_HOSTS = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    "web.push.apple.com",
    ".notify.windows.com",
    ".push.apple.com",
)

OK = "ok"
GONE = "gone"


def configured() -> bool:
    return bool(constants.VAPID_PUBLIC_KEY and constants.VAPID_PRIVATE_KEY)


def public_key() -> str | None:
    return constants.VAPID_PUBLIC_KEY or None


def endpoint_allowed(endpoint: str) -> bool:
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.port not in (None, 443)
    ):
        return False
    host = parsed.hostname.lower()
    return any(
        host.endswith(allowed) if allowed.startswith(".") else host == allowed
        for allowed in PUSH_HOSTS
    )


def _send_sync(subscription: dict[str, Any], data: str) -> int:
    from pywebpush import WebPushException, webpush

    try:
        response = webpush(
            subscription_info=subscription,
            data=data,
            vapid_private_key=constants.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": constants.VAPID_SUBJECT},
            timeout=10,
            ttl=3600,
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return int(status or 0)
    return int(getattr(response, "status_code", 201) or 201)


async def send(subscription: Any, payload: dict[str, Any]) -> str:
    """``ok``, ``gone`` (the permission was revoked or the subscription
    expired: 404/410), or ``failed:<code>``."""
    if not configured():
        return "failed:needs_setup"
    if not endpoint_allowed(subscription.endpoint):
        return "failed:endpoint_not_allowed"
    info = {
        "endpoint": subscription.endpoint,
        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
    }
    try:
        status = await asyncio.to_thread(_send_sync, info, json.dumps(payload))
    except Exception as exc:  # noqa: BLE001 - one device failing is that device's state
        logger.warning("Push to subscription {} failed: {}", subscription.id, exc)
        return "failed:error"
    if status in (404, 410):
        return GONE
    if 200 <= status < 300:
        return OK
    return f"failed:{status}"
