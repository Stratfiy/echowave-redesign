"""Storage for escalations (services/escalation).

Every read and write names the organization. The two exceptions are the
lookups a carrier callback needs (``by transfer id``), which return the row
for the caller to check against the run it belongs to -- a callback carries
no signed-in person to scope by.

The state machine's rules live in ``services/escalation/record.py``; this
module only offers the compare-and-swap primitives they are built from, so a
second worker, a retried webhook or a double click cannot move a row twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from api.db.base_client import BaseDBClient
from api.db.escalation_models import CallEscalationOutcomeModel, EscalationModel


def _now() -> datetime:
    return datetime.now(UTC)


class EscalationClient(BaseDBClient):
    async def open_escalation(
        self,
        *,
        organization_id: int,
        idempotency_key: str,
        reason_code: str,
        reason_detail: str | None = None,
        trigger: str = "auto",
        workflow_id: int | None = None,
        workflow_run_id: int | None = None,
    ) -> tuple[EscalationModel, bool]:
        """The row for this key, and whether this call wrote it.

        ``INSERT ... ON CONFLICT DO NOTHING`` on the unique key, then a read:
        two workers that see the same trigger at the same moment both come
        back with one row, and only one of them is told it is new.
        """
        now = _now()
        async with self.async_session() as session:
            inserted = await session.scalar(
                insert(EscalationModel)
                .values(
                    escalation_uuid=str(uuid.uuid4()),
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    workflow_run_id=workflow_run_id,
                    idempotency_key=idempotency_key,
                    state="requested",
                    reason_code=reason_code,
                    reason_detail=(reason_detail or None) and str(reason_detail)[:200],
                    trigger=trigger,
                    attempt_count=0,
                    attempts=[],
                    requested_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(EscalationModel.id)
            )
            await session.commit()
            row = await session.scalar(
                select(EscalationModel).where(
                    EscalationModel.idempotency_key == idempotency_key,
                    EscalationModel.organization_id == organization_id,
                )
            )
        if row is None:
            # The key exists under another organization. Keys carry the run
            # id, which is global, so this is a bug rather than a race -- and
            # it must not hand one tenant another's row.
            raise LookupError("An escalation with that key belongs elsewhere.")
        return row, inserted is not None

    async def get_escalation(
        self, escalation_uuid: str, *, organization_id: int
    ) -> Optional[EscalationModel]:
        async with self.async_session() as session:
            return await session.scalar(
                select(EscalationModel).where(
                    EscalationModel.escalation_uuid == escalation_uuid,
                    EscalationModel.organization_id == organization_id,
                )
            )

    async def get_escalation_by_id(
        self, escalation_id: int, *, organization_id: int
    ) -> Optional[EscalationModel]:
        async with self.async_session() as session:
            return await session.scalar(
                select(EscalationModel).where(
                    EscalationModel.id == escalation_id,
                    EscalationModel.organization_id == organization_id,
                )
            )

    async def get_escalation_by_uuid_unscoped(
        self, escalation_uuid: str
    ) -> Optional[EscalationModel]:
        """For a carrier callback, which has no person to scope by. The caller
        checks the row against the run the callback was signed for."""
        async with self.async_session() as session:
            return await session.scalar(
                select(EscalationModel).where(
                    EscalationModel.escalation_uuid == escalation_uuid
                )
            )

    async def get_escalation_by_transfer_unscoped(
        self, transfer_id: str
    ) -> Optional[EscalationModel]:
        async with self.async_session() as session:
            return await session.scalar(
                select(EscalationModel).where(
                    EscalationModel.current_transfer_id == transfer_id
                )
            )

    async def transition_escalation(
        self,
        escalation_id: int,
        *,
        organization_id: int,
        to_state: str,
        from_states: Iterable[str],
        **fields: Any,
    ) -> bool:
        """Move the row to ``to_state`` if it is in one of ``from_states``.

        Returns whether this call moved it. A row already past the state
        (a retried callback, a second worker) is left alone and reported as
        not moved, which is how the caller knows not to act twice.
        """
        values = {"state": to_state, "updated_at": _now(), **fields}
        async with self.async_session() as session:
            result = await session.execute(
                update(EscalationModel)
                .where(
                    EscalationModel.id == escalation_id,
                    EscalationModel.organization_id == organization_id,
                    EscalationModel.state.in_(list(from_states)),
                )
                .values(**values)
            )
            await session.commit()
            return bool(result.rowcount)

    async def claim_escalation_attempt(
        self,
        escalation_id: int,
        *,
        organization_id: int,
        expected_count: int,
        transfer_id: str,
        attempt: dict[str, Any],
    ) -> bool:
        """Claim the right to dial attempt ``expected_count + 1``.

        One conditional UPDATE: the counter moves from ``expected_count`` to
        the next number only once, so whichever worker loses the race (or a
        retry that already dialled) gets False and must not ring anybody.
        """
        async with self.async_session() as session:
            row = await session.scalar(
                select(EscalationModel)
                .where(
                    EscalationModel.id == escalation_id,
                    EscalationModel.organization_id == organization_id,
                )
                .with_for_update()
            )
            if (
                row is None
                or row.attempt_count != expected_count
                or row.state not in ("requested", "dialling")
            ):
                await session.rollback()
                return False
            row.attempt_count = expected_count + 1
            row.state = "dialling"
            row.current_transfer_id = transfer_id
            row.attempts = [*(row.attempts or []), attempt]
            row.updated_at = _now()
            await session.commit()
            return True

    async def update_escalation(
        self, escalation_id: int, *, organization_id: int, **fields: Any
    ) -> bool:
        if not fields:
            return False
        async with self.async_session() as session:
            result = await session.execute(
                update(EscalationModel)
                .where(
                    EscalationModel.id == escalation_id,
                    EscalationModel.organization_id == organization_id,
                )
                .values(updated_at=_now(), **fields)
            )
            await session.commit()
            return bool(result.rowcount)

    async def set_escalation_attempt_outcome(
        self,
        escalation_id: int,
        *,
        organization_id: int,
        transfer_id: str,
        outcome: str,
    ) -> None:
        async with self.async_session() as session:
            row = await session.scalar(
                select(EscalationModel)
                .where(
                    EscalationModel.id == escalation_id,
                    EscalationModel.organization_id == organization_id,
                )
                .with_for_update()
            )
            if row is None:
                await session.rollback()
                return
            attempts = []
            for entry in row.attempts or []:
                entry = dict(entry)
                if entry.get("transfer_id") == transfer_id:
                    entry["outcome"] = outcome
                    entry["ended_at"] = _now().isoformat()
                attempts.append(entry)
            row.attempts = attempts
            row.updated_at = _now()
            await session.commit()

    async def list_escalations(
        self,
        *,
        organization_id: int,
        since: datetime | None = None,
        states: Iterable[str] | None = None,
        workflow_id: int | None = None,
        limit: int = 50,
    ) -> list[EscalationModel]:
        query = select(EscalationModel).where(
            EscalationModel.organization_id == organization_id
        )
        if since is not None:
            query = query.where(EscalationModel.requested_at >= since)
        if states is not None:
            query = query.where(EscalationModel.state.in_(list(states)))
        if workflow_id is not None:
            query = query.where(EscalationModel.workflow_id == workflow_id)
        query = query.order_by(EscalationModel.requested_at.desc()).limit(limit)
        async with self.async_session() as session:
            return list((await session.execute(query)).scalars())

    async def record_call_escalation_outcome(
        self,
        *,
        organization_id: int,
        workflow_run_id: int,
        outcome: str,
        workflow_id: int | None = None,
        reason_code: str | None = None,
        transfer_result: str | None = None,
        failure_reason: str | None = None,
        fallback: str | None = None,
        time_to_human_ms: int | None = None,
        escalation_id: int | None = None,
    ) -> None:
        """One row per call; a second write for the same call replaces it,
        so the last word on the call is the one kept."""
        values = {
            "organization_id": organization_id,
            "workflow_id": workflow_id,
            "workflow_run_id": workflow_run_id,
            "outcome": outcome,
            "reason_code": reason_code,
            "transfer_result": transfer_result,
            "failure_reason": failure_reason,
            "fallback": fallback,
            "time_to_human_ms": time_to_human_ms,
            "escalation_id": escalation_id,
            "recorded_at": _now(),
        }
        async with self.async_session() as session:
            statement = insert(CallEscalationOutcomeModel).values(**values)
            statement = statement.on_conflict_do_update(
                index_elements=["workflow_run_id"],
                set_={
                    k: statement.excluded[k]
                    for k in values
                    if k not in ("workflow_run_id", "organization_id")
                },
                where=CallEscalationOutcomeModel.organization_id == organization_id,
            )
            await session.execute(statement)
            await session.commit()

    async def get_call_escalation_outcome(
        self, workflow_run_id: int, *, organization_id: int
    ) -> Optional[CallEscalationOutcomeModel]:
        async with self.async_session() as session:
            return await session.scalar(
                select(CallEscalationOutcomeModel).where(
                    CallEscalationOutcomeModel.workflow_run_id == workflow_run_id,
                    CallEscalationOutcomeModel.organization_id == organization_id,
                )
            )
