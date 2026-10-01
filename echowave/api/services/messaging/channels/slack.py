"""Slack: the Decibyl app in a workspace. DMs and Block Kit cards.

Setup is LAUNCH-6 (KAN-278): ``SLACK_CLIENT_ID``, ``SLACK_CLIENT_SECRET``,
``SLACK_SIGNING_SECRET``. A workspace installs the app with "Add to Slack"
from Settings (``install_url`` → ``complete_install``), which stores that
workspace's bot token, encrypted, against the organisation. A member then
links themselves with a code like every other app.

Every request from Slack carries ``X-Slack-Signature`` (v0 HMAC over
``v0:{timestamp}:{body}``) and ``X-Slack-Request-Timestamp``; a request older
than five minutes is refused so a captured one cannot be replayed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.channel_identity_models import SlackInstallationModel

from .base import SLACK, Card, Inbound, button_id, parse_button_id

TIMEOUT_SECONDS = 15
MAX_SKEW_SECONDS = 5 * 60
SCOPES = "chat:write,im:history,im:read,im:write,users:read"
_STATE_AUDIENCE = "decibyl-slack-install"


def client_id() -> str:
    return os.getenv("SLACK_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.getenv("SLACK_CLIENT_SECRET", "").strip()


def signing_secret() -> str:
    return os.getenv("SLACK_SIGNING_SECRET", "").strip()


def verify(
    raw_body: bytes,
    timestamp: str | None,
    signature: str | None,
    *,
    now: float | None = None,
) -> bool:
    secret = signing_secret()
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs((now or time.time()) - ts) > MAX_SKEW_SECONDS:
        return False
    base = b"v0:" + str(ts).encode() + b":" + raw_body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


def parse_event(payload: dict[str, Any]) -> Inbound | None:
    """A direct message to the app from a person, or None."""
    event = payload.get("event") or {}
    if event.get("type") != "message" or event.get("channel_type") != "im":
        return None
    # The app's own messages, edits and joins come back as events too.
    if event.get("bot_id") or event.get("subtype"):
        return None
    team = str(payload.get("team_id") or event.get("team") or "")
    user = str(event.get("user") or "")
    if not team or not user:
        return None
    return Inbound(
        channel=SLACK,
        external_id=f"{team}:{user}",
        message_id=f"ev:{payload.get('event_id') or event.get('ts')}",
        text=str(event.get("text") or ""),
        ref={"team_id": team, "channel": event.get("channel")},
    )


def parse_interaction(payload: dict[str, Any]) -> Inbound | None:
    """A card button pressed, or None."""
    if payload.get("type") != "block_actions":
        return None
    actions = payload.get("actions") or []
    if not actions:
        return None
    tap = parse_button_id((actions[0] or {}).get("value"))
    team = str(((payload.get("team") or {}).get("id")) or "")
    user = str(((payload.get("user") or {}).get("id")) or "")
    channel = ((payload.get("channel") or {}).get("id")) or (
        (payload.get("container") or {}).get("channel_id")
    )
    if not team or not user or tap is None:
        return None
    return Inbound(
        channel=SLACK,
        external_id=f"{team}:{user}",
        message_id=f"ia:{payload.get('trigger_id') or actions[0].get('action_ts')}",
        tap=tap,
        display_name=str((payload.get("user") or {}).get("name") or ""),
        ref={"team_id": team, "channel": channel},
        extra={"response_url": payload.get("response_url")},
    )


def blocks(card: Card) -> list[dict[str, Any]]:
    text = f"*{card.headline()}*"
    if card.effect and card.state == "proposed":
        text += f"\n{card.effect}"
    out: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}
    ]
    buttons = card.buttons()
    if buttons:
        out.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": label},
                        "action_id": verb,
                        "value": button_id(card.event_id, verb),
                        **({"style": "primary"} if verb == "confirm" else {}),
                    }
                    for verb, label in buttons
                ],
            }
        )
    return out


# --- install ----------------------------------------------------------------


def install_url(*, organization_id: int, user_id: int, redirect_uri: str) -> str | None:
    from api.constants import OSS_JWT_SECRET

    if not client_id():
        return None
    state = jwt.encode(
        {
            "aud": _STATE_AUDIENCE,
            "org": organization_id,
            "usr": user_id,
            "exp": datetime.now(UTC) + timedelta(minutes=15),
        },
        OSS_JWT_SECRET,
        algorithm="HS256",
    )
    query = urlencode(
        {
            "client_id": client_id(),
            "scope": SCOPES,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"https://slack.com/oauth/v2/authorize?{query}"


async def complete_install(*, code: str, state: str, redirect_uri: str) -> str:
    """Exchange the code, store the workspace's bot token. Returns the
    workspace name. Raises ValueError with a message for the screen."""
    from api.constants import OSS_JWT_SECRET
    from api.services.configuration.organization_credentials import _cipher

    try:
        claims = jwt.decode(
            state, OSS_JWT_SECRET, algorithms=["HS256"], audience=_STATE_AUDIENCE
        )
    except jwt.InvalidTokenError as exc:
        raise ValueError("This Slack install link has expired. Start again.") from exc
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": client_id(),
                "client_secret": client_secret(),
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    body = response.json() if response.content else {}
    if not body.get("ok"):
        raise ValueError(
            f"Slack refused the install: {body.get('error') or response.status_code}"
        )
    team = body.get("team") or {}
    token = str(body.get("access_token") or "")
    if not team.get("id") or not token:
        raise ValueError("Slack did not return a workspace and a bot token.")
    encrypted = _cipher().encrypt(token.encode()).decode()
    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(SlackInstallationModel).where(
                    SlackInstallationModel.team_id == team["id"]
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = SlackInstallationModel(team_id=team["id"])
            session.add(row)
        row.organization_id = int(claims["org"])
        row.installed_by_user_id = int(claims["usr"])
        row.team_name = str(team.get("name") or "")[:200]
        row.bot_user_id = str(body.get("bot_user_id") or "") or None
        row.encrypted_bot_token = encrypted
        await session.commit()
    return str(team.get("name") or "your workspace")


async def bot_token(team_id: str) -> str | None:
    from api.services.configuration.organization_credentials import _cipher

    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(SlackInstallationModel).where(
                    SlackInstallationModel.team_id == team_id
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    try:
        return _cipher().decrypt(row.encrypted_bot_token.encode()).decode()
    except Exception:  # noqa: BLE001
        logger.error("Slack bot token for {} cannot be decrypted", team_id)
        return None


class SlackAdapter:
    name = SLACK

    def enabled(self, organization_id: int | None = None) -> bool:
        return bool(client_id() and signing_secret())

    async def send_text(self, ref: dict[str, Any], text: str) -> bool:
        return await self._post(ref, {"text": text[:39000]})

    async def send_card(self, ref: dict[str, Any], card: Card) -> bool:
        return await self._post(ref, {"text": card.headline(), "blocks": blocks(card)})

    async def acknowledge(self, inbound: Inbound, note: str = "") -> None:
        # The fresh card posted after a press is the acknowledgement; the
        # HTTP 200 on the interaction is what Slack needs within 3 s.
        return None

    async def _post(self, ref: dict[str, Any], body: dict[str, Any]) -> bool:
        team_id, channel = ref.get("team_id"), ref.get("channel")
        if not team_id or not channel:
            return False
        token = await bot_token(str(team_id))
        if not token:
            return False
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"channel": channel, **body},
                )
            ok = bool((response.json() or {}).get("ok"))
        except Exception as exc:  # noqa: BLE001
            logger.error("Slack post failed: {}", exc)
            return False
        if not ok:
            logger.warning("Slack refused a post: {}", response.text[:300])
        return ok


def form_payload(raw_body: bytes) -> dict[str, Any]:
    """Slack posts interactions as ``payload=<json>`` form data."""
    from urllib.parse import parse_qs

    values = parse_qs(raw_body.decode(errors="replace")).get("payload") or ["{}"]
    try:
        return json.loads(values[0])
    except ValueError:
        return {}


ADAPTER = SlackAdapter()
