"""What a person does with a handoff card: Accept, Decline, Hand back to AI.

And the carrier-side events that move the same record: a person's leg
answered by a human, its call ending.

Every function names the organization and reads the row through it; an
escalation id from another workspace is answered as not found, the same as
one that does not exist.

**Hand back to AI** re-points the caller's live leg at a fresh agent stream
and hangs up the person's leg. The new agent run carries the person's note,
and it opens by saying it is back and what was decided, not "hello". Where
the carrier cannot move a live caller (only Plivo can today), the card says
so and offers what can be done instead.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.escalation import record

HANDBACK_TTL_SECONDS = 600
MAX_NOTE_CHARS = 1000


class EscalationError(ValueError):
    """The click cannot be honoured; the message says why, for the screen."""


class NotFound(EscalationError):
    pass


class NotSupported(EscalationError):
    pass


async def _redis():
    from api.services.telephony.call_transfer_manager import get_call_transfer_manager

    manager = await get_call_transfer_manager()
    return await manager._get_redis()


async def _row(organization_id: int, escalation_uuid: str):
    row = await db_client.get_escalation(
        escalation_uuid, organization_id=organization_id
    )
    if row is None:
        raise NotFound("That handover is not here.")
    return row


async def sync_card(row_or_id: Any, *, organization_id: int):
    """Re-read the row and write its state onto the card on the thread, so
    the card everyone opens later says what actually happened."""
    escalation_id = getattr(row_or_id, "id", row_or_id)
    fresh = await db_client.get_escalation_by_id(
        escalation_id, organization_id=organization_id
    )
    if fresh is None:
        return None
    if fresh.timeline_event_id:
        try:
            await db_client.set_agent_event_payload(
                fresh.timeline_event_id,
                organization_id=organization_id,
                payload={
                    "handoff": fresh.handoff_card or {},
                    "escalation_uuid": fresh.escalation_uuid,
                    "state": fresh.state,
                    "failure_reason": fresh.failure_reason,
                    "fallback": fresh.fallback,
                    "human_response": fresh.human_response,
                    "outcome_note": fresh.outcome_note,
                },
            )
        except Exception as exc:  # noqa: BLE001 - the row is the record
            logger.debug("Handoff card not refreshed: {}", exc)
    return fresh


async def get(organization_id: int, escalation_uuid: str) -> dict[str, Any]:
    row = await _row(organization_id, escalation_uuid)
    out = record.as_dict(row)
    out["can_hand_back"] = await _can_hand_back(row)
    return out


async def _provider_for(row):
    from api.services.telephony.webhook_guard import provider_for_run_id

    if not row.workflow_run_id:
        return None, None
    resolved = await provider_for_run_id(int(row.workflow_run_id))
    if resolved is None:
        return None, None
    return resolved


async def _can_hand_back(row) -> bool:
    if row.state != record.BRIDGED:
        return False
    try:
        provider, _ = await _provider_for(row)
    except Exception:  # noqa: BLE001
        return False
    return bool(provider is not None and provider.supports_escalation_hand_back())


async def accept(organization_id: int, escalation_uuid: str, *, user_id: int) -> dict:
    row = await _row(organization_id, escalation_uuid)
    if row.human_response is None:
        await db_client.update_escalation(
            row.id,
            organization_id=organization_id,
            human_response="accepted",
            human_user_id=user_id,
        )
    await sync_card(row, organization_id=organization_id)
    return await get(organization_id, escalation_uuid)


async def decline(organization_id: int, escalation_uuid: str, *, user_id: int) -> dict:
    """Not mine: while the caller is still waiting, the ladder moves to the
    next person (or back to the agent); once bridged, the caller goes back to
    the agent."""
    row = await _row(organization_id, escalation_uuid)
    if row.state == record.BRIDGED:
        return await hand_back(
            organization_id,
            escalation_uuid,
            user_id=user_id,
            note="The colleague who picked up could not take this call.",
            response="declined",
        )
    await db_client.update_escalation(
        row.id,
        organization_id=organization_id,
        human_response="declined",
        human_user_id=user_id,
    )
    if row.state in (record.REQUESTED, record.DIALLING, record.BRIEFING) and (
        row.current_transfer_id
    ):
        from api.services.escalation.dialer import report_leg_outcome

        await report_leg_outcome(
            row.current_transfer_id, human=False, reason="declined"
        )
    await sync_card(row, organization_id=organization_id)
    return await get(organization_id, escalation_uuid)


async def hand_back(
    organization_id: int,
    escalation_uuid: str,
    *,
    user_id: int,
    note: str,
    response: str | None = None,
) -> dict:
    row = await _row(organization_id, escalation_uuid)
    if row.state in record.TERMINAL:
        if row.handed_back_at is not None:
            return await get(organization_id, escalation_uuid)
        raise EscalationError("This call has already ended.")
    if row.state != record.BRIDGED:
        raise EscalationError(
            "The caller is not with a person yet. Decline sends them on to the "
            "next person or back to the agent."
        )
    provider, run = await _provider_for(row)
    if provider is None or not provider.supports_escalation_hand_back():
        raise NotSupported(
            "This phone carrier cannot move a caller back to the agent mid-call. "
            "Finish the call yourself, or tell the caller the agent will call "
            "them back."
        )
    redis = await _redis()
    # Two clicks, two tabs, two people: one hand back.
    if not await redis.set(
        f"escalation:handback-lock:{row.escalation_uuid}", "1", nx=True, ex=60
    ):
        return await get(organization_id, escalation_uuid)

    note = " ".join((note or "").split())[:MAX_NOTE_CHARS]
    token = secrets.token_urlsafe(24)
    card = row.handoff_card or {}
    user = await db_client.get_user_by_id(user_id) if user_id else None
    await redis.setex(
        f"escalation:handback:{token}",
        HANDBACK_TTL_SECONDS,
        json.dumps(
            {
                "escalation_id": row.id,
                "organization_id": organization_id,
                "workflow_run_id": row.workflow_run_id,
                "note": note,
                "summary": card.get("summary"),
                "human": _person(user),
                "escalation_uuid": row.escalation_uuid,
            }
        ),
    )
    from api.utils.common import get_backend_endpoints

    backend_endpoint, _ = await get_backend_endpoints()
    caller_call_id = str(
        (getattr(run, "gathered_context", None) or {}).get("call_id") or ""
    )
    resume_url = (
        f"{backend_endpoint}/api/v1/telephony/{provider.PROVIDER_NAME}/"
        f"escalation-handback/{token}"
    )
    moved = await provider.hand_back_to_ai(
        caller_call_id=caller_call_id,
        human_call_id=row.human_call_id,
        resume_url=resume_url,
    )
    if not moved:
        await redis.delete(f"escalation:handback-lock:{row.escalation_uuid}")
        raise EscalationError(
            "The carrier did not move the caller back. They are still with "
            "the person who picked up."
        )
    await record.move(
        row,
        record.COMPLETED,
        organization_id=organization_id,
        handed_back_at=datetime.now(UTC),
        outcome_note=note or None,
        human_user_id=user_id,
        human_response=response or row.human_response or "accepted",
    )
    await sync_card(row, organization_id=organization_id)
    return await get(organization_id, escalation_uuid)


def _person(user: Any) -> str | None:
    if user is None:
        return None
    from api.services.workflow.tasks_board import person_name

    # An email address is not a name to say aloud; its first part nearly is.
    return person_name(user).split("@")[0].strip() or None


async def claim_handback(token: str) -> dict[str, Any] | None:
    """The hand-back a carrier is fetching, once. None if unknown or spent."""
    redis = await _redis()
    key = f"escalation:handback:{token}"
    raw = await redis.getdel(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


async def peek_handback(token: str) -> dict[str, Any] | None:
    redis = await _redis()
    raw = await redis.get(f"escalation:handback:{token}")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


# --- carrier-side events ---------------------------------------------------


async def on_human_answered(transfer_id: str, call_id: str | None) -> None:
    row = await db_client.get_escalation_by_transfer_unscoped(transfer_id)
    if row is not None and call_id:
        await db_client.update_escalation(
            row.id, organization_id=row.organization_id, human_call_id=call_id
        )


async def on_transfer_status(transfer_id: str, call_status: str) -> None:
    """The person's leg ended after the bridge: the escalation is complete."""
    if (call_status or "").lower() not in ("completed", "hangup"):
        return
    row = await db_client.get_escalation_by_transfer_unscoped(transfer_id)
    if row is None or row.state != record.BRIDGED:
        return
    await record.move(row, record.COMPLETED, organization_id=row.organization_id)
    await sync_card(row, organization_id=row.organization_id)


