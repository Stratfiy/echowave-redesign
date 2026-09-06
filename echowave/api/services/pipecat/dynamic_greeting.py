"""Ask the customer's own system what this agent should open with.

A template variable is resolved before the call is placed, so a greeting can
carry a name but not a fact: "your order shipped this morning", "there are two
appointments free today", "your balance cleared". Those are true at the moment
the phone is answered and not a minute earlier, which is why Gnani ships this
and why a pre-call variable cannot substitute for it.

**The whole design problem is that somebody is already on the line.** This runs
between the call connecting and the first word being spoken, so every failure
mode — a slow endpoint, a 500, a redirect to nowhere, a payload that is not
JSON — has to end in the agent greeting them anyway. There is no error state
that is better than the static greeting, so there is no error state: every path
returns *some* greeting, and the only question is whose.

That is also why the timeout is short and hard. A greeting that arrives after
three seconds is not a greeting, it is a silence the caller has already filled
with "hello?".
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from api.utils.url_security import validate_user_configured_service_url

#: Default and ceiling for the fetch. Chosen against a caller's patience rather
#: than an endpoint's convenience: dead air on answer is the single worst thing
#: this product can do, and 1.5s is already at the edge of noticeable.
DEFAULT_TIMEOUT_MS = 1500
MAX_TIMEOUT_MS = 3000

#: Anything longer is not a greeting. Truncating rather than rejecting: a
#: customer whose endpoint returns an essay should hear the first sentence of
#: it, not the fallback.
MAX_GREETING_CHARS = 400


def is_enabled(config: Any) -> bool:
    """Did the account turn this on, and give it somewhere to ask?"""
    return (
        isinstance(config, dict)
        and config.get("enabled") is True
        and isinstance(config.get("url"), str)
        and bool(config["url"].strip())
    )


def _timeout_seconds(config: dict) -> float:
    raw = config.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    try:
        milliseconds = int(raw)
    except (TypeError, ValueError):
        milliseconds = DEFAULT_TIMEOUT_MS
    milliseconds = max(200, min(milliseconds, MAX_TIMEOUT_MS))
    return milliseconds / 1000.0


def _greeting_from_payload(payload: Any) -> str | None:
    """Pull the greeting out of whatever shape the customer returned.

    Three key names accepted because three are obvious and a customer should
    not have to read our documentation to guess which one we picked.
    """
    if isinstance(payload, str):
        text = payload.strip()
        return text or None
    if not isinstance(payload, dict):
        return None
    for key in ("greeting", "message", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def fetch_greeting(
    config: Any,
    *,
    context: dict,
    fallback: str,
) -> str:
    """The opening line for this call. Never raises, always returns something.

    ``fallback`` is the agent's configured greeting and is what comes back on
    every failure — disabled, bad URL, timeout, non-2xx, unparseable body, or
    an empty answer.
    """
    if not is_enabled(config):
        return fallback

    url = config["url"].strip()
    try:
        # The same guard every other user-configured URL goes through. Without
        # it this is a server-side fetch of an address the customer chooses,
        # i.e. a request forgery primitive pointed at our own network.
        validate_user_configured_service_url(url, field_name="Dynamic greeting URL")
    except ValueError as error:
        logger.warning(
            f"Dynamic greeting URL refused, using the static greeting: {error}"
        )
        return fallback

    try:
        async with httpx.AsyncClient(
            timeout=_timeout_seconds(config),
            # A redirect is a second URL the guard above never saw, which would
            # be a way around it.
            follow_redirects=False,
        ) as client:
            response = await client.post(url, json=context)
            response.raise_for_status()
            greeting = _greeting_from_payload(response.json())
    except Exception as error:  # noqa: BLE001 - a caller is on the line
        logger.warning(
            f"Dynamic greeting fetch failed, using the static greeting: {error}"
        )
        return fallback

    if not greeting:
        logger.info(
            "Dynamic greeting endpoint returned no greeting; using the static one"
        )
        return fallback

    return greeting[:MAX_GREETING_CHARS]
