"""Reads and writes for the record of what agents did in outside software."""

from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import case, func, select

from api.db.base_client import BaseDBClient
from api.db.models import (
    AppInteractionModel,
    WorkflowDefinitionModel,
    WorkflowRunModel,
)


class AppInteractionClient(BaseDBClient):
    async def create_app_interaction(
        self,
        *,
        organization_id: int,
        workflow_run_id: Optional[int],
        workflow_id: Optional[int],
        definition_id: Optional[int],
        kind: str,
        app: Optional[str],
        name: str,
        status: str,
        error: Optional[str],
        duration_ms: Optional[int],
    ) -> None:
        """Insert one row.

        Returns nothing on purpose. The caller is a live call that has already
        given the model its answer; there is no id it could use and no decision
        it could make differently, and returning a model would invite somebody
        to await this before continuing the conversation.
        """
        async with self.async_session() as session:
            session.add(
                AppInteractionModel(
                    organization_id=organization_id,
                    workflow_run_id=workflow_run_id,
                    workflow_id=workflow_id,
                    definition_id=definition_id,
                    kind=kind,
                    app=app,
                    name=name,
                    status=status,
                    error=error,
                    duration_ms=duration_ms,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def app_interactions_for_run(self, workflow_run_id: int) -> list[Any]:
        """What one call actually did, oldest first.

        The support question. Somebody says the agent did not send the
        confirmation; this says whether it tried, and what came back.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(AppInteractionModel)
                .where(AppInteractionModel.workflow_run_id == workflow_run_id)
                .order_by(AppInteractionModel.created_at.asc())
            )
            return list(result.scalars().all())

    async def app_interaction_summary(
        self, *, organization_id: int, days: int = 30
    ) -> list[dict[str, Any]]:
        """Per-app counts, failures and typical latency for one organization.

        Average rather than a percentile because Postgres gives the average for
        free and a percentile needs an ordered-set aggregate over a column with
        no index for it. The average is the wrong statistic for latency and the
        right one for the question this answers, which is "is this connector
        roughly fast or roughly slow" rather than "what does the worst call
        look like".
        """
        since = datetime.now(UTC) - timedelta(days=days)
        async with self.async_session() as session:
            result = await session.execute(
                select(
                    AppInteractionModel.kind,
                    AppInteractionModel.app,
                    func.count(AppInteractionModel.id).label("calls"),
                    func.sum(
                        case((AppInteractionModel.status == "error", 1), else_=0)
                    ).label("errors"),
                    func.avg(AppInteractionModel.duration_ms).label("avg_ms"),
                )
                .where(
                    AppInteractionModel.organization_id == organization_id,
                    AppInteractionModel.created_at >= since,
                )
                .group_by(AppInteractionModel.kind, AppInteractionModel.app)
                .order_by(func.count(AppInteractionModel.id).desc())
            )
            return [
                {
                    "kind": row.kind,
                    "app": row.app,
                    "calls": int(row.calls or 0),
                    "errors": int(row.errors or 0),
                    "avg_ms": int(row.avg_ms) if row.avg_ms is not None else None,
                }
                for row in result.all()
            ]

    async def run_attribution(self, workflow_run_id: int) -> dict[str, Optional[int]]:
        """Which agent, and which version of it, a run belongs to.

        Two integers off an indexed primary key, deliberately rather than
        reusing ``get_workflow_run_with_context``: that loads the workflow, its
        definition and the organization to answer a question that needs neither,
        and this is called from a live call.
        """
        async with self.async_session() as session:
            row = (
                await session.execute(
                    select(
                        WorkflowRunModel.workflow_id,
                        WorkflowRunModel.definition_id,
                    ).where(WorkflowRunModel.id == workflow_run_id)
                )
            ).first()
        if not row:
            return {"workflow_id": None, "definition_id": None}
        return {"workflow_id": row.workflow_id, "definition_id": row.definition_id}

    async def outcomes_by_version(
        self, *, organization_id: int, workflow_id: int, days: int = 30
    ) -> list[dict[str, Any]]:
        """What each published version of one agent actually achieved.

        The query the whole of version attribution exists for. Before this,
        "we changed the prompt and bookings fell" was a story somebody told;
        now it is a number somebody checks.

        A call counts as having produced an outcome when at least one of its
        actions touched an outside app and succeeded. That is the product's own
        claim -- a call is finished when the record exists -- so the metric and
        the pitch are the same sentence, and a calculator call cannot flatter
        it because ``app`` is null on tools that touch nothing.
        """
        since = datetime.now(UTC) - timedelta(days=days)

        # Runs that reached an outside system, per version.
        succeeded = (
            select(
                AppInteractionModel.definition_id.label("definition_id"),
                AppInteractionModel.workflow_run_id.label("run_id"),
            )
            .where(
                AppInteractionModel.organization_id == organization_id,
                AppInteractionModel.workflow_id == workflow_id,
                AppInteractionModel.created_at >= since,
                AppInteractionModel.status == "success",
                AppInteractionModel.app.isnot(None),
                AppInteractionModel.workflow_run_id.isnot(None),
            )
            .distinct()
            .subquery()
        )

        async with self.async_session() as session:
            runs = (
                await session.execute(
                    select(
                        WorkflowRunModel.definition_id,
                        WorkflowDefinitionModel.version_number,
                        WorkflowDefinitionModel.published_at,
                        func.count(WorkflowRunModel.id).label("calls"),
                    )
                    .join(
                        WorkflowDefinitionModel,
                        WorkflowRunModel.definition_id == WorkflowDefinitionModel.id,
                    )
                    .where(
                        WorkflowRunModel.workflow_id == workflow_id,
                        WorkflowRunModel.created_at >= since,
                    )
                    .group_by(
                        WorkflowRunModel.definition_id,
                        WorkflowDefinitionModel.version_number,
                        WorkflowDefinitionModel.published_at,
                    )
                )
            ).all()

            produced = dict(
                (
                    await session.execute(
                        select(
                            succeeded.c.definition_id,
                            func.count(func.distinct(succeeded.c.run_id)),
                        ).group_by(succeeded.c.definition_id)
                    )
                ).all()
            )

        out: list[dict[str, Any]] = []
        for row in runs:
            calls = int(row.calls or 0)
            with_outcome = int(produced.get(row.definition_id, 0))
            out.append(
                {
                    "definition_id": row.definition_id,
                    "version_number": row.version_number,
                    "published_at": row.published_at,
                    "calls": calls,
                    "calls_with_outcome": with_outcome,
                    # None rather than zero on no calls. A version nobody has
                    # run yet has no rate, and printing 0% next to it reads as
                    # "this version fails", which is a different claim.
                    "outcome_rate": (round(with_outcome / calls, 4) if calls else None),
                }
            )
        out.sort(key=lambda r: (r["version_number"] is None, r["version_number"] or 0))
        return out
