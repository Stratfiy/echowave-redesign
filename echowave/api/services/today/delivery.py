"""Putting one occurrence of a reminder or a brief in front of a person.

A delivery row is written *before* anything is sent, unique per (what,
which occurrence, channel), so a tick that runs twice, a worker that comes
back after a restart, or a Test pressed in two tabs cannot send the same
occurrence twice on one channel. The row then records what actually
happened, in words a person can read:

* ``sent`` -- in the app, where the evidence is the row itself;
* ``accepted`` -- a provider took it (WhatsApp) but has not confirmed it
  reached the phone; never shown as delivered;
* ``needs_setup`` -- the channel cannot be used yet (WhatsApp not configured
  or not linked, phone notifications not built) -- said, never faked;
* ``skipped`` -- with the reason (WhatsApp's 24-hour window is closed);
* ``failed`` -- with the reason;
* ``unknown`` -- the send broke midway; we do not know, and it is never
  retried blind.

Sending to a person's *own* WhatsApp at the time they confirmed is the
thing they asked for in the editor (the confirmation is the exact schedule
and channel), so it is not a card. It spends the person's daily outbound
allowance like any send (operational quotas).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.today_models import TodayDeliveryModel

IN_APP = "in_app"
WHATSAPP = "whatsapp"
PUSH = "push"

AVAILABLE = "available"
NEEDS_SETUP = "needs_setup"
UNAVAILABLE = "unavailable"

#: What the person reads when a channel cannot be used yet.
PUSH_NOT_READY = (
    "Phone notifications are not set up yet. You will see this in Today in "
    "the meantime."
)
PUSH_NOT_ON = (
    "Phone notifications are not switched on here yet. You will see this in "
    "Today in the meantime."
)
PUSH_NO_DEVICE = "Turn on notifications on your phone or browser in Settings first."
WHATSAPP_NOT_CONFIGURED = "WhatsApp is not set up for Decibyl yet."
WHATSAPP_NOT_LINKED = "Link your WhatsApp to Decibyl to get this there."
WHATSAPP_WINDOW_CLOSED = (
    "WhatsApp only lets Decibyl message you within 24 hours of your last "
    "message to it. Say hello on WhatsApp to open it again."
)


async def _push_devices(user_id: int) -> int:
    from sqlalchemy import func

    from api.db.identity_models import PushSubscriptionModel

    async with db_client.async_session() as session:
        return int(
            await session.scalar(
                select(func.count(PushSubscriptionModel.id)).where(
                    PushSubscriptionModel.user_id == user_id,
                    PushSubscriptionModel.revoked_at.is_(None),
                )
            )
            or 0
        )


async def _send_push(user_id: int, text: str) -> tuple[str, str | None, str | None]:
    """Through the identity stream's push sender, so devices, revoked
    permissions and failures are kept in one place. Lock-screen text stays
    generic while the person's private previews are on (their default).
    (status, reason_code, evidence)."""
    from api.services.identity import notifications

    prefs = await notifications.get(user_id)
    title, body = "Decibyl", text
    if prefs.get("private_previews", True):
        title, body = notifications.GENERIC_TITLE, notifications.GENERIC_BODY
    outcome = await notifications._push(
        user_id, {"title": title, "body": body[:500], "url": "/tasks", "tag": "today"}
    )
    if outcome in ("sent", "partial"):
        # A push service took it; that is not proof the person saw it.
        return "accepted", None, f"push:{outcome}"
    if outcome in ("needs_setup", "no_device"):
        return NEEDS_SETUP, f"push_{outcome}", None
    return "failed", "push_failed", None


async def _whatsapp_number(organization_id: int, user_id: int) -> str | None:
    from api.services.messaging.channels import identities

    for identity in await identities.for_member(
        organization_id=organization_id, user_id=user_id
    ):
        if identity.channel == WHATSAPP:
            return str(identity.conversation_ref.get("to") or identity.external_id)
    return None


async def channel_state(
    organization_id: int, user_id: int, channel: str
) -> dict[str, Any]:
    """Whether ``channel`` can reach this person now, and if not, why."""
    if channel == IN_APP:
        return {"channel": channel, "state": AVAILABLE, "reason": None}
    if channel == PUSH:
        # Web push is the identity stream's (services/identity): its switch,
        # the operator's VAPID keys, and at least one device this person
        # allowed. Any one missing is "needs setup", said in words. A phone
        # running the native app (``mobile_push``) counts as a device too.
        from api.services import features
        from api.services.identity import push

        if not features.is_on("identity_notifications", organization_id):
            return {"channel": channel, "state": NEEDS_SETUP, "reason": PUSH_NOT_ON}
        from api.services.identity import mobile_push

        phones = mobile_push.enabled(organization_id)
        if not push.configured() and not phones:
            return {"channel": channel, "state": NEEDS_SETUP, "reason": PUSH_NOT_READY}
        if not await _push_devices(user_id) and not (
            phones and await mobile_push.has_device(user_id)
        ):
            return {"channel": channel, "state": NEEDS_SETUP, "reason": PUSH_NO_DEVICE}
        return {"channel": channel, "state": AVAILABLE, "reason": None}
    if channel == WHATSAPP:
        from api.services.messaging import platform_whatsapp

        if not platform_whatsapp.is_configured():
            return {
                "channel": channel,
                "state": NEEDS_SETUP,
                "reason": WHATSAPP_NOT_CONFIGURED,
            }
        if not await _whatsapp_number(organization_id, user_id):
            return {
                "channel": channel,
                "state": NEEDS_SETUP,
                "reason": WHATSAPP_NOT_LINKED,
            }
        return {"channel": channel, "state": AVAILABLE, "reason": None}
    return {
        "channel": channel,
        "state": UNAVAILABLE,
        "reason": "Not a channel we deliver on.",
    }


async def _claim(
    *,
    organization_id: int,
    user_id: int,
    subject_kind: str,
    subject_id: int,
    occurrence_key: str,
    channel: str,
    is_test: bool,
    still_due: Callable[[Any], Awaitable[bool]] | None = None,
) -> int | None:
    """Write the row first. None when this occurrence was already claimed.

    ``still_due`` runs in the claim's own transaction, before the insert: a
    subject cancelled or moved since the caller read it is not claimed
    (``_Withdrawn``). It may lock its row, so a change made at the same
    moment either lands first (nothing is claimed) or waits for the claim
    (and changes what comes after this occurrence)."""
    stmt = (
        insert(TodayDeliveryModel)
        .values(
            organization_id=organization_id,
            user_id=user_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            occurrence_key=occurrence_key,
            channel=channel,
            is_test=is_test,
            status="queued",
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(constraint="uq_today_delivery_occurrence")
        .returning(TodayDeliveryModel.id)
    )
    async with db_client.async_session() as session:
        if still_due is not None and not await still_due(session):
            await session.rollback()
            raise _Withdrawn()
        delivery_id = await session.scalar(stmt)
        await session.commit()
    return int(delivery_id) if delivery_id is not None else None


class _Withdrawn(Exception):
    """The subject stopped being due between the read and the claim."""


async def _finish(delivery_id: int, **values: Any) -> None:
    from sqlalchemy import update

    async with db_client.async_session() as session:
        await session.execute(
            update(TodayDeliveryModel)
            .where(TodayDeliveryModel.id == delivery_id)
            .values(**values)
        )
        await session.commit()


async def _send_whatsapp(
    organization_id: int, to: str, body: str
) -> tuple[str, str | None, str | None]:
    """(status, reason_code, evidence). Separate so tests can fake the
    provider; production goes through the platform sender."""
    from api.services.billing import messaging_charges
    from api.services.messaging import platform_whatsapp
    from api.services.messaging.send import send_message

    try:
        result = await send_message(
            provider=platform_whatsapp.PROVIDER,
            credentials=platform_whatsapp.credentials(),
            to=to,
            from_="",
            body=body[:1600],
        )
    except Exception as exc:  # noqa: BLE001 - outcome unknown, never retried blind
        logger.error("Today WhatsApp delivery broke midway: {}", exc)
        return "unknown", "provider_error", None
    if not result.ok:
        return "failed", "provider_refused", None
    try:
        async with db_client.async_session() as session:
            await messaging_charges.debit_message(
                session,
                organization_id=organization_id,
                message_id=result.message_id or f"today:{to}:{hash(body)}",
                node_name="Decibyl",
                category=messaging_charges.SERVICE,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - the send happened; billing is logged
        logger.error("Could not charge the Today WhatsApp message: {}", exc)
    return "accepted", None, f"whatsapp:{result.message_id or 'accepted'}"


async def deliver(
    *,
    organization_id: int,
    user_id: int,
    subject_kind: str,
    subject_id: int,
    occurrence_key: str,
    channel: str,
    text: str,
    is_test: bool = False,
    still_due: Callable[[Any], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """Deliver one occurrence on one channel, once. Never raises.

    With ``still_due`` (see ``_claim``), an occurrence that is no longer due
    when it is claimed -- cancelled, paused or moved after the caller read
    it -- is not sent and leaves no row: ``{"withdrawn": True}``."""
    try:
        delivery_id = await _claim(
            organization_id=organization_id,
            user_id=user_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            occurrence_key=occurrence_key,
            channel=channel,
            is_test=is_test,
            still_due=still_due,
        )
    except _Withdrawn:
        return {
            "channel": channel,
            "status": None,
            "occurrence_key": occurrence_key,
            "duplicate": False,
            "withdrawn": True,
        }
    if delivery_id is None:
        async with db_client.async_session() as session:
            existing = await session.scalar(
                select(TodayDeliveryModel).where(
                    TodayDeliveryModel.subject_kind == subject_kind,
                    TodayDeliveryModel.subject_id == subject_id,
                    TodayDeliveryModel.occurrence_key == occurrence_key,
                    TodayDeliveryModel.channel == channel,
                )
            )
        return {**(view(existing) if existing else {}), "duplicate": True}

    now = datetime.now(UTC)
    status, reason, detail, evidence = "sent", None, None, None
    try:
        state = await channel_state(organization_id, user_id, channel)
        if state["state"] != AVAILABLE:
            status, reason, detail = (
                NEEDS_SETUP,
                f"{channel}_needs_setup",
                state["reason"],
            )
        elif channel == IN_APP:
            evidence = f"in_app:{delivery_id}"
        elif channel == PUSH:
            status, reason, evidence = await _send_push(user_id, text)
        elif channel == WHATSAPP:
            from api.services.messaging import whatsapp_inbound

            to = await _whatsapp_number(organization_id, user_id)
            if await whatsapp_inbound.session_open(str(to)) is False:
                status, reason, detail = (
                    "skipped",
                    "outside_whatsapp_window",
                    WHATSAPP_WINDOW_CLOSED,
                )
            else:
                from api.services import quotas

                try:
                    await quotas.consume(user_id, quotas.OUTBOUND_MESSAGES)
                except quotas.QuotaExceeded as exc:
                    status, reason, detail = (
                        "failed",
                        "quota_outbound_messages",
                        str(exc)[:300],
                    )
                else:
                    status, reason, evidence = await _send_whatsapp(
                        organization_id, str(to), text
                    )
                    if status == "unknown":
                        from api.services.workflow import task_ledger

                        detail = task_ledger.UNKNOWN_COPY
    except Exception as exc:  # noqa: BLE001 - recorded on the row, never raised
        logger.exception("Today delivery {} failed: {}", delivery_id, exc)
        status, reason, detail = (
            "failed",
            "internal_error",
            "Something went wrong on our side.",
        )

    await _finish(
        delivery_id,
        status=status,
        reason_code=reason,
        detail=detail,
        evidence=evidence,
        delivered_at=now if status in ("sent", "accepted") else None,
    )
    if subject_kind == "reminder" and not is_test:
        from api.services import events

        await events.emit(
            "reminder_delivered"
            if status in ("sent", "accepted")
            else "reminder_failed",
            user_id=user_id,
            organization_id=organization_id,
            task_id=f"reminder:{subject_id}",
            properties={
                "channel": channel,
                "status": status,
                **({"reason_code": reason} if reason else {}),
            },
        )
    return {
        "id": delivery_id,
        "channel": channel,
        "status": status,
        "reason_code": reason,
        "detail": detail,
        "evidence": evidence,
        "is_test": is_test,
        "occurrence_key": occurrence_key,
        "duplicate": False,
    }


def view(row: TodayDeliveryModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "subject_kind": row.subject_kind,
        "subject_id": row.subject_id,
        "occurrence_key": row.occurrence_key,
        "channel": row.channel,
        "status": row.status,
        "reason_code": row.reason_code,
        "detail": row.detail,
        "evidence": row.evidence,
        "is_test": row.is_test,
        "at": row.created_at.isoformat() if row.created_at else None,
        "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None,
    }


async def for_subject(
    organization_id: int,
    user_id: int,
    subject_kind: str,
    subject_id: int,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(TodayDeliveryModel)
                .where(
                    TodayDeliveryModel.organization_id == organization_id,
                    TodayDeliveryModel.user_id == user_id,
                    TodayDeliveryModel.subject_kind == subject_kind,
                    TodayDeliveryModel.subject_id == subject_id,
                )
                .order_by(TodayDeliveryModel.id.desc())
                .limit(limit)
            )
        ).scalars()
        return [view(r) for r in rows]


__all__ = [
    "IN_APP",
    "PUSH",
    "WHATSAPP",
    "channel_state",
    "deliver",
    "for_subject",
    "view",
]
