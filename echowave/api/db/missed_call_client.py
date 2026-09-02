"""Storage for missed-call rings and what we did about each one.

Callers crossing this boundary are already normalised by
`services/compliance/dnd.normalise_number`, the same form the loop guard and
the DND list compare on. Normalising again here would put the rule in two
places, and two copies of a normalisation rule eventually disagree — at which
point a refusal cannot be matched to the caller that caused it.
"""

from typing import Optional, Sequence

from sqlalchemy import select, update

from api.db.base_client import BaseDBClient
from api.db.models import MissedCallEventModel


class MissedCallClient(BaseDBClient):
    async def record_missed_call(
        self,
        *,
        organization_id: int,
        telephony_phone_number_id: int,
        caller: str,
        provider: str | None = None,
    ) -> MissedCallEventModel:
        """Write the ring down before deciding what to do about it.

        Recorded first, outcome second, so a crash between the ring and the
        callback leaves a `pending` row rather than nothing. A row that stays
        pending is a real signal — it means the worker never picked the job up
        — and it is only visible because the row was written this early.
        """
        async with self.async_session() as session:
            event = MissedCallEventModel(
                organization_id=organization_id,
                telephony_phone_number_id=telephony_phone_number_id,
                caller=caller,
                provider=provider,
                outcome="pending",
            )
            session.add(event)
            await session.commit()
            await session.refresh(event)
            return event

    async def resolve_missed_call(
        self,
        event_id: int,
        *,
        organization_id: int,
        outcome: str,
        refusal_reason: str | None = None,
        workflow_run_id: int | None = None,
    ) -> None:
        """Close the row out.

        Scoped by organization_id as well as id. The id alone would be enough
        to find the row, and that is exactly the habit that lets one tenant's
        identifier reach another tenant's data the day this is called from
        somewhere that takes an id from a request.
        """
        async with self.async_session() as session:
            await session.execute(
                update(MissedCallEventModel)
                .where(
                    MissedCallEventModel.id == event_id,
                    MissedCallEventModel.organization_id == organization_id,
                )
                .values(
                    outcome=outcome,
                    refusal_reason=refusal_reason,
                    workflow_run_id=workflow_run_id,
                )
            )
            await session.commit()

    async def get_missed_call(
        self, event_id: int, *, organization_id: int
    ) -> Optional[MissedCallEventModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(MissedCallEventModel).where(
                    MissedCallEventModel.id == event_id,
                    MissedCallEventModel.organization_id == organization_id,
                )
            )
            return result.scalars().first()

    async def list_missed_calls(
        self, organization_id: int, *, limit: int = 50, offset: int = 0
    ) -> Sequence[MissedCallEventModel]:
        """Newest first — the operator's question is "who rang just now"."""
        async with self.async_session() as session:
            result = await session.execute(
                select(MissedCallEventModel)
                .where(MissedCallEventModel.organization_id == organization_id)
                .order_by(MissedCallEventModel.received_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return result.scalars().all()