def intro_line(card: dict[str, Any], human_name: str | None = None) -> str:
    """The three-way introduction, heard by both."""
    caller = card.get("caller") or {}
    who = caller.get("name") or "a caller"
    about = card.get("intent")
    greeting = f"{human_name}, " if human_name else ""
    return (
        f"{greeting}I have {who} on the line"
        + (f" about {about}." if about else ".")
        + " I've passed on the details. I'll leave you to it."
    )


async def list_open_for_today(
    organization_id: int, *, since: datetime | None = None
) -> list[dict[str, Any]]:
    """Handovers from the last day still waiting on a person's answer."""
    since = since or datetime.now(UTC) - timedelta(days=1)
    rows = await db_client.list_escalations(
        organization_id=organization_id, since=since, limit=20
    )
    out = []
    for row in rows:
        if row.human_response is not None:
            continue
        if row.state not in record.OPEN and row.fallback is None:
            continue
        card = row.handoff_card or {}
        caller = card.get("caller") or {}
        out.append(
            {
                "id": row.id,
                "escalation_uuid": row.escalation_uuid,
                "title": f"{caller.get('name') or caller.get('number_masked') or 'A caller'} "
                f"needs a person: {card.get('reason') or row.reason_code}",
                "at": row.requested_at.isoformat() if row.requested_at else None,
                "state": row.state,
            }
        )
    return out


__all__ = [
    "EscalationError",
    "NotFound",
    "NotSupported",
    "accept",
    "claim_handback",
    "decline",
    "get",
    "hand_back",
    "intro_line",
    "list_open_for_today",
    "on_human_answered",
    "on_transfer_status",
    "peek_handback",
    "sync_card",
]
