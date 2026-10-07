"""Microsoft Teams: the Decibyl bot in a personal chat, Adaptive Card buttons.

Setup is LAUNCH-6 (KAN-278): an Azure Bot resource (single-tenant, as Azure
now requires for new bots) with its messaging endpoint at
``{BACKEND}/api/v1/public/teams/messages``, and ``MICROSOFT_APP_ID``,
``MICROSOFT_APP_PASSWORD`` and ``MICROSOFT_APP_TENANT_ID`` in the environment.

**Inbound proof.** Every request from the Bot Connector carries a bearer JWT
signed with a key from Bot Framework's OpenID metadata. We check the
signature, that the issuer is Bot Framework, that the audience is our app id,
that it has not expired, and that its ``serviceUrl`` claim equals the
activity's. Only then is the activity's ``serviceUrl`` trusted as the place
to post replies: an unverified one would let anyone point our bot token at a
host of their choosing.

**Outbound.** A client-credentials token for ``api.botframework.com``, cached
until shortly before it expires, and ``POST {serviceUrl}/v3/conversations/
{id}/activities``.
"""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from loguru import logger

from .base import TEAMS, Card, Inbound, button_id, parse_button_id

TIMEOUT_SECONDS = 15
OPENID_METADATA = "https://login.botframework.com/v1/.well-known/openidconfiguration"
ISSUER = "https://api.botframework.com"
SCOPE = "https://api.botframework.com/.default"
#: Seconds of clock drift tolerated on the inbound token.
LEEWAY_SECONDS = 5 * 60

_jwks_client: jwt.PyJWKClient | None = None
_token_cache: dict[str, Any] = {"value": None, "expires_at": 0.0}


def app_id() -> str:
    return os.getenv("MICROSOFT_APP_ID", "").strip()


def app_password() -> str:
    return os.getenv("MICROSOFT_APP_PASSWORD", "").strip()


def tenant_id() -> str:
    return os.getenv("MICROSOFT_APP_TENANT_ID", "").strip()


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        metadata = httpx.get(OPENID_METADATA, timeout=TIMEOUT_SECONDS).json()
        _jwks_client = jwt.PyJWKClient(metadata["jwks_uri"], cache_keys=True)
    return _jwks_client


def verify(authorization: str | None, activity: dict[str, Any]) -> bool:
    """True if the request is from the Bot Connector, for our bot, about this
    activity's service URL."""
    if not app_id() or not authorization or not authorization.startswith("Bearer "):
        return False
    token = authorization[len("Bearer ") :].strip()
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=app_id(),
            issuer=ISSUER,
            leeway=LEEWAY_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - any failure is a refusal
        logger.warning("Teams: token refused: {}", exc)
        return False
    claimed = str(claims.get("serviceurl") or claims.get("serviceUrl") or "")
    return bool(claimed) and claimed.rstrip("/") == str(
        activity.get("serviceUrl") or ""
    ).rstrip("/")


def parse(activity: dict[str, Any]) -> Inbound | None:
    """A message or card press in a personal chat, or None."""
    if not isinstance(activity, dict) or activity.get("type") != "message":
        return None
    conversation = activity.get("conversation") or {}
    # Channels and group chats are not Decibyl's: it answers one person.
    if conversation.get("conversationType") not in (None, "personal"):
        return None
    sender = activity.get("from") or {}
    tenant = str(
        conversation.get("tenantId")
        or ((activity.get("channelData") or {}).get("tenant") or {}).get("id")
        or ""
    )
    who = str(sender.get("aadObjectId") or sender.get("id") or "")
    service_url = str(activity.get("serviceUrl") or "")
    if not who or not conversation.get("id") or not service_url.startswith("https://"):
        return None
    value = activity.get("value") if isinstance(activity.get("value"), dict) else {}
    tap = parse_button_id(value.get("card")) if value else None
    return Inbound(
        channel=TEAMS,
        external_id=f"{tenant}:{who}",
        message_id=f"act:{activity.get('id')}",
        text="" if tap else _plain(str(activity.get("text") or "")),
        tap=tap,
        display_name=str(sender.get("name") or ""),
        ref={
            "service_url": service_url,
            "conversation_id": conversation.get("id"),
            "tenant_id": tenant,
        },
    )


def _plain(text: str) -> str:
    """Teams wraps a mention of the bot in <at>…</at>; the rest is the ask."""
    import re

    return re.sub(r"<at>.*?</at>", "", text).strip()


def adaptive_card(card: Card) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": card.headline(), "weight": "Bolder", "wrap": True}
    ]
    if card.effect and card.state == "proposed":
        body.append({"type": "TextBlock", "text": card.effect, "wrap": True})
    return {
        "contentType": "application/vnd.microsoft.card.adaptive",
        "content": {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": body,
            "actions": [
                {
                    "type": "Action.Submit",
                    "title": label,
                    "data": {"card": button_id(card.event_id, verb, card.version)},
                    **({"style": "positive"} if verb == "confirm" else {}),
                }
                for verb, label in card.buttons()
            ],
        },
    }


async def _access_token() -> str | None:
    if _token_cache["value"] and _token_cache["expires_at"] > time.time() + 60:
        return _token_cache["value"]
    if not app_id() or not app_password():
        return None
    authority = tenant_id() or "botframework.com"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"https://login.microsoftonline.com/{authority}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": app_id(),
                    "client_secret": app_password(),
                    "scope": SCOPE,
                },
            )
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.error("Teams: could not get a token: {}", exc)
        return None
    token = body.get("access_token")
    if not token:
        logger.error("Teams: token refused: {}", body.get("error_description") or body)
        return None
    _token_cache["value"] = token
    _token_cache["expires_at"] = time.time() + int(body.get("expires_in") or 3000)
    return token


class TeamsAdapter:
    name = TEAMS

    def enabled(self, organization_id: int | None = None) -> bool:
        return bool(app_id() and app_password())

    async def send_text(self, ref: dict[str, Any], text: str) -> bool:
        if not text.strip():
            return False
        return await self._post(ref, {"type": "message", "text": text[:28000]})

    async def send_card(self, ref: dict[str, Any], card: Card) -> bool:
        return await self._post(
            ref,
            {"type": "message", "attachments": [adaptive_card(card)]},
        )

    async def acknowledge(self, inbound: Inbound, note: str = "") -> None:
        # The fresh card posted after a press is the acknowledgement.
        return None

    async def _post(self, ref: dict[str, Any], activity: dict[str, Any]) -> bool:
        service_url = str(ref.get("service_url") or "")
        conversation_id = ref.get("conversation_id")
        if not conversation_id or urlparse(service_url).scheme != "https":
            return False
        token = await _access_token()
        if not token:
            return False
        url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url, headers={"Authorization": f"Bearer {token}"}, json=activity
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("Teams post failed: {}", exc)
            return False
        if response.status_code >= 400:
            logger.warning(
                "Teams refused a post: {} {}", response.status_code, response.text[:300]
            )
            return False
        return True


ADAPTER = TeamsAdapter()
