"""Execution against Composio's REST API, scoped to one organization.

Composio is the connector layer for everything Google Calendar is not: Gmail,
Sheets, Slack, HubSpot, Zoho and the several hundred others we are not going
to write an OAuth flow for one at a time. This module is the whole of our side
of it -- one authenticated POST per tool call.

Two decisions are load-bearing enough to state here.

**No SDK.** The official ``composio`` package is synchronous. A blocking HTTP
call on this event loop does not stall one tool call, it stalls every call
sharing the worker: the caller who asked for the appointment waits, and so
does everyone else mid-sentence on that process. ``composio_client`` ships an
async variant, but it is a generated client whose shape tracks their spec, and
what we need from it is a POST to one path. httpx.AsyncClient is what the HTTP
tool and the Calendar client already use, so this reads like them.

**The tenant id is derived, never passed in.** Composio decides which mailbox
a call touches from the ``user_id`` in the request body. Take it from an
argument and one mis-wired caller sends another organization's identifier;
derive it from ``organization_id`` and there is no value a caller could supply
that would cross the boundary. See :func:`tenant_user_id`.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx
from loguru import logger

from api.constants import (
    COMPOSIO_API_KEY,
    COMPOSIO_BASE_URL,
    COMPOSIO_TIMEOUT_SECS,
)

#: Prefix on every identifier we hand Composio. Namespaced because the id space
#: is shared across everything one Composio project ever sees: a bare "7" is a
#: plausible identifier for something else we integrate later, and the failure
#: mode of a collision is one organization reading another's mail.
TENANT_PREFIX = "decibyl_org_"


class ComposioNotConfigured(RuntimeError):
    """Raised when a Composio tool runs on a deployment with no API key."""


class ComposioExecutionError(RuntimeError):
    """Raised when Composio accepted the request and the tool itself failed."""


def is_configured() -> bool:
    """Whether this deployment can execute Composio tools at all."""
    return bool(COMPOSIO_API_KEY)


def tenant_user_id(organization_id: int) -> str:
    """The Composio identity for one organization.

    The only place a tenant boundary is drawn for Composio, so it is strict
    about its input rather than forgiving: a ``None`` organization_id reaching
    here means the engine could not establish who is calling, and the safe
    response to "I don't know whose call this is" is to refuse, not to fall
    back to a default account that belongs to somebody.

    A bool is rejected despite being an int for the same reason it is in
    ``_as_seconds``: ``organization_id=True`` means a caller passed the wrong
    field, and ``decibyl_org_True`` is a silent wrong answer rather than a
    loud one.
    """
    if isinstance(organization_id, bool) or not isinstance(organization_id, int):
        raise ComposioNotConfigured(
            "Composio tools need a numeric organization_id to scope the call"
        )
    if organization_id <= 0:
        raise ComposioNotConfigured(
            "Composio tools need a positive organization_id to scope the call"
        )
    return f"{TENANT_PREFIX}{organization_id}"


def _headers() -> dict[str, str]:
    if not COMPOSIO_API_KEY:
        raise ComposioNotConfigured(
            "COMPOSIO_API_KEY is not set on this deployment; "
            "Composio tools cannot be executed"
        )
    return {
        "x-api-key": COMPOSIO_API_KEY,
        "Content-Type": "application/json",
    }


async def execute_tool(
    *,
    tool_slug: str,
    arguments: dict[str, Any],
    organization_id: Optional[int],
    timeout_secs: float = COMPOSIO_TIMEOUT_SECS,
) -> dict[str, Any]:
    """Run one Composio tool on behalf of one organization.

    Returns the same ``{"status": "success"|"error", ...}`` envelope the HTTP
    and Calendar tools return, so the model sees one contract regardless of
    which kind of tool it called and can say something useful either way.

    Failure is never raised past this point for the model's benefit: a tool
    that 500s mid-conversation should leave the agent able to say "I couldn't
    do that just now", which it cannot do if the exception unwinds the turn.
    """
    user_id = tenant_user_id(organization_id)  # raises before any network I/O
    headers = _headers()

    url = f"{COMPOSIO_BASE_URL}/api/v3.1/tools/execute/{tool_slug}"
    payload = {"arguments": arguments or {}, "user_id": user_id}

    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException:
        logger.warning(
            "Composio tool {} timed out after {}s for org {}",
            tool_slug,
            timeout_secs,
            organization_id,
        )
        return {
            "status": "error",
            "error": f"{tool_slug} did not respond in time",
        }
    except httpx.HTTPError as exc:
        logger.warning("Composio tool {} could not be reached: {}", tool_slug, exc)
        return {"status": "error", "error": f"{tool_slug} could not be reached"}

    if response.status_code == 401 or response.status_code == 403:
        # Ours, not the customer's: our project key is wrong or revoked. Say so
        # in the log with enough detail to act on, and say nothing about keys to
        # the model, which is one prompt injection away from repeating it aloud.
        logger.error(
            "Composio rejected our project key ({}) executing {}",
            response.status_code,
            tool_slug,
        )
        return {"status": "error", "error": f"{tool_slug} is not available right now"}

    try:
        body = response.json()
    except ValueError:
        logger.error(
            "Composio returned non-JSON for {} (HTTP {})",
            tool_slug,
            response.status_code,
        )
        return {"status": "error", "error": f"{tool_slug} returned an unreadable reply"}

    if response.status_code >= 400:
        message = _error_message(body) or f"HTTP {response.status_code}"
        logger.warning("Composio tool {} failed: {}", tool_slug, message)
        return {"status": "error", "error": message}

    # Composio reports a tool's own failure inside a 200: `successful: false`
    # with the provider's message. Reading only the HTTP status would report
    # "sent" for an email the provider rejected, which is precisely the class
    # of lie action_honesty_instructions exists to prevent.
    if body.get("successful") is False:
        message = _error_message(body) or f"{tool_slug} did not succeed"
        logger.warning("Composio tool {} reported failure: {}", tool_slug, message)
        return {"status": "error", "error": message}

    return {"status": "success", "data": body.get("data", body)}


def _error_message(body: Any) -> Optional[str]:
    """The provider's own words for what went wrong, when it gave any."""
    if not isinstance(body, dict):
        return None
    for key in ("error", "message", "detail"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("message")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


async def connected_toolkits(
    organization_id: Optional[int],
    *,
    timeout_secs: float = COMPOSIO_TIMEOUT_SECS,
) -> list[str]:
    """Which apps this organization has actually authorized, upper-cased.

    Read separately from execution because the two answer different questions
    at different times: this one is for the editor, so an operator is told
    "connect Gmail first" while building the agent rather than by an agent
    apologising to a caller.
    """
    user_id = tenant_user_id(organization_id)
    headers = _headers()

    url = f"{COMPOSIO_BASE_URL}/api/v3.1/connected_accounts"
    params = {"user_ids": user_id, "statuses": "ACTIVE"}

    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "Could not list Composio connections for org {}: {}", organization_id, exc
        )
        return []

    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []

    slugs: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        toolkit = item.get("toolkit")
        slug = toolkit.get("slug") if isinstance(toolkit, dict) else item.get("toolkit")
        if isinstance(slug, str) and slug.strip():
            slugs.append(slug.strip().upper())
    return sorted(set(slugs))


