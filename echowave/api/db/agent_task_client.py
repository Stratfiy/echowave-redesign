"""Reads and writes for the task board. Org-scoped on every read."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from api.db.base_client import BaseDBClient
from api.db.models import AgentTaskModel


class AgentTaskClient(BaseDBClient):
    async def tasks_for_organization(
        self, organization_id: int, *, limit: int = 200
    ) -> Sequence[AgentTaskModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentTaskModel)
                .where(AgentTaskModel.organization_id == organization_id)
                .order_by(AgentTaskModel.id.desc())
                .limit(limit)
            )
            return result.scalars().all()

    async def get_task(
        self, task_id: int, *, organization_id: int
    ) -> AgentTaskModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentTaskModel).where(
                    AgentTaskModel.id == task_id,
                    AgentTaskModel.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    async def create_task(self, *, organization_id: int, **fields) -> AgentTaskModel:
        async with self.async_session() as session:
            task = AgentTaskModel(organization_id=organization_id, **fields)
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    async def update_task(
        self, task_id: int, *, organization_id: int, **fields: Any
    ) -> AgentTaskModel | None:
        """Change fields. A status of ``doing`` stamps ``started_at``; a
        terminal status stamps ``finished_at``."""
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentTaskModel).where(
                    AgentTaskModel.id == task_id,
                    AgentTaskModel.organization_id == organization_id,
                )
            )
            task = result.scalar_one_or_none()
            if task is None:
                return None
            for key, value in fields.items():
                setattr(task, key, value)
            status = fields.get("status")
            now = datetime.now(UTC)
            if status == "doing" and task.started_at is None:
                task.started_at = now
            if status in ("done", "could_not"):
                task.finished_at = now
            await session.commit()
            await session.refresh(task)
            return task

    async def delete_task(self, task_id: int, *, organization_id: int) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentTaskModel).where(
                    AgentTaskModel.id == task_id,
                    AgentTaskModel.organization_id == organization_id,
                )
            )
            task = result.scalar_one_or_none()
            if task is None:
                return False
            await session.delete(task)
            await session.commit()
            return True
