"""Storage for the training loop (services/training_loop).

Every method names the organization and filters by it in the query. There is
deliberately no method that reads events across organizations: the training
data is each workspace's own, and a query that could span two would be the
first step to a model that learned from both.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, cast, func, select, update
from sqlalchemy.dialects.postgresql import insert

from api.db.base_client import BaseDBClient
from api.db.models import (
    AgentEventModel,
    CallCostItemModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.db.training_loop_models import LearningEventModel
from api.enums import CostComponent


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
                    # An archived row is the workspace's "delete": its words
                    # are not to be read back into anything new.
                    LearningEventModel.archived_at.is_(None),
                )
            )

    async def archive_learning_events(
        self, organization_id: int, now: datetime | None = None
    ) -> int:
        """Archive every event this workspace has not archived yet: they stay
        in the table, out of every export, count and training read. Words are
        kept as they are (they are kept only as the law requires); nothing is
        deleted. Returns how many rows were archived."""
        async with self.async_session() as session:
            result = await session.execute(
                update(LearningEventModel)
                .where(
                    LearningEventModel.organization_id == organization_id,
                    LearningEventModel.archived_at.is_(None),
                )
                .values(archived_at=now or datetime.now(UTC))
            )
            await session.commit()
        return int(result.rowcount or 0)

    async def count_archived_learning_events(self, organization_id: int) -> int:
        async with self.async_session() as session:
            return int(
                await session.scalar(
                    select(func.count(LearningEventModel.id)).where(
                        LearningEventModel.organization_id == organization_id,
                        LearningEventModel.archived_at.is_not(None),
                    )
                )
                or 0
            )

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
            LearningEventModel.archived_at.is_(None),
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

        Only ``granted`` rows that are not archived: a ``declined`` row has
        nothing to export, and an archived one is the workspace's deleted
        data -- neither is ever training data.
        """
        query = (
            select(LearningEventModel)
            .where(
                LearningEventModel.organization_id == organization_id,
                LearningEventModel.consent_state == "granted",
                LearningEventModel.archived_at.is_(None),
            )
            .order_by(LearningEventModel.id)
            .limit(limit)
        )
        if workflow_id is not None:
            query = query.where(LearningEventModel.workflow_id == workflow_id)
        async with self.async_session() as session:
            return list((await session.execute(query)).scalars().all())

    async def routing_explore_spend_paise(
        self, *, organization_id: int, workflow_id: int | None, since: datetime
    ) -> int:
        """What this workspace's counterfactual samples cost since ``since``,
        in paise, for one agent or (``workflow_id`` None) for the workspace's
        agent-less work. Archived rows count: the money was spent."""
        query = select(
            func.coalesce(
                func.sum(
                    cast(LearningEventModel.data["cost_paise"].astext, BigInteger)
                ),
                0,
            )
        ).where(
            LearningEventModel.organization_id == organization_id,
            LearningEventModel.event_type == "routing_counterfactual",
            LearningEventModel.created_at >= since,
            LearningEventModel.workflow_id == workflow_id
            if workflow_id is not None
            else LearningEventModel.workflow_id.is_(None),
        )
        async with self.async_session() as session:
            return int(await session.scalar(query) or 0)

    async def link_routing_outcome(
        self,
        *,
        organization_id: int,
        input_ref: str,
        outcome: dict[str, Any],
        usage: dict[str, Any] | None = None,
    ) -> bool:
        """Attach a later outcome (an eval result, a thumb, an approval) to the
        latest routing decision of this workspace for ``input_ref``, and fill
        in the model, tokens and cost the work turned out to use, where the
        decision did not have them. False when there is no such decision."""
        async with self.async_session() as session:
            row = await session.scalar(
                select(LearningEventModel)
                .where(
                    LearningEventModel.organization_id == organization_id,
                    LearningEventModel.event_type == "routing_decision",
                    LearningEventModel.input_ref == input_ref,
                    LearningEventModel.archived_at.is_(None),
                )
                .order_by(LearningEventModel.id.desc())
                .limit(1)
                .with_for_update()
            )
            if row is None:
                return False
            data = dict(row.data or {})
            outcomes = list(data.get("outcomes") or [])
            if outcome not in outcomes:
                outcomes.append(outcome)
            data["outcomes"] = outcomes
            usage = usage or {}
            if usage.get("cost_paise") is not None:
                data["cost_paise"] = int(usage["cost_paise"])
            if usage.get("model") and not row.model:
                row.model = str(usage["model"])[:128]
            if row.prompt_tokens is None and usage.get("prompt_tokens") is not None:
                row.prompt_tokens = usage["prompt_tokens"]
            if (
                row.completion_tokens is None
                and usage.get("completion_tokens") is not None
            ):
                row.completion_tokens = usage["completion_tokens"]
            row.data = data
            await session.commit()
        return True

    async def run_model_cost_paise(
        self, *, organization_id: int, workflow_run_id: int
    ) -> int:
        """What the models behind one run cost us, in paise (the vendor's
        price), read through the run's workspace."""
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
                    WorkflowRunModel.id == workflow_run_id,
                    CallCostItemModel.component.in_(components),
                )
            )
        return int(total or 0)

    async def message_before(
        self, *, organization_id: int, event: Any, actor: str
    ) -> str | None:
        """The text of the latest ``actor`` message in the same conversation
        before ``event`` -- what a reply was a reply to. Read through the
        workspace, in the event's own agent, folder and thread."""
        query = (
            select(AgentEventModel)
            .where(
                AgentEventModel.organization_id == organization_id,
                AgentEventModel.kind == "message",
                AgentEventModel.actor == actor,
                AgentEventModel.id < event.id,
            )
            .order_by(AgentEventModel.id.desc())
            .limit(1)
        )
        for column in ("workflow_id", "folder_id", "thread_id"):
            value = getattr(event, column)
            query = query.where(
                getattr(AgentEventModel, column) == value
                if value is not None
                else getattr(AgentEventModel, column).is_(None)
            )
        async with self.async_session() as session:
            row = await session.scalar(query)
        if row is None:
            return None
        payload = row.payload or {}
        return str(payload.get("body") or row.summary or "") or None

    async def agent_model_spend_paise(
        self, *, organization_id: int, workflow_id: int | None, since: datetime
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
                    WorkflowRunModel.created_at >= since,
                    CallCostItemModel.component.in_(components),
                    *(
                        [WorkflowRunModel.workflow_id == workflow_id]
                        if workflow_id is not None
                        else []
                    ),
                )
            )
        return int(total or 0)
