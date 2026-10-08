"""Reads and writes for the task board. Org-scoped on every read."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, text

from api.db.base_client import BaseDBClient
from api.db.models import AgentTaskCommentModel, AgentTaskModel

#: The first key of the advisory lock that serializes task numbering inside
#: one workspace (the second key is the workspace's id).
_NUMBERING_LOCK = 7_140_001


def visible_clause(user_id: int | None):
    """The tasks a person may see: the workspace's, and their own private
    ones. ``None`` (nobody known) sees no private task."""
    if user_id is None:
        return AgentTaskModel.private_to_user_id.is_(None)
    return or_(
        AgentTaskModel.private_to_user_id.is_(None),
        AgentTaskModel.private_to_user_id == user_id,
    )


def is_visible_to(task: Any, user_id: int | None) -> bool:
    """``visible_clause`` for a row already read."""
    owner = getattr(task, "private_to_user_id", None)
    return owner is None or (user_id is not None and int(owner) == int(user_id))


#: The default ``visible_to``: every task, for the worker and staff, which
#: act on all of them.
EVERYONE = object()


class AgentTaskClient(BaseDBClient):
    async def tasks_for_organization(
        self,
        organization_id: int,
        *,
        limit: int = 200,
        visible_to: int | None | object = EVERYONE,
    ) -> Sequence[AgentTaskModel]:
        """The workspace's tasks. A screen or a tool answering a person passes
        ``visible_to`` with that person's id, so another person's private
        task (a meeting's follow-up) is not among them."""
        query = select(AgentTaskModel).where(
            AgentTaskModel.organization_id == organization_id
        )
        if visible_to is not EVERYONE:
            query = query.where(visible_clause(visible_to))
        async with self.async_session() as session:
            result = await session.execute(
                query.order_by(AgentTaskModel.id.desc()).limit(limit)
            )
            return result.scalars().all()

    async def get_task(
        self,
        task_id: int,
        *,
        organization_id: int,
        visible_to: int | None | object = EVERYONE,
    ) -> AgentTaskModel | None:
        """One task, or None. With ``visible_to``, another person's private
        task is None too, the way a wrong workspace's is."""
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentTaskModel).where(
                    AgentTaskModel.id == task_id,
                    AgentTaskModel.organization_id == organization_id,
                )
            )
            task = result.scalar_one_or_none()
        if task is not None and visible_to is not EVERYONE:
            if not is_visible_to(task, visible_to):
                return None
        return task

    async def tasks_due_between(self, start, end) -> list[AgentTaskModel]:
        """Open tasks, every account, due inside [start, end): the daily
        reminder sweep's one query."""
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentTaskModel)
                .where(
                    AgentTaskModel.status == "todo",
                    AgentTaskModel.due_at.is_not(None),
                    AgentTaskModel.due_at >= start,
                    AgentTaskModel.due_at < end,
                )
                .order_by(AgentTaskModel.organization_id, AgentTaskModel.due_at)
            )
            return list(result.scalars().all())

    async def create_task(self, *, organization_id: int, **fields) -> AgentTaskModel:
        """A row, numbered inside its workspace (TB-1): the identifier is
        ``prefix-number``, and the number never moves."""
        async with self.async_session() as session:
            if fields.get("number") is None:
                # Two tasks made at once (two cards confirmed together) would
                # both read the same highest number and one would fail on
                # ``uq_agent_tasks_org_number``. The lock is per workspace and
                # held until this transaction commits.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:k, :o)"),
                    {"k": _NUMBERING_LOCK, "o": organization_id},
                )
                highest = await session.scalar(
                    select(func.max(AgentTaskModel.number)).where(
                        AgentTaskModel.organization_id == organization_id
                    )
                )
                fields["number"] = int(highest or 0) + 1
            task = AgentTaskModel(organization_id=organization_id, **fields)
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task

    async def update_task(
        self,
        task_id: int,
        *,
        organization_id: int,
        unless_status: Sequence[str] = (),
        **fields: Any,
    ) -> AgentTaskModel | None:
        """Change fields. A status of ``in_progress`` stamps ``started_at``;
        a terminal status stamps ``finished_at``.

        ``unless_status`` makes it a checkout (TB-2): the row is locked, and
        left alone -- None is returned -- if it is already in one of those
        statuses. Two workers picking up the same task cannot both start it.
        """
        async with self.async_session() as session:
            query = select(AgentTaskModel).where(
                AgentTaskModel.id == task_id,
                AgentTaskModel.organization_id == organization_id,
            )
            if unless_status:
                query = query.with_for_update()
            result = await session.execute(query)
            task = result.scalar_one_or_none()
            if task is None or task.status in unless_status:
                return None
            for key, value in fields.items():
                setattr(task, key, value)
            status = fields.get("status")
            now = datetime.now(UTC)
            if status == "in_progress" and task.started_at is None:
                task.started_at = now
            if status in ("done", "cancelled"):
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

    async def subtasks_of(
        self,
        task_id: int,
        *,
        organization_id: int,
        visible_to: int | None | object = EVERYONE,
    ) -> list[AgentTaskModel]:
        query = select(AgentTaskModel).where(
            AgentTaskModel.parent_id == task_id,
            AgentTaskModel.organization_id == organization_id,
        )
        if visible_to is not EVERYONE:
            query = query.where(visible_clause(visible_to))
        async with self.async_session() as session:
            result = await session.execute(query.order_by(AgentTaskModel.id))
            return list(result.scalars().all())

    # --- comments (TB-1) ---------------------------------------------------

    async def comments_for_task(
        self, task_id: int, *, organization_id: int
    ) -> list[AgentTaskCommentModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(AgentTaskCommentModel)
                .where(
                    AgentTaskCommentModel.task_id == task_id,
                    AgentTaskCommentModel.organization_id == organization_id,
                )
                .order_by(AgentTaskCommentModel.id)
            )
            return list(result.scalars().all())

    async def comment_counts(
        self, organization_id: int, task_ids: Sequence[int]
    ) -> dict[int, int]:
        if not task_ids:
            return {}
        async with self.async_session() as session:
            rows = await session.execute(
                select(AgentTaskCommentModel.task_id, func.count())
                .where(
                    AgentTaskCommentModel.organization_id == organization_id,
                    AgentTaskCommentModel.task_id.in_(list(task_ids)),
                )
                .group_by(AgentTaskCommentModel.task_id)
            )
            return {int(task_id): int(n) for task_id, n in rows.all()}

    async def add_task_comment(
        self,
        *,
        organization_id: int,
        task_id: int,
        body: str,
        author_user_id: int | None = None,
        author_workflow_id: int | None = None,
    ) -> AgentTaskCommentModel:
        async with self.async_session() as session:
            row = AgentTaskCommentModel(
                organization_id=organization_id,
                task_id=task_id,
                body=body,
                author_user_id=author_user_id,
                author_workflow_id=author_workflow_id,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row
