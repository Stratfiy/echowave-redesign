"""Storage for the training loop (services/training_loop).

Every method names the organization and filters by it in the query. There is
deliberately no method that reads events across organizations: the training
data is each workspace's own, and a query that could span two would be the
first step to a model that learned from both.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from api.db.base_client import BaseDBClient
from api.db.models import (
    CallCostItemModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.db.training_loop_models import LearningEventModel
from api.enums import CostComponent

#: Text columns: what consent covers, and what withdrawing it clears.
TEXT_COLUMNS = ("input_text", "model_output", "owner_final")


class TrainingLoopClient(BaseDBClient):
    async def insert_learning_event(self, values: dict[str, Any]) -> bool:
        """Write one event. False when the same event was already there."""
        async with self.async_session() as session:
            row_id = await session.scalar(
                insert(LearningEventModel)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_learning_events_once")
                .returning(LearningEventModel.id)
            )
            await session.commit()
        return row_id is not None

    async def get_learning_event(
        self, *, organization_id: int, event_type: str, subject_key: str
    ) -> LearningEventModel | None:
        async with self.async_session() as session:
            return await session.scalar(
                select(LearningEventModel).where(
                    LearningEventModel.organization_id == organization_id,
                    LearningEventModel.event_type == event_type,
                    LearningEventModel.subject_key == subject_key,
                )
            )

    async def clear_learning_event_text(self, organization_id: int) -> int:
        """Take the words out of every event this workspace has, keeping what
        happened. Returns how many rows held any."""
        async with self.async_session() as session:
            result = await session.execute(
                update(LearningEventModel)
                .where(
                    LearningEventModel.organization_id == organization_id,
                    LearningEventModel.consent_state == "granted",
                )
                .values(
                    input_text=None,
                    model_output=None,
                    owner_final=None,
                    consent_state="declined",
                )
            )
            await session.commit()
        return int(result.rowcount or 0)

    async def count_learning_events(
        self,
        *,
        organization_id: int,
        workflow_id: int | None = None,
        event_types: Iterable[str],
        since: datetime | None = None,
    ) -> dict[str, int]:
        query = select(
            LearningEventModel.event_type, func.count(LearningEventModel.id)
        ).where(
            LearningEventModel.organization_id == organization_id,
            LearningEventModel.event_type.in_(list(event_types)),
        )
        if workflow_id is not None:
            query = query.where(LearningEventModel.workflow_id == workflow_id)
        if since is not None:
            query = query.where(LearningEventModel.created_at >= since)
        async with self.async_session() as session:
            rows = (
                await session.execute(query.group_by(LearningEventModel.event_type))
            ).all()
        return {event_type: int(n) for event_type, n in rows}

    async def list_learning_events_for_export(
        self,
        *,
        organization_id: int,
        workflow_id: int | None = None,
        limit: int,
    ) -> list[LearningEventModel]:
        """This workspace's events that still carry their words, oldest first.

        Only ``granted`` rows: a ``declined`` row has nothing to export and
        must never be mistaken for training data.
        """
        query = (
            select(LearningEventModel)
            .where(
                LearningEventModel.organization_id == organization_id,
                LearningEventModel.consent_state == "granted",
            )
            .order_by(LearningEventModel.id)
            .limit(limit)
        )
        if workflow_id is not None:
            query = query.where(LearningEventModel.workflow_id == workflow_id)
        async with self.async_session() as session:
            return list((await session.execute(query)).scalars().all())

    async def agent_model_spend_paise(
        self, *, organization_id: int, workflow_id: int, since: datetime
    ) -> int:
        """What the language models behind this agent cost us since ``since``,
        in paise: the vendor's price (``provider_cost_paise``), not what the
        workspace was charged, summed over the receipt lines for the model.
        """
        components = [CostComponent.LLM.value] + [
            c.value for c in CostComponent.llm_split_components()
        ]
        async with self.async_session() as session:
            total = await session.scalar(
                select(
                    func.coalesce(func.sum(CallCostItemModel.provider_cost_paise), 0)
                )
                .join(
                    WorkflowRunModel,
                    WorkflowRunModel.id == CallCostItemModel.workflow_run_id,
                )
                .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
                .where(
                    WorkflowModel.organization_id == organization_id,
                    WorkflowRunModel.workflow_id == workflow_id,
                    WorkflowRunModel.created_at >= since,
                    CallCostItemModel.component.in_(components),
                )
            )
        return int(total or 0)
