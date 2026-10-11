"""One escalation, as a row that moves through its states exactly once.

    requested -> dialling -> briefing -> bridged -> completed
          \\          \\           \\          \\
           +----------+-----------+----------+--> failed(reason)

``dialling -> dialling`` is the next person on the ladder. Every move is a
compare-and-swap on the current state (``db_client.transition_escalation``),
so a callback that arrives twice, two workers, or a person double-clicking
Hand back each move the row once and are told when they did not.

"Retries never dial twice": dialling goes through ``claim_attempt``, which
advances ``attempt_count`` from the number the caller read. A retry that read
the same number as a dial that already happened is refused, and rings nobody.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.escalation import ReasonCode
from api.services.training_loop import hooks as training_hooks

REQUESTED = "requested"
DIALLING = "dialling"
BRIEFING = "briefing"
BRIDGED = "bridged"
COMPLETED = "completed"
FAILED = "failed"

STATES = (REQUESTED, DIALLING, BRIEFING, BRIDGED, COMPLETED, FAILED)
TERMINAL = (COMPLETED, FAILED)
OPEN = (REQUESTED, DIALLING, BRIEFING, BRIDGED)

#: state -> the states it may be entered from.
ALLOWED_FROM: dict[str, tuple[str, ...]] = {
    DIALLING: (REQUESTED, DIALLING),
    BRIEFING: (DIALLING,),
    BRIDGED: (BRIEFING,),
    COMPLETED: (BRIDGED, BRIEFING),
    FAILED: (REQUESTED, DIALLING, BRIEFING, BRIDGED),
}


class TransitionRefused(Exception):
    pass


def idempotency_key(workflow_run_id: int | None, sequence: int) -> str:
    """The n-th escalation on a call. Duplicates of one trigger share it."""
    return f"run:{workflow_run_id or 0}:{sequence}"


def _now() -> datetime:
    return datetime.now(UTC)


async def open_escalation(
    *,
    organization_id: int,
    workflow_id: int | None,
    workflow_run_id: int | None,
    sequence: int,
    reason: ReasonCode | str,
    detail: str = "",
    trigger: str = "auto",
):
    """The row for this escalation, and whether this caller created it."""
    code = reason.value if isinstance(reason, ReasonCode) else str(reason)
    row, created = await db_client.open_escalation(
        organization_id=organization_id,
        idempotency_key=idempotency_key(workflow_run_id, sequence),
        reason_code=code,
        reason_detail=detail,
        trigger=trigger,
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
    )
    if created:
        await training_hooks.escalation_opened(
            organization_id=organization_id,
            escalation_id=row.id,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            reason=code,
            detail=detail,
        )
    return row, created


async def move(
    row_or_id: Any,
    to_state: str,
    *,
    organization_id: int,
    **fields: Any,
) -> bool:
    """Move the row if the state machine allows it from where it is now."""
    if to_state not in ALLOWED_FROM:
        raise TransitionRefused(f"Nothing moves to {to_state!r}.")
    escalation_id = getattr(row_or_id, "id", row_or_id)
    if to_state == BRIDGED:
        fields.setdefault("bridged_at", _now())
    if to_state in TERMINAL:
        fields.setdefault("completed_at", _now())
    moved = await db_client.transition_escalation(
        escalation_id,
        organization_id=organization_id,
        to_state=to_state,
        from_states=ALLOWED_FROM[to_state],
        **fields,
    )
    if not moved:
        logger.info(
            "Escalation {} not moved to {}: already past it", escalation_id, to_state
        )
    return moved


async def fail(row_or_id: Any, reason: str, *, organization_id: int, **fields) -> bool:
    return await move(
        row_or_id,
        FAILED,
        organization_id=organization_id,
        failure_reason=(reason or "unknown")[:40],
        **fields,
    )


async def claim_attempt(
    row_or_id: Any,
    *,
    organization_id: int,
    expected_count: int,
    transfer_id: str,
    target: str,
) -> bool:
    """May attempt ``expected_count + 1`` be dialled? Once only."""
    escalation_id = getattr(row_or_id, "id", row_or_id)
    return await db_client.claim_escalation_attempt(
        escalation_id,
        organization_id=organization_id,
        expected_count=expected_count,
        transfer_id=transfer_id,
        attempt={
            "n": expected_count + 1,
            "target": target,
            "transfer_id": transfer_id,
            "outcome": None,
            "started_at": _now().isoformat(),
        },
    )


def as_dict(row: Any) -> dict[str, Any]:
    """What the app is shown. Numbers in ``attempts`` are already masked."""
    return {
        "escalation_uuid": row.escalation_uuid,
        "workflow_id": row.workflow_id,
        "workflow_run_id": row.workflow_run_id,
        "state": row.state,
        "failure_reason": row.failure_reason,
        "reason_code": row.reason_code,
        "reason_detail": row.reason_detail,
        "trigger": row.trigger,
        "attempts": list(row.attempts or []),
        "fallback": row.fallback,
        "handoff_card": row.handoff_card or {},
        "human_response": row.human_response,
        "outcome_note": row.outcome_note,
        "time_to_human_ms": row.time_to_human_ms,
        "requested_at": row.requested_at.isoformat() if row.requested_at else None,
        "bridged_at": row.bridged_at.isoformat() if row.bridged_at else None,
        "handed_back_at": row.handed_back_at.isoformat()
        if row.handed_back_at
        else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


__all__ = [
    "ALLOWED_FROM",
    "BRIDGED",
    "BRIEFING",
    "COMPLETED",
    "DIALLING",
    "FAILED",
    "OPEN",
    "REQUESTED",
    "STATES",
    "TERMINAL",
    "TransitionRefused",
    "as_dict",
    "claim_attempt",
    "fail",
    "idempotency_key",
    "move",
    "open_escalation",
]
