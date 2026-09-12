"""Reads and writes for the record of what agents did in outside software."""

from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from sqlalchemy import case, func, select

from api.db.base_client import BaseDBClient
from api.db.models import AppInteractionModel


class AppInteractionClient(BaseDBClient):
    async def create_app_interaction(
        self,
        *,
        organization_id: int,
        workflow_run_id: Optional[int],
        workflow_id: Optional[int],
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