async def toolkit_name(
    toolkit: str, *, timeout_secs: float = COMPOSIO_TIMEOUT_SECS
) -> Optional[str]:
    """Composio's display name for a toolkit, or None if there is no such app.

    Exists so nothing downstream has to trust a slug a model produced. A model
    asked to connect "the WhatsApp one" will cheerfully invent ``WHATSAPP_BIZ``,
    and an auth config created against an invented slug fails later, somewhere
    less obviously connected to the guess. Checking here turns that into "I
    don't know that app" in the same turn the user asked.
    """
    headers = _headers()
    url = f"{COMPOSIO_BASE_URL}/api/v3.1/toolkits/{toolkit.strip().lower()}"
    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            response = await client.get(url, headers=headers)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not look up Composio toolkit {}: {}", toolkit, exc)
        return None

    if response.status_code == 404:
        return None
    if response.status_code >= 400:
        logger.warning(
            "Composio toolkit lookup for {} failed: HTTP {}",
            toolkit,
            response.status_code,
        )
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    name = body.get("name") if isinstance(body, dict) else None
    return name if isinstance(name, str) and name.strip() else None


async def _managed_auth_config_id(
    toolkit: str, *, timeout_secs: float
) -> Optional[str]:
    """The project's auth config for this app, creating one if it has none.

    Reused rather than created per organization on purpose: an auth config is
    the *application's* registration with the provider, not a customer's
    account. One per app, shared; the per-customer part is the connected
    account hanging off it. Creating one per organization would multiply
    registrations for no gain and make the provider's rate limits ours to
    explain.
    """
    headers = _headers()
    slug = toolkit.strip().lower()

    async with httpx.AsyncClient(timeout=timeout_secs) as client:
        existing = await client.get(
            f"{COMPOSIO_BASE_URL}/api/v3.1/auth_configs",
            headers=headers,
            params={"toolkit_slug": slug},
        )
        if existing.status_code < 400:
            try:
                items = (existing.json() or {}).get("items") or []
            except ValueError:
                items = []
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    return item["id"]

        created = await client.post(
            f"{COMPOSIO_BASE_URL}/api/v3.1/auth_configs",
            headers=headers,
            json={
                "toolkit": {"slug": slug.upper()},
                # Composio's own verified OAuth application. The alternative is
                # registering ours with every provider, which for Google's
                # restricted scopes means an annual paid security assessment
                # before a single customer can connect a mailbox. Worth doing
                # later for our own branding on the consent screen; not worth
                # doing to ship the first one.
                "auth_config": {"type": "use_composio_managed_auth"},
            },
        )

    if created.status_code >= 400:
        logger.error(
            "Could not create a Composio auth config for {}: HTTP {} {}",
            slug,
            created.status_code,
            created.text[:200],
        )
        return None
    try:
        body = created.json()
    except ValueError:
        return None
    config = body.get("auth_config") if isinstance(body, dict) else None
    config_id = config.get("id") if isinstance(config, dict) else None
    return config_id if isinstance(config_id, str) else None


