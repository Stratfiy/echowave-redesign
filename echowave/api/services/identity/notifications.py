"""How one person is told things, and telling them (screen 21; handoff 24).

Preferences are the person's own row (``notification_preferences``); no
route takes another person's id, and saving never touches anyone else's.
The save follows the design's contract: the client names the revision it
read; an older one is a conflict that carries the stored value.

Channels: push (this person's browsers and phones), email (their login
address), and the app they linked to talk to Decibyl. The in-app bell is
the workspace's, shared by every member, so personal notices are not put
there; the screen says so instead of offering a switch that would leak.

Topics: reminders the person asked for (always outside quiet hours -- an
explicit time is an explicit time), approvals waiting, task updates, mail
at their Decibyl address, and optional suggestions (off until chosen,
capped per day by server policy).

Delivery (:func:`notify`) never raises, writes one row per person, dedupe
key and channel before sending (so a retried job notifies once), and keeps
lock-screen text generic while private previews are on (the default).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.identity_models import (
    NotificationDeliveryModel,
    NotificationPreferencesModel,
    PushSubscriptionModel,
)
from api.services import features
from api.services.identity import push

FLAG = "identity_notifications"

CHANNELS = ("push", "email", "channel")
TOPICS = {
    "reminders": {
        "label": "Reminders you asked for",
        "requested": True,
        "default": True,
    },
    "approvals": {
        "label": "Something waits for your OK",
        "requested": False,
        "default": True,
    },
    "task_updates": {
        "label": "A task finished or needs you",
        "requested": False,
        "default": True,
    },
    "mail": {
        "label": "Mail at your Decibyl address",
        "requested": False,
        "default": True,
    },
    "suggestions": {
        "label": "Suggestions from Decibyl",
        "requested": False,
        "default": False,
    },
}
OPTIONAL = ("suggestions",)
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
GENERIC_TITLE = "Decibyl"
GENERIC_BODY = "You have an update. Open Decibyl to see it."
DEFAULT_TIMEZONE = "Asia/Kolkata"


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


class PreferencesInvalid(ValueError):
    pass


class Conflict(Exception):
    def __init__(self, stored: dict[str, Any]):
        super().__init__("conflict")
        self.stored = stored


def _now() -> datetime:
    return datetime.now(UTC)


def _defaults(user_id: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "channels": {channel: False for channel in CHANNELS},
        "topics": {
            name: {"on": meta["default"], "snoozed_until": None}
            for name, meta in TOPICS.items()
        },
        "quiet_start": None,
        "quiet_end": None,
        "private_previews": True,
        "revision": 0,
        "updated_at": None,
    }


def _merge(user_id: int, row: NotificationPreferencesModel | None) -> dict[str, Any]:
    prefs = _defaults(user_id)
    if row is None:
        return prefs
    prefs["channels"].update(
        {k: bool(v) for k, v in (row.channels or {}).items() if k in CHANNELS}
    )
    for name, value in (row.topics or {}).items():
        if name in TOPICS and isinstance(value, dict):
            prefs["topics"][name] = {
                "on": bool(value.get("on")),
                "snoozed_until": value.get("snoozed_until"),
            }
    prefs.update(
        quiet_start=row.quiet_start,
        quiet_end=row.quiet_end,
        private_previews=bool(row.private_previews),
        revision=row.revision,
        updated_at=row.updated_at,
    )
    return prefs


async def get(user_id: int) -> dict[str, Any]:
    async with db_client.async_session() as session:
        row = await session.get(NotificationPreferencesModel, user_id)
    return _merge(user_id, row)


def _validate(changes: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    allowed = {"channels", "topics", "quiet_start", "quiet_end", "private_previews"}
    unknown = set(changes) - allowed
    if unknown:
        raise PreferencesInvalid(f"Unknown setting: {', '.join(sorted(unknown))}")
    out = {
        "channels": dict(current["channels"]),
        "topics": {k: dict(v) for k, v in current["topics"].items()},
        "quiet_start": current["quiet_start"],
        "quiet_end": current["quiet_end"],
        "private_previews": current["private_previews"],
    }
    for name, value in (changes.get("channels") or {}).items():
        if name not in CHANNELS or not isinstance(value, bool):
            raise PreferencesInvalid(f"Unknown channel: {name}")
        out["channels"][name] = value
    for name, value in (changes.get("topics") or {}).items():
        if name not in TOPICS or not isinstance(value, dict):
            raise PreferencesInvalid(f"Unknown topic: {name}")
        if "on" in value:
            if not isinstance(value["on"], bool):
                raise PreferencesInvalid("A topic is on or off.")
            out["topics"][name]["on"] = value["on"]
        if "snoozed_until" in value:
            until = value["snoozed_until"]
            if until is not None:
                try:
                    parsed = datetime.fromisoformat(str(until))
                except ValueError as exc:
                    raise PreferencesInvalid("Snooze needs a date and time.") from exc
                if parsed.tzinfo is None:
                    raise PreferencesInvalid("Snooze needs a timezone.")
                if parsed - _now() > timedelta(days=30):
                    raise PreferencesInvalid("Snooze for at most 30 days.")
                until = parsed.astimezone(UTC).isoformat()
            out["topics"][name]["snoozed_until"] = until
    for key in ("quiet_start", "quiet_end"):
        if key in changes:
            value = changes[key]
            if value is not None and not _TIME.match(str(value)):
                raise PreferencesInvalid("Quiet hours are HH:MM.")
            out[key] = value
    if (out["quiet_start"] is None) != (out["quiet_end"] is None):
        raise PreferencesInvalid("Quiet hours need a start and an end.")
    if "private_previews" in changes:
        if not isinstance(changes["private_previews"], bool):
            raise PreferencesInvalid("Private previews are on or off.")
        out["private_previews"] = changes["private_previews"]
    return out


async def save(
    user_id: int, changes: dict[str, Any], *, revision: int
) -> dict[str, Any]:
    current = await get(user_id)
    if revision != current["revision"]:
        raise Conflict(current)
    values = _validate(changes, current)
    async with db_client.async_session() as session:
        if current["revision"] == 0:
            result = await session.execute(
                insert(NotificationPreferencesModel)
                .values(user_id=user_id, revision=1, updated_at=_now(), **values)
                .on_conflict_do_nothing(index_elements=["user_id"])
            )
        else:
            result = await session.execute(
                update(NotificationPreferencesModel)
                .where(
                    NotificationPreferencesModel.user_id == user_id,
                    NotificationPreferencesModel.revision == revision,
                )
                .values(revision=revision + 1, updated_at=_now(), **values)
            )
        await session.commit()
    if not result.rowcount:
        # A second tab saved between our read and our write.
        raise Conflict(await get(user_id))
    return await get(user_id)


# --- devices ----------------------------------------------------------------


async def devices(user_id: int) -> list[dict[str, Any]]:
    async with db_client.async_session() as session:
        rows = await session.scalars(
            select(PushSubscriptionModel)
            .where(PushSubscriptionModel.user_id == user_id)
            .order_by(PushSubscriptionModel.id.desc())
            .limit(50)
        )
        return [
            {
                "id": r.id,
                "label": r.device_label or "A browser",
                "created_at": r.created_at,
                "last_success_at": r.last_success_at,
                "last_failure_at": r.last_failure_at,
                # Revoked when the browser said the permission is gone.
                "state": "revoked"
                if r.revoked_at
                else ("failing" if r.failure_code else "active"),
            }
            for r in rows
        ]


async def subscribe(
    user_id: int, *, endpoint: str, p256dh: str, auth: str, device_label: str | None
) -> dict[str, Any]:
    if not push.endpoint_allowed(endpoint):
        raise PreferencesInvalid("That is not a browser push address.")
    if not p256dh or not auth or len(p256dh) > 255 or len(auth) > 255:
        raise PreferencesInvalid("The browser's push keys are missing.")
    values = {
        "user_id": user_id,
        "p256dh": p256dh,
        "auth": auth,
        "device_label": (device_label or "")[:80] or None,
        "revoked_at": None,
        "failure_code": None,
        "created_at": _now(),
    }
    async with db_client.async_session() as session:
        # One endpoint is one browser. Signing in as someone else on the
        # same browser moves it to them; the first person stops receiving.
        await session.execute(
            insert(PushSubscriptionModel)
            .values(endpoint=endpoint, **values)
            .on_conflict_do_update(index_elements=["endpoint"], set_=values)
        )
        await session.commit()
    return {"devices": await devices(user_id)}


async def unsubscribe(user_id: int, subscription_id: int) -> bool:
    async with db_client.async_session() as session:
        result = await session.execute(
            update(PushSubscriptionModel)
            .where(
                PushSubscriptionModel.id == subscription_id,
                PushSubscriptionModel.user_id == user_id,
                PushSubscriptionModel.revoked_at.is_(None),
            )
            .values(revoked_at=_now())
        )
        await session.commit()
    return bool(result.rowcount)


# --- delivering -------------------------------------------------------------


async def _timezone(user_id: int) -> ZoneInfo:
    try:
        from api.services import member_preferences

        tz = (await member_preferences.get(user_id)).get("timezone")
        return ZoneInfo(tz or DEFAULT_TIMEZONE)
    except Exception:  # noqa: BLE001
        return ZoneInfo(DEFAULT_TIMEZONE)


def in_quiet_hours(start: str | None, end: str | None, local: datetime) -> bool:
    if not start or not end:
        return False
    s = time.fromisoformat(start)
    e = time.fromisoformat(end)
    now = local.time()
    if s == e:
        return False
    if s < e:
        return s <= now < e
    return now >= s or now < e  # across midnight


async def _claim(
    user_id: int, topic: str, channel: str, dedupe_key: str, outcome: str
) -> bool:
    async with db_client.async_session() as session:
        result = await session.execute(
            insert(NotificationDeliveryModel)
            .values(
                user_id=user_id,
                topic=topic,
                channel=channel,
                dedupe_key=dedupe_key,
                outcome=outcome,
            )
            .on_conflict_do_nothing(constraint="uq_notification_delivery")
        )
        await session.commit()
    return bool(result.rowcount)


async def _settle(user_id: int, channel: str, dedupe_key: str, outcome: str) -> None:
    async with db_client.async_session() as session:
        await session.execute(
            update(NotificationDeliveryModel)
            .where(
                NotificationDeliveryModel.user_id == user_id,
                NotificationDeliveryModel.channel == channel,
                NotificationDeliveryModel.dedupe_key == dedupe_key,
            )
            .values(outcome=outcome)
        )
        await session.commit()


async def _sent_today(user_id: int, topic: str, local_midnight_utc: datetime) -> int:
    async with db_client.async_session() as session:
        return int(
            await session.scalar(
                select(
                    func.count(func.distinct(NotificationDeliveryModel.dedupe_key))
                ).where(
                    NotificationDeliveryModel.user_id == user_id,
                    NotificationDeliveryModel.topic == topic,
                    NotificationDeliveryModel.outcome.in_(("sent", "partial")),
                    NotificationDeliveryModel.created_at >= local_midnight_utc,
                )
            )
            or 0
        )


async def _push(user_id: int, payload: dict[str, Any]) -> str:
    async with db_client.async_session() as session:
        subs = list(
            await session.scalars(
                select(PushSubscriptionModel).where(
                    PushSubscriptionModel.user_id == user_id,
                    PushSubscriptionModel.revoked_at.is_(None),
                )
            )
        )
    if not push.configured():
        return "needs_setup"
    if not subs:
        return "no_device"
    results = []
    for sub in subs:
        result = await push.send(sub, payload)
        results.append(result)
        values: dict[str, Any]
        if result == push.OK:
            values = {"last_success_at": _now(), "failure_code": None}
        elif result == push.GONE:
            # The browser says the permission is gone: reflect it (screen 21).
            values = {"revoked_at": _now(), "failure_code": "permission_revoked"}
        else:
            values = {"last_failure_at": _now(), "failure_code": result[:64]}
        async with db_client.async_session() as session:
            await session.execute(
                update(PushSubscriptionModel)
                .where(PushSubscriptionModel.id == sub.id)
                .values(**values)
            )
            await session.commit()
    if all(r == push.OK for r in results):
        return "sent"
    if any(r == push.OK for r in results):
        return "partial"
    return "failed"


async def _email(user_id: int, title: str, body: str, link: str | None) -> str:
    from api.services.messaging.email import email_is_configured, send_email

    if not email_is_configured():
        return "needs_setup"
    user = await db_client.get_user_by_id(user_id)
    to = getattr(user, "email", None)
    if not to:
        return "no_address"
    text = body + (f"\n\nOpen Decibyl: {link}" if link else "")
    result = await send_email(
        to=to, subject=title, body_text=text, sender="notifications"
    )
    return "sent" if result.ok else "failed"


async def _channel(user_id: int, title: str, body: str) -> str:
    from sqlalchemy import select as _select

    from api.db.channel_identity_models import ChannelIdentityModel
    from api.services.messaging.channels import dispatch

    async with db_client.async_session() as session:
        identity = await session.scalar(
            _select(ChannelIdentityModel)
            .where(ChannelIdentityModel.user_id == user_id)
            .order_by(ChannelIdentityModel.id.desc())
            .limit(1)
        )
    if identity is None:
        return "no_device"
    try:
        adapter = dispatch.adapter_for(identity.channel)
        if not adapter.enabled():
            return "needs_setup"
        ref = dict(identity.conversation_ref or {})
        ref.setdefault("to", identity.external_id)
        ref.setdefault("organization_id", identity.organization_id)
        ok = await adapter.send_text(ref, f"{title}\n{body}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Notice on {} failed: {}", identity.channel, exc)
        return "failed"
    return "sent" if ok else "failed"


async def notify(
    user_id: int,
    *,
    topic: str,
    title: str,
    body: str,
    link: str | None = None,
    dedupe_key: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Tell one person one thing on the channels they chose. Returns the
    outcome per channel (or ``{"all": reason}`` when nothing was tried).
    Never raises; does nothing while the flag is off everywhere."""
    if topic not in TOPICS or not features.on_anywhere(FLAG):
        return {"all": "off"}
    try:
        return await _notify(
            user_id, topic=topic, title=title, body=body, link=link,
            dedupe_key=dedupe_key[:128], now=now or _now(),
        )  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - a notice must never break its cause
        logger.warning("Could not notify user {} ({}): {}", user_id, topic, exc)
        return {"all": "failed"}


