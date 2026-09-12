"""Reads and writes for standing instructions -- the Desks' clock.

Three queries and two stamps. Kept small on purpose: the firing *decision*
lives in ``services/workflow/routines.py`` as pure functions over a moment, so
nothing here decides whether a routine should run. This layer only fetches the
candidates and records what the tick concluded.
"""

from datetime import UTC, datetime
from typing import Optional, Sequence

from sqlalchemy import select

from api.db.base_client import BaseDBClient
from api.db.models import AgentRoutineModel


class RoutineClient(BaseDBClient):
    async def armed_routines(self) -> Sequence[AgentRoutineModel]:
        """Every routine switched on, across all tenants.

        Cross-tenant by design and called only by the minute tick, which is
        the one caller in the product that legitimately has no organisation --
        it is the clock, not a user. Every other read here takes an
        ``organization_id``, and the tick carries each routine's own tenant
        forward into the run it starts.

        Untested routines are filtered out in the runtime rather than here, so
        one that was somehow armed without a test run is *recorded as skipped*
        instead of vanishing from the tick with nothing said.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentRoutineModel)
                .where(AgentRoutineModel.is_active.is_(True))
                .order_by(AgentRoutineModel.id)
            )
            return result.scalars().all()

    async def routines_for_workflow(
        self, workflow_id: int, *, organization_id: int
    ) -> Sequence[AgentRoutineModel]:
        """This bot's routines, for its ribbon. Org-scoped."""
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentRoutineModel)
                .where(
                    AgentRoutineModel.workflow_id == workflow_id,
                    AgentRoutineModel.organization_id == organization_id,
                )
                .order_by(AgentRoutineModel.id)
            )
            return result.scalars().all()

    async def get_routine(
        self, routine_id: int, *, organization_id: int
    ) -> Optional[AgentRoutineModel]:
        """One routine, or None if it is not this organisation's.

        The ``organization_id`` is not optional and is not a filter applied
        afterwards: a routine id from a request body proves the row exists, not
        that the caller may touch it.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentRoutineModel).where(
                    AgentRoutineModel.id == routine_id,
                    AgentRoutineModel.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    async def mark_routine_fired(self, routine_id: int, *, slot: datetime) -> None:
        """Stamp the slot that fired, and clear any skip.

        The *slot*, not ``now()``. That is what makes a minute tick safe: the
        runtime compares this against the slot it is considering, so a tick
        that runs twice in one minute, or a worker returning inside the
        catch-up window, cannot send the same report twice. Stamping the
        moment instead would let a run one second late count as a different
        slot.
        """
        async with self.async_session() as session:
            routine = await session.get(AgentRoutineModel, routine_id)
            if routine is None:
                return
            routine.last_fired_at = slot
            routine.last_skipped_reason = None
            routine.last_skipped_at = None
            await session.commit()

    async def mark_routine_skipped(self, routine_id: int, *, reason: str) -> None:
        """Record why the last tick declined.

        Stored on the routine as well as written to the timeline, so its own
        screen can answer "why didn't it run" without a query across the event
        log -- and so the answer survives however long the timeline is kept.
        """
        async with self.async_session() as session:
            routine = await session.get(AgentRoutineModel, routine_id)
            if routine is None:
                return
            routine.last_skipped_reason = reason[:32]
            routine.last_skipped_at = datetime.now(UTC)
            await session.commit()

    async def mark_routine_tested(
        self, routine_id: int, *, organization_id: int
    ) -> bool:
        """Record a test run, which is what lets the routine be armed.

        Org-scoped, because this is reachable from a request and it is the
        gate on arming: letting another tenant stamp it would let them arm
        somebody else's Desk.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentRoutineModel).where(
                    AgentRoutineModel.id == routine_id,
                    AgentRoutineModel.organization_id == organization_id,
                )
            )
            routine = result.scalar_one_or_none()
            if routine is None:
                return False
            routine.tested_at = datetime.now(UTC)
            await session.commit()
            return True
