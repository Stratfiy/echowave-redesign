"""Push to the native app on a person's phone (MOBILE.md, flag ``mobile_push``).

The app registers its Expo push token after the person allowed notifications
on the phone; this module keeps those tokens and sends through Expo's push
service, which relays to APNs and FCM with the credentials uploaded to the
Expo project. The server never holds an Apple or Google key.

It is a second kind of device under the same push channel, not a new
channel: :func:`notifications._push` sends to the person's browsers *and*
their phones, so a reminder, the brief, mail or a "did it arrive?" reaches
the app with no producer changed, and the person's topics, quiet hours,
snoozes and private previews (generic lock-screen text by default) apply
exactly as they do on the web.

Three producers are added for the app (:func:`announce_reply`,
:func:`announce_approval`, :func:`announce_call`): a reply from Decibyl, a
card waiting for the person's OK, and the end of a call placed for them.
Those are sent to the app only -- never by email or WhatsApp, where a line
per chat reply would be noise -- and only while the flag is on for the
workspace. Each is claimed once per (person, dedupe key, channel), so a
retried job notifies once.

Every function here never raises into its caller: a notice must never break
the reply, card or call that caused it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from api import constants
from api.db import db_client
from api.db.mobile_push_models import MobilePushTokenModel
from api.services import features

FLAG = "mobile_push"
PLATFORMS = ("ios", "android")
#: What Expo issues: ``ExponentPushToken[...]`` (``ExpoPushToken`` is the
#: newer spelling). Anything else is refused, so the table only ever holds
#: something Expo can deliver to.
TOKEN = re.compile(r"^Expo(nent)?PushToken\[[A-Za-z0-9_\-]{8,200}\]$")
#: Expo takes at most 100 messages per request.
BATCH = 100
TIMEOUT_SECONDS = 10.0
#: Expo's answer for an install that is gone (app removed, permission
#: withdrawn): the token is revoked, as a 410 revokes a browser.
GONE = "DeviceNotRegistered"


class Invalid(ValueError):
    """A registration that cannot be stored, in words a person can read."""


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)


def _now() -> datetime:
    return datetime.now(UTC)


def _view(row: MobilePushTokenModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "platform": row.platform,
        "label": row.device_label
        or ("iPhone" if row.platform == "ios" else "Android phone"),
        "app_version": row.app_version,
        "created_at": row.created_at,
        "last_seen_at": row.last_seen_at,
        "last_success_at": row.last_success_at,
        "last_failure_at": row.last_failure_at,
        "state": "revoked"
        if row.revoked_at
        else ("failing" if row.failure_code else "active"),
    }


# --- devices ----------------------------------------------------------------


async def devices(user_id: int) -> list[dict[str, Any]]:
    """The person's own phones, newest first. Never anyone else's."""
    async with db_client.async_session() as session:
        rows = await session.scalars(
            select(MobilePushTokenModel)
            .where(MobilePushTokenModel.user_id == user_id)
            .order_by(MobilePushTokenModel.id.desc())
            .limit(20)
        )
        return [_view(r) for r in rows]


async def register(
    user_id: int,
    organization_id: int,
    *,
    token: str,
    platform: str,
    device_label: str | None = None,
    app_version: str | None = None,
) -> dict[str, Any]:
    """Keep this phone's token for this person. Called on every app open, so
    ``last_seen_at`` says the install is alive. One install is one person's:
    signing in as someone else on the same phone moves it to them."""
    token = (token or "").strip()
    if not TOKEN.match(token):
        raise Invalid("That is not a push token from the Decibyl app.")
    if platform not in PLATFORMS:
        raise Invalid("The app must say whether it is on iOS or Android.")
    now = _now()
    values = {
        "user_id": user_id,
        "organization_id": organization_id,
        "platform": platform,
        "device_label": (device_label or "").strip()[:80] or None,
        "app_version": (app_version or "").strip()[:32] or None,
        "last_seen_at": now,
        "revoked_at": None,
        "failure_code": None,
    }
    async with db_client.async_session() as session:
        await session.execute(
            insert(MobilePushTokenModel)
            .values(token=token, created_at=now, **values)
            .on_conflict_do_update(index_elements=["token"], set_=values)
        )
        await session.commit()
        row = await session.scalar(
            select(MobilePushTokenModel).where(MobilePushTokenModel.token == token)
        )
    return _view(row)


async def unregister(user_id: int, token: str) -> bool:
    """Stop pushing to this phone (sign-out, or the person turned it off).
    Only the owner's own token; anyone else's is not found."""
    async with db_client.async_session() as session:
        result = await session.execute(
            update(MobilePushTokenModel)
            .where(
                MobilePushTokenModel.token == (token or "").strip(),
                MobilePushTokenModel.user_id == user_id,
                MobilePushTokenModel.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
        await session.commit()
    return bool(result.rowcount)


async def remove(user_id: int, device_id: int) -> bool:
    """Revoke one of the person's phones from their device list."""
    async with db_client.async_session() as session:
        result = await session.execute(
            update(MobilePushTokenModel)
            .where(
                MobilePushTokenModel.id == device_id,
                MobilePushTokenModel.user_id == user_id,
                MobilePushTokenModel.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
        await session.commit()
    return bool(result.rowcount)


async def _live_tokens(user_id: int) -> list[MobilePushTokenModel]:
    """Active tokens whose workspace has the flag on. The filter is in
    Python only for the flag, which is not in the database."""
    async with db_client.async_session() as session:
        rows = list(
            await session.scalars(
                select(MobilePushTokenModel).where(
                    MobilePushTokenModel.user_id == user_id,
                    MobilePushTokenModel.revoked_at.is_(None),
                )
            )
        )
    return [r for r in rows if enabled(r.organization_id)]


async def has_device(user_id: int) -> bool:
    return bool(await _live_tokens(user_id))


# --- sending ----------------------------------------------------------------


async def _post(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One request to Expo; returns its push tickets in message order.
    Separate so tests replace the network, never the logic around it."""
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if constants.EXPO_ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {constants.EXPO_ACCESS_TOKEN}"
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.post(
            constants.EXPO_PUSH_URL, json=messages, headers=headers
        )
    response.raise_for_status()
    data = response.json().get("data")
    if not isinstance(data, list):
        # A single message comes back as an object, not a list.
        data = [data] if isinstance(data, dict) else []
    return data


def message(token: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The Expo message for one phone. ``data.url`` is the web path the app
    maps to its own screen (``mobile/src/lib/links.ts``), so the same
    producers serve both."""
    return {
        "to": token,
        "title": str(payload.get("title") or "Decibyl")[:120],
        "body": str(payload.get("body") or "")[:500],
        "data": {"url": payload.get("url") or "/", "tag": payload.get("tag")},
        "sound": "default",
        "priority": "high",
        # Android: the channel the app creates at start (mobile/src/lib/push.ts).
        "channelId": "default",
    }


async def send_to_user(user_id: int, payload: dict[str, Any]) -> str:
    """Send ``payload`` to every live phone of this person. Returns ``off``
    (no workspace of theirs has the flag), ``no_device``, or ``sent`` /
    ``partial`` / ``failed`` like web push."""
    if not features.on_anywhere(FLAG):
        return "off"
    tokens = await _live_tokens(user_id)
    if not tokens:
        return "no_device"
    results: list[str] = []
    for start in range(0, len(tokens), BATCH):
        chunk = tokens[start : start + BATCH]
        try:
            tickets = await _post([message(t.token, payload) for t in chunk])
        except Exception as exc:  # noqa: BLE001 - one failed request, not a crash
            logger.warning("Expo push failed for user {}: {}", user_id, exc)
            tickets = []
        for index, row in enumerate(chunk):
            ticket = tickets[index] if index < len(tickets) else None
            results.append(await _settle(row, ticket))
    if all(r == "ok" for r in results):
        return "sent"
    if any(r == "ok" for r in results):
        return "partial"
    return "failed"


async def _settle(row: MobilePushTokenModel, ticket: dict[str, Any] | None) -> str:
    values: dict[str, Any]
    if ticket and ticket.get("status") == "ok":
        values = {"last_success_at": _now(), "failure_code": None}
        outcome = "ok"
    else:
        error = ((ticket or {}).get("details") or {}).get("error") or (
            "no_ticket" if ticket is None else "error"
        )
        if error == GONE:
            values = {"revoked_at": _now(), "failure_code": "device_not_registered"}
        else:
            values = {"last_failure_at": _now(), "failure_code": str(error)[:64]}
        outcome = str(error)
    async with db_client.async_session() as session:
        await session.execute(
            update(MobilePushTokenModel)
            .where(MobilePushTokenModel.id == row.id)
            .values(**values)
        )
        await session.commit()
    return outcome


# --- producers for the app --------------------------------------------------


async def _announce(
    organization_id: int | None,
    user_id: int | None,
    *,
    topic: str,
    title: str,
    body: str,
    link: str,
    dedupe_key: str,
) -> dict[str, str]:
    if not organization_id or not isinstance(user_id, int) or user_id <= 0:
        return {"all": "no_person"}
    if not enabled(organization_id):
        return {"all": "off"}
    try:
        from api.services.identity import notifications

        return await notifications.notify(
            user_id,
            topic=topic,
            title=title,
            body=body,
            link=link,
            dedupe_key=dedupe_key,
            channels=("push",),
            web=False,
        )
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        logger.warning("Could not announce {} to user {}: {}", topic, user_id, exc)
        return {"all": "failed"}


def thread_link(thread_id: str | None) -> str:
    return f"/overview?thread={thread_id}" if thread_id else "/overview"


async def announce_reply(
    *,
    organization_id: int | None,
    user_id: int | None,
    thread_id: str | None,
    event_id: int | None,
    body: str,
) -> dict[str, str]:
    """Decibyl answered the person on a thread."""
    if event_id is None:
        return {"all": "no_event"}
    text = " ".join((body or "").split())
    return await _announce(
        organization_id,
        user_id,
        topic="task_updates",
        title="Decibyl replied",
        body=text[:140] + ("…" if len(text) > 140 else ""),
        link=thread_link(thread_id),
        dedupe_key=f"mobile:reply:{event_id}",
    )


async def announce_approval(
    *,
    organization_id: int | None,
    user_id: int | None,
    event_id: int | None,
    label: str,
) -> dict[str, str]:
    """A card waits for the person's OK. The notice opens the approval; the
    Confirm itself is only ever on the card, never on the notification."""
    if event_id is None:
        return {"all": "no_event"}
    return await _announce(
        organization_id,
        user_id,
        topic="approvals",
        title="Decibyl wants your OK",
        body=(label or "Something is waiting for you.")[:140],
        link=f"/tasks/approvals/{event_id}",
        dedupe_key=f"mobile:approval:{event_id}",
    )


async def announce_call(
    *,
    organization_id: int | None,
    user_id: int | None,
    workflow_run_id: int,
    summary: str,
) -> dict[str, str]:
    """A call Decibyl placed for the person ended."""
    return await _announce(
        organization_id,
        user_id,
        topic="task_updates",
        title="Your call finished",
        body=(summary or "The call ended.")[:140],
        link="/overview",
        dedupe_key=f"mobile:call:{workflow_run_id}",
    )