async def _notify(
    user_id: int,
    *,
    topic: str,
    title: str,
    body: str,
    link: str | None,
    dedupe_key: str,
    now: datetime,
) -> dict[str, str]:
    prefs = await get(user_id)
    setting = prefs["topics"][topic]
    reason = None
    if not setting["on"]:
        reason = "off"
    elif (
        setting.get("snoozed_until")
        and datetime.fromisoformat(setting["snoozed_until"]) > now
    ):
        reason = "snoozed"
    tz = await _timezone(user_id)
    local = now.astimezone(tz)
    if reason is None and topic in OPTIONAL:
        from api import constants

        midnight = datetime.combine(local.date(), time(0), tzinfo=tz).astimezone(UTC)
        if (
            await _sent_today(user_id, topic, midnight)
            >= constants.NOTIFY_SUGGESTION_DAILY_CAP
        ):
            reason = "capped"
    if (
        reason is None
        and not TOPICS[topic]["requested"]
        and in_quiet_hours(prefs["quiet_start"], prefs["quiet_end"], local)
    ):
        reason = "quiet"
    if reason is not None:
        await _claim(user_id, topic, "all", dedupe_key, reason)
        return {"all": reason}
    chosen = [c for c in CHANNELS if prefs["channels"].get(c)]
    if not chosen:
        return {"all": "no_channel"}
    shown_title, shown_body = title, body
    if prefs["private_previews"]:
        shown_title, shown_body = GENERIC_TITLE, GENERIC_BODY
    outcomes: dict[str, str] = {}
    for channel in chosen:
        if not await _claim(user_id, topic, channel, dedupe_key, "pending"):
            outcomes[channel] = "duplicate"
            continue
        if channel == "push":
            outcome = await _push(
                user_id,
                {
                    "title": shown_title,
                    "body": shown_body,
                    "url": link or "/",
                    "tag": topic,
                },
            )
        elif channel == "email":
            outcome = await _email(user_id, shown_title, shown_body, link)
        else:
            outcome = await _channel(user_id, shown_title, shown_body)
        await _settle(user_id, channel, dedupe_key, outcome[:16])
        outcomes[channel] = outcome
    return outcomes


async def send_test(user_id: int) -> dict[str, str]:
    """One labelled test push to the person's own devices. Not a topic, so
    it is not counted against anything and creates nothing recurring."""
    outcome = await _push(
        user_id,
        {
            "title": "Decibyl test",
            "body": "Test notification. Push works on this device.",
            "url": "/settings/notifications",
            "tag": "test",
        },
    )
    return {"push": outcome}
