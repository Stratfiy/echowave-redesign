"""The task ledger: one state set, versioned moves, idempotent creation.

Handoff 5 ("Consistent task state"), 10 ("Execution contract") and the
design's state contracts ("Task state", "Approval state", "Event delivery").
It extends the board's ``agent_tasks`` rather than adding a second task
table: the board keeps its columns (``status``), and the ledger adds the
vocabulary every channel shows -- queued, running, needs input, awaiting
approval, scheduled, completed, failed, cancelled, outcome unknown -- with
three guarantees the board alone did not give:

* **Stale writes lose.** Every move names the ``state_version`` it read.
  The update is ``WHERE state_version = expected``; a writer holding an
  older version (a late worker, a second tab, a replayed webhook) changes
  nothing and is told so. Each move is also a row in
  ``agent_task_transitions`` numbered by that version, so the history has
  no gaps and two writers cannot both record step 4.
* **One request, one task.** ``create`` takes an idempotency key; a retry
  with the same key returns the task the first attempt made.
* **Completion has evidence.** A task cannot be marked completed without
  ``outcome_evidence`` (a message id, a card, a person's say-so), and a
  timeout or lost worker is ``outcome_unknown`` -- never a success, and
  never retried blind: only a reconciliation moves it on.

Approval binding lives on the card (``actions.py``): the card's payload has
a version hash, Confirm must name it, and editing the payload makes a new
version that needs a new Confirm. A ledger task waiting on a card records
the card's id and the version it was for.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from api.db import db_client
from api.db.controls_models import AgentTaskTransitionModel
from api.db.models import AgentTaskModel
from api.services import events, features

FLAG = "task_ledger"

QUEUED = "queued"
RUNNING = "running"
NEEDS_INPUT = "needs_input"
AWAITING_APPROVAL = "awaiting_approval"
SCHEDULED = "scheduled"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
OUTCOME_UNKNOWN = "outcome_unknown"

STATES = (
    QUEUED,
    RUNNING,
    NEEDS_INPUT,
    AWAITING_APPROVAL,
    SCHEDULED,
    COMPLETED,
    FAILED,
    CANCELLED,
    OUTCOME_UNKNOWN,
)
TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})

#: Where each state may go. Terminal states go nowhere; outcome unknown only
#: to what reconciliation finds.
ALLOWED: dict[str, frozenset[str]] = {
    QUEUED: frozenset(
        {RUNNING, SCHEDULED, AWAITING_APPROVAL, NEEDS_INPUT, CANCELLED, FAILED}
    ),
    SCHEDULED: frozenset({QUEUED, RUNNING, CANCELLED, FAILED}),
    RUNNING: frozenset(
        {COMPLETED, FAILED, NEEDS_INPUT, AWAITING_APPROVAL, OUTCOME_UNKNOWN, CANCELLED}
    ),
    NEEDS_INPUT: frozenset({QUEUED, RUNNING, CANCELLED, FAILED}),
    AWAITING_APPROVAL: frozenset({QUEUED, SCHEDULED, RUNNING, CANCELLED, FAILED}),
    OUTCOME_UNKNOWN: frozenset({COMPLETED, FAILED}),
    COMPLETED: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}

#: The board column each ledger state shows in.
BOARD_STATUS = {
    QUEUED: "todo",
    SCHEDULED: "todo",
    RUNNING: "in_progress",
    NEEDS_INPUT: "blocked",
    AWAITING_APPROVAL: "in_review",
    COMPLETED: "done",
    FAILED: "blocked",
    CANCELLED: "cancelled",
    OUTCOME_UNKNOWN: "blocked",
}

#: The ledger state of a row the ledger never touched, read from its board
#: column -- including the names the board used before TB-1.
FROM_BOARD = {
    "backlog": QUEUED,
    "todo": QUEUED,
    "in_progress": RUNNING,
    "in_review": NEEDS_INPUT,
    "blocked": NEEDS_INPUT,
    "done": COMPLETED,
    "cancelled": CANCELLED,
    "doing": RUNNING,
    "waiting": NEEDS_INPUT,
    "could_not": FAILED,
}

#: The analytics event a move into each state emits, where there is one.
_EVENT = {
    RUNNING: "task_started",
    COMPLETED: "task_completed",
    FAILED: "task_failed",
    CANCELLED: "task_cancelled",
}

#: Copy a person reads for the states that need it (design, "Reusable error
#: copy").
UNKNOWN_COPY = (
    "We are checking whether this was delivered. Please do not send it again."
)


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


class LedgerError(ValueError):
    pass


class StaleState(LedgerError):
    """The move named a version older than the stored one."""

    def __init__(self, current: int, current_state: str):
        self.current = current
        self.current_state = current_state
        super().__init__(
            "This task changed since you read it. Reload it and try again."
        )


class NotAllowed(LedgerError):
    pass


def state_of(task: Any) -> str:
    """The ledger state of a task row, whether or not the ledger wrote it.
    An unknown board status is ``needs_input`` -- seen, not hidden."""
    stored = getattr(task, "ledger_state", None)
    if stored in STATES:
        return stored
    return FROM_BOARD.get(getattr(task, "status", None) or "", NEEDS_INPUT)


def as_dict(task: Any) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "state": state_of(task),
        "version": int(getattr(task, "state_version", 0) or 0),
        "board_status": task.status,
        "idempotency_key": getattr(task, "idempotency_key", None),
        "outcome_evidence": getattr(task, "outcome_evidence", None),
        "approval_event_id": getattr(task, "approval_event_id", None),
        "payload_version": getattr(task, "payload_version", None),
        "notice": UNKNOWN_COPY if state_of(task) == OUTCOME_UNKNOWN else None,
    }


async def create(
    *,
    organization_id: int,
    title: str,
    brief: str = "",
    created_by: int | None,
    idempotency_key: str | None = None,
    state: str = QUEUED,
    **fields: Any,
) -> tuple[AgentTaskModel, bool]:
    """A ledger task. Returns (task, created). With a key already used in
    this workspace, the existing task and False -- never a second task."""
    if state not in (QUEUED, SCHEDULED, AWAITING_APPROVAL, NEEDS_INPUT):
        raise NotAllowed(f"A task cannot start {state}.")
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()[:128] or None
    if idempotency_key:
        existing = await _by_key(organization_id, idempotency_key)
        if existing is not None:
            return existing, False
    try:
        task = await db_client.create_task(
            organization_id=organization_id,
            title=title[:200],
            brief=brief,
            created_by=created_by,
            status=BOARD_STATUS[state],
            ledger_state=state,
            state_version=1,
            idempotency_key=idempotency_key,
            **fields,
        )
    except IntegrityError:
        # Two first attempts raced; the key's unique index let one in.
        if idempotency_key:
            existing = await _by_key(organization_id, idempotency_key)
            if existing is not None:
                return existing, False
        raise
    await _record(organization_id, task.id, 1, None, state, created_by, "created")
    return task, True


async def _by_key(organization_id: int, key: str) -> AgentTaskModel | None:
    async with db_client.async_session() as session:
        return await session.scalar(
            select(AgentTaskModel).where(
                AgentTaskModel.organization_id == organization_id,
                AgentTaskModel.idempotency_key == key,
            )
        )


async def _record(
    organization_id: int,
    task_id: int,
    sequence: int,
    from_state: str | None,
    to_state: str,
    actor_user_id: int | None,
    reason_code: str | None,
) -> None:
    async with db_client.async_session() as session:
        await session.execute(
            insert(AgentTaskTransitionModel).values(
                organization_id=organization_id,
                task_id=task_id,
                sequence=sequence,
                from_state=from_state,
                to_state=to_state,
                actor_user_id=actor_user_id,
                reason_code=(reason_code or None) and reason_code[:64],
                occurred_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def transition(
    *,
    organization_id: int,
    task_id: int,
    to_state: str,
    expected_version: int,
    actor_user_id: int | None = None,
    reason_code: str | None = None,
    evidence: dict[str, Any] | None = None,
    approval_event_id: int | None = None,
    payload_version: str | None = None,
) -> AgentTaskModel:
    """Move a task, if it is still at ``expected_version``.

    Raises StaleState when it is not (nothing changes), NotAllowed for a
    move the state set forbids, and LedgerError for a completion without
    evidence or a task that is not in this workspace.
    """
    if to_state not in STATES:
        raise NotAllowed(f"{to_state} is not a task state.")
    task = await db_client.get_task(task_id, organization_id=organization_id)
    if task is None:
        raise LedgerError("That task is not here.")
    current = state_of(task)
    version = int(task.state_version or 0)
    if version != expected_version:
        raise StaleState(version, current)
    if to_state not in ALLOWED[current]:
        raise NotAllowed(
            f"A {current.replace('_', ' ')} task cannot become {to_state.replace('_', ' ')}."
        )
    if to_state == COMPLETED and not (evidence or task.outcome_evidence):
        raise LedgerError("A task is completed only with evidence of the outcome.")

    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "ledger_state": to_state,
        "state_version": version + 1,
        "status": BOARD_STATUS[to_state],
    }
    if evidence is not None:
        values["outcome_evidence"] = evidence
    if approval_event_id is not None:
        values["approval_event_id"] = approval_event_id
    if payload_version is not None:
        values["payload_version"] = payload_version
    if to_state == RUNNING and task.started_at is None:
        values["started_at"] = now
    if to_state in TERMINAL:
        values["finished_at"] = now
    async with db_client.async_session() as session:
        result = await session.execute(
            update(AgentTaskModel)
            .where(
                AgentTaskModel.id == task_id,
                AgentTaskModel.organization_id == organization_id,
                AgentTaskModel.state_version == expected_version,
            )
            .values(**values)
            .returning(AgentTaskModel.id)
        )
        if result.first() is None:
            await session.rollback()
            fresh = await db_client.get_task(task_id, organization_id=organization_id)
            raise StaleState(
                int(getattr(fresh, "state_version", 0) or 0), state_of(fresh)
            )
        session.add(
            AgentTaskTransitionModel(
                organization_id=organization_id,
                task_id=task_id,
                sequence=version + 1,
                from_state=current,
                to_state=to_state,
                actor_user_id=actor_user_id,
                reason_code=(reason_code or None) and reason_code[:64],
                occurred_at=now,
            )
        )
        name = _EVENT.get(to_state)
        if name:
            # In the same transaction: the event exists exactly when the move
            # does (handoff 35, transactional outbox).
            await events.emit(
                name,
                session=session,
                user_id=actor_user_id,
                organization_id=organization_id,
                task_id=task_id,
                properties={
                    "status": to_state,
                    "reason_code": reason_code,
                    **(
                        {"has_evidence": bool(evidence or task.outcome_evidence)}
                        if to_state == COMPLETED
                        else {}
                    ),
                },
            )
        await session.commit()
    fresh = await db_client.get_task(task_id, organization_id=organization_id)
    assert fresh is not None
    return fresh


async def history(organization_id: int, task_id: int) -> list[dict[str, Any]]:
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    select(AgentTaskTransitionModel)
                    .where(
                        AgentTaskTransitionModel.organization_id == organization_id,
                        AgentTaskTransitionModel.task_id == task_id,
                    )
                    .order_by(AgentTaskTransitionModel.sequence)
                )
            )
            .scalars()
            .all()
        )
    return [
        {
            "sequence": r.sequence,
            "from": r.from_state,
            "to": r.to_state,
            "actor_user_id": r.actor_user_id,
            "reason_code": r.reason_code,
            "at": r.occurred_at.isoformat(),
        }
        for r in rows
    ]


async def follow_board(
    *, organization_id: int, task_id: int, status: str, user_id: int | None
) -> None:
    """Keep the ledger in step when a person moves a card on the board.

    The board's move has already happened; this records it in the ledger's
    vocabulary with a new version, so a client holding the old version is
    told its view is stale. A person marking a card done is the evidence.
    Never raises: the board's own write is what the person asked for.
    """
    if not enabled(organization_id):
        return
    target = FROM_BOARD.get(status)
    if target is None:
        return
    try:
        task = await db_client.get_task(task_id, organization_id=organization_id)
        if task is None or state_of(task) == target:
            return
        values: dict[str, Any] = {
            "ledger_state": target,
            "state_version": int(task.state_version or 0) + 1,
        }
        if target == COMPLETED and not task.outcome_evidence:
            values["outcome_evidence"] = {"marked_done_by": user_id}
        async with db_client.async_session() as session:
            result = await session.execute(
                update(AgentTaskModel)
                .where(
                    AgentTaskModel.id == task_id,
                    AgentTaskModel.organization_id == organization_id,
                    AgentTaskModel.state_version == task.state_version,
                )
                .values(**values)
                .returning(AgentTaskModel.id)
            )
            if result.first() is None:
                await session.rollback()
                return
            session.add(
                AgentTaskTransitionModel(
                    organization_id=organization_id,
                    task_id=task_id,
                    sequence=values["state_version"],
                    from_state=state_of(task),
                    to_state=target,
                    actor_user_id=user_id,
                    reason_code="board_move",
                    occurred_at=datetime.now(UTC),
                )
            )
            name = _EVENT.get(target)
            if name:
                await events.emit(
                    name,
                    session=session,
                    user_id=user_id,
                    organization_id=organization_id,
                    task_id=task_id,
                    properties={"status": target, "reason_code": "board_move"},
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        from loguru import logger

        logger.warning("Ledger could not follow board move on {}: {}", task_id, exc)


def card_state(payload: dict[str, Any] | None) -> str:
    """The ledger state of an approval card (actions.py), so a card and a
    task read the same on every channel."""
    from api.services.workflow import actions

    state = (payload or {}).get("state") or actions.PROPOSED
    return {
        actions.PROPOSED: AWAITING_APPROVAL,
        actions.ARMED: SCHEDULED,
        actions.RUNNING: RUNNING,
        actions.DONE: COMPLETED,
        actions.FAILED: FAILED,
        actions.UNDONE: CANCELLED,
        actions.CANCELLED: CANCELLED,
        actions.DECLINED: CANCELLED,
        actions.OUTCOME_UNKNOWN: OUTCOME_UNKNOWN,
    }.get(state, NEEDS_INPUT)