async def connect_link(
    *,
    toolkit: str,
    organization_id: Optional[int],
    timeout_secs: float = COMPOSIO_TIMEOUT_SECS,
) -> dict[str, Any]:
    """A URL this organization's owner opens to authorize one app.

    The whole OAuth dance belongs to Composio: we never see the provider's
    tokens, never hold a refresh token, and never implement a callback. What we
    hold is the mapping from our organization to their ``user_id``, which is
    the only part that has to be right.

    Note the ``/api/v3/`` path. Managed-auth connections are minted here and
    not on the v3.1 ``connected_accounts`` endpoint, which now refuses them and
    says so -- keep the version difference rather than tidying it away.
    """
    user_id = tenant_user_id(organization_id)
    headers = _headers()

    config_id = await _managed_auth_config_id(toolkit, timeout_secs=timeout_secs)
    if not config_id:
        return {"error": f"Could not set up {toolkit} for connecting."}

    try:
        async with httpx.AsyncClient(timeout=timeout_secs) as client:
            response = await client.post(
                f"{COMPOSIO_BASE_URL}/api/v3/connected_accounts/link",
                headers=headers,
                json={"auth_config_id": config_id, "user_id": user_id},
            )
    except httpx.HTTPError as exc:
        logger.warning("Could not mint a Composio connect link: {}", exc)
        return {"error": f"Could not start connecting {toolkit} just now."}

    if response.status_code >= 400:
        logger.error(
            "Composio refused a connect link for {} ({}): {}",
            toolkit,
            response.status_code,
            response.text[:200],
        )
        return {"error": f"Could not start connecting {toolkit} just now."}

    try:
        body = response.json()
    except ValueError:
        return {"error": f"Could not start connecting {toolkit} just now."}

    url = body.get("redirect_url")
    if not isinstance(url, str) or not url:
        return {"error": f"Could not start connecting {toolkit} just now."}

    return {"url": url, "expires_at": body.get("expires_at")}
