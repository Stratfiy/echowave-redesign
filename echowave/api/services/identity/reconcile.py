"""Finding out what happened to a send whose outcome is unknown.

The controls stream marks a card ``outcome_unknown`` when a send broke
midway or its worker died (CONTROLS.md, section 3), and never retries it.
This asks each provider what actually happened and settles the card --
``done`` with the provider's evidence, or ``failed`` with its reason -- and
only ever from ``outcome_unknown`` (the ledger allows completed or failed
from there, nothing else). Nothing is ever sent again.

Per provider:

* ``identity_email`` -- our own send record (written before the mail server
  is called) and the provider's delivery / bounce events.
* ``whatsapp`` -- Meta's status webhooks, matched on the card's key, which
  is sent with the message as ``biz_opaque_callback_data`` while this flag
  is on.
* ``composio_accounts`` (a disconnect) -- whether the account is still
  listed at Composio.
* ``carrier`` (a number request) -- whether the number is on the account.
* anything else (a connected app's send, an email attachment) -- no
  provider can be asked, so after :data:`ASK_AFTER` the person who confirmed
  it is asked "Did it arrive?" and their answer settles the card, recorded
  as theirs.

Each settled card says how it was settled, and the person who confirmed it
is notified (topic ``task_updates``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.identity_models import DeliveryReceiptModel
from api.db.models import AgentEventModel
from api.enums import AgentEventKind
from api.services import features

FLAG = "identity_reconciliation"

DELIVERED = "delivered"
NOT_DELIVERED = "not_delivered"
UNKNOWN = "unknown"

#: How long a provider is given before the person is asked instead.
ASK_AFTER = timedelta(minutes=30)
#: How far back the sweep looks. An older unknown card stays as it is, for a
#: person to settle; it is never guessed at.
LOOK_BACK = timedelta(days=7)
BATCH = 200


@dataclass
class Verdict:
    outcome: str
    evidence: str
    reason_code: str | None = None


def enabled(organization_id: int | None) -> bool:
    return features.is_on(FLAG, organization_id)


def callback_key(organization_id: int, payload: dict[str, Any]) -> str | None:
    """The key a WhatsApp send carries for its status webhooks, or None."""
    key = payload.get("idempotency_key")
    if not key or not enabled(organization_id):
        return None
    return f"decibyl:{key}"


async def record_whatsapp_statuses(payload: dict[str, Any]) -> int:
    """Keep Meta's delivery statuses as receipts. Never raises."""
    if not features.on_anywhere(FLAG):
        return 0
    rows = []
    try:
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                for status in (change.get("value") or {}).get("statuses") or []:
                    message_id = str(status.get("id") or "")[:255]
                    state = str(status.get("status") or "").lower()
                    if not message_id or state not in (
                        "sent",
                        "delivered",
                        "read",
                        "failed",
                    ):
                        continue
                    errors = status.get("errors") or [{}]
                    rows.append(
                        {
                            "provider": "whatsapp",
                            "provider_message_id": message_id,
                            "idempotency_key": (
                                str(status.get("biz_opaque_callback_data") or "")[:128]
                                or None
                            ),
                            "status": state,
                            "detail_code": str(errors[0].get("code") or "")[:64]
                            or None,
                        }
                    )
    except (AttributeError, TypeError) as exc:
        logger.warning("Unreadable WhatsApp statuses: {}", exc)
        return 0
    if not rows:
        return 0
    try:
        async with db_client.async_session() as session:
            await session.execute(
                insert(DeliveryReceiptModel)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_delivery_receipt")
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - a receipt must not fail Meta's call
        logger.warning("Could not store WhatsApp receipts: {}", exc)
        return 0
    return len(rows)


def provider_of(payload: dict[str, Any]) -> str:
    action = payload.get("action")
    args = payload.get("args") or {}
    if action == "send_identity_email":
        return "identity_email"
    if action == "disconnect_app":
        return "composio_accounts"
    if action == "request_number":
        return "carrier"
    if action == "send_document" and args.get("channel") == "whatsapp":
        return "whatsapp"
    if action == "run_tool":
        return f"composio:{(args.get('toolkit') or 'app').lower()}"
    if action == "send_document":
        return "smtp"
    return str(action or "unknown")


# --- the reconcilers --------------------------------------------------------


async def _identity_email(event: Any, payload: dict[str, Any]) -> Verdict:
    from api.services.identity import email_identity

    sent = await email_identity.send_for_card(event.id)
    if sent is None:
        # The record is written before the mail server is called; none means
        # it never got that far.
        return Verdict(NOT_DELIVERED, "It never reached the mail server.", "never_sent")
    if sent.state == "delivered":
        return Verdict(
            DELIVERED, f"The mail provider reported it delivered ({sent.message_id})."
        )
    if sent.state == "accepted":
        return Verdict(DELIVERED, f"The mail server accepted it ({sent.message_id}).")
    if sent.state in ("bounced", "complained", "failed"):
        return Verdict(
            NOT_DELIVERED, f"The mail server reported it {sent.state}.", sent.state
        )
    return Verdict(UNKNOWN, "")


async def _whatsapp(event: Any, payload: dict[str, Any]) -> Verdict:
    key = payload.get("idempotency_key")
    if not key:
        return Verdict(UNKNOWN, "")
    async with db_client.async_session() as session:
        statuses = set(
            await session.scalars(
                select(DeliveryReceiptModel.status).where(
                    DeliveryReceiptModel.provider == "whatsapp",
                    DeliveryReceiptModel.idempotency_key == f"decibyl:{key}",
                )
            )
        )
    if statuses & {"delivered", "read"}:
        return Verdict(DELIVERED, "WhatsApp reported it delivered.")
    if "failed" in statuses:
        return Verdict(NOT_DELIVERED, "WhatsApp reported it failed.", "whatsapp_failed")
    return Verdict(UNKNOWN, "")


async def _composio_accounts(event: Any, payload: dict[str, Any]) -> Verdict:
    from api.services.integrations.composio import client

    args = payload.get("args") or {}
    owner = payload.get("private_to")
    try:
        accounts = await client.accounts_with_status(
            event.organization_id,
            user_id=owner if args.get("scope") == "mine" else None,
        )
    except Exception:  # noqa: BLE001 - not known yet; asked again next sweep
        return Verdict(UNKNOWN, "")
    still = any(
        a["connected_account_id"] == args.get("connected_account_id") for a in accounts
    )
    if still:
        return Verdict(
            NOT_DELIVERED, "It is still connected at the app.", "still_connected"
        )
    return Verdict(DELIVERED, "The app no longer lists the connection.")


async def _carrier(event: Any, payload: dict[str, Any]) -> Verdict:
    from api.db.models import TelephonyPhoneNumberModel

    address = str((payload.get("args") or {}).get("address") or "")
    async with db_client.async_session() as session:
        number = await session.scalar(
            select(TelephonyPhoneNumberModel).where(
                TelephonyPhoneNumberModel.organization_id == event.organization_id,
                TelephonyPhoneNumberModel.address == address,
            )
        )
    if number is None:
        return Verdict(NOT_DELIVERED, "The number is not on the account.", "not_bought")
    if number.carrier_number_id:
        return Verdict(DELIVERED, f"The carrier lists {address} on the account.")
    return Verdict(UNKNOWN, "")


RECONCILERS = {
    "identity_email": _identity_email,
    "whatsapp": _whatsapp,
    "composio_accounts": _composio_accounts,
    "carrier": _carrier,
}


# --- settling ---------------------------------------------------------------


def _confirmer(payload: dict[str, Any]) -> int | None:
    by = (payload.get("confirmed") or {}).get("by")
    return by if isinstance(by, int) and by > 0 else None


async def _settle(
    event: Any, payload: dict[str, Any], verdict: Verdict, *, by: str
) -> bool:
    from api.services.identity import notifications
    from api.services.workflow import actions

    now = datetime.now(UTC).isoformat()
    payload["reconciled"] = {
        "provider": provider_of(payload),
        "outcome": verdict.outcome,
        "evidence": verdict.evidence,
        "by": by,
        "at": now,
    }
    payload.pop("reconcile", None)
    if verdict.outcome == DELIVERED:
        payload["state"] = actions.DONE
        payload["done"] = {"at": now, "note": verdict.evidence}
        payload.pop("error", None)
        line = f"{payload.get('label')}: it went. {verdict.evidence}"
        event_name = "task_completed"
    else:
        payload["state"] = actions.FAILED
        payload["error"] = f"It did not go. {verdict.evidence}"
        payload["reason_code"] = verdict.reason_code or "not_delivered"
        line = f"{payload.get('label')}: it did not go. {verdict.evidence}"
        event_name = "task_failed"
    try:
        await actions._move(event, actions.OUTCOME_UNKNOWN, payload)
    except actions.ActionError:
        return False  # settled by someone else meanwhile
    await actions._say(event, line)
    confirmer = _confirmer(payload)
    await actions._emit(event_name, event, payload, confirmer)
    if confirmer:
        await notifications.notify(
            confirmer,
            topic="task_updates",
            title="We checked a send",
            body=line[:200],
            link="/overview",
            dedupe_key=f"reconciled:{event.id}",
        )
    return True


async def _ask_person(event: Any, payload: dict[str, Any]) -> None:
    from api.services.identity import notifications
    from api.services.workflow import actions

    if (payload.get("reconcile") or {}).get("asked_at"):
        return
    payload["reconcile"] = {
        "needs_person": True,
        "asked_at": datetime.now(UTC).isoformat(),
        "provider": provider_of(payload),
        # No provider can answer later either: the sweep stops looking.
        "final": provider_of(payload) not in RECONCILERS,
    }
    try:
        await actions._move(event, actions.OUTCOME_UNKNOWN, payload)
    except actions.ActionError:
        return
    await actions._say(
        event,
        f"{payload.get('label')}: we cannot ask the app whether it arrived. "
        "Please check, then tell us on the card: it arrived, or it did not.",
    )
    confirmer = _confirmer(payload)
    if confirmer:
        await notifications.notify(
            confirmer,
            topic="task_updates",
            title="Did it arrive?",
            body="Check whether a send arrived, then tell Decibyl.",
            link="/overview",
            dedupe_key=f"reconcile_ask:{event.id}",
        )


def _due(payload: dict[str, Any]) -> datetime | None:
    fires = payload.get("fires_at")
    try:
        return datetime.fromisoformat(fires) if fires else None
    except ValueError:
        return None


async def sweep(now: datetime | None = None) -> dict[str, int]:
    """One pass over unknown cards. Returns counts by what happened."""
    counts = {"delivered": 0, "not_delivered": 0, "asked": 0, "waiting": 0}
    if not features.on_anywhere(FLAG):
        return counts
    now = now or datetime.now(UTC)
    async with db_client.async_session() as session:
        rows = (
            await session.execute(
                select(AgentEventModel.id, AgentEventModel.organization_id)
                .where(
                    AgentEventModel.kind == AgentEventKind.ACTION_PROPOSED.value,
                    AgentEventModel.at >= now - LOOK_BACK,
                    text("agent_events.payload->>'state' = 'outcome_unknown'"),
                    text(
                        "coalesce(agent_events.payload->'reconcile'->>'final', 'false') <> 'true'"
                    ),
                )
                .order_by(AgentEventModel.id)
                .limit(BATCH)
            )
        ).all()
    for event_id, organization_id in rows:
        if not enabled(organization_id):
            continue
        event = await db_client.get_agent_event(
            event_id, organization_id=organization_id
        )
        if event is None:
            continue
        payload = dict(event.payload or {})
        reconciler = RECONCILERS.get(provider_of(payload))
        verdict = Verdict(UNKNOWN, "")
        if reconciler is not None:
            try:
                verdict = await reconciler(event, payload)
            except Exception as exc:  # noqa: BLE001 - one card is not the sweep
                logger.warning("Reconciling card {} failed: {}", event_id, exc)
        if verdict.outcome in (DELIVERED, NOT_DELIVERED):
            if await _settle(event, payload, verdict, by=provider_of(payload)):
                counts[verdict.outcome] += 1
            continue
        due = _due(payload) or now
        if now - due >= ASK_AFTER:
            if not (payload.get("reconcile") or {}).get("asked_at"):
                await _ask_person(event, payload)
                counts["asked"] += 1
            else:
                counts["waiting"] += 1
        else:
            counts["waiting"] += 1
    return counts


class NotYours(Exception):
    pass


async def person_says(
    organization_id: int, event_id: int, user_id: int, *, arrived: bool, is_admin: bool
) -> dict[str, Any]:
    """The person who confirmed the card (or its private owner, or an admin
    of the workspace) says whether it arrived. Settles it as theirs."""
    from api.services.workflow import actions

    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if event is None or event.kind != AgentEventKind.ACTION_PROPOSED.value:
        raise NotYours()
    payload = dict(event.payload or {})
    owner = payload.get("private_to")
    if owner is not None and owner != user_id:
        raise NotYours()
    if owner is None and _confirmer(payload) != user_id and not is_admin:
        raise NotYours()
    if payload.get("state") != actions.OUTCOME_UNKNOWN:
        raise ValueError("This one is already settled.")
    verdict = Verdict(
        DELIVERED if arrived else NOT_DELIVERED,
        "Checked by the person who approved it."
        if _confirmer(payload) == user_id
        else "Checked by a workspace admin.",
        None if arrived else "person_says_not_delivered",
    )
    if not await _settle(event, payload, verdict, by=f"person:{user_id}"):
        raise ValueError("This one is already settled.")
    fresh = await db_client.get_agent_event(event_id, organization_id=organization_id)
    return dict(fresh.payload or {})
