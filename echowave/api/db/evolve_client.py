"""Reads and writes for evolving skills (services/evolve). Org-scoped on
every one: no method here takes an id without the organisation it must
belong to."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from api.db.base_client import BaseDBClient
from api.db.evolve_models import ExperienceRecordModel, SkillVersionModel


class EvolveClient(BaseDBClient):
    # --- experience ---------------------------------------------------------

    async def insert_experience(
        self, *, organization_id: int, run_key: str, kind: str, **fields: Any
    ) -> tuple[int, bool]:
        """Write one record, or find the one already written.

        Returns ``(id, created)``. ``ON CONFLICT DO NOTHING`` on the unique
        ``(organization_id, run_key, kind)``, so two workers collecting the
        same run at once write one row and both learn its id.
        """
        now = datetime.now(UTC)
        values = {
            "organization_id": organization_id,
            "run_key": run_key,
            "kind": kind,
            "occurred_at": fields.pop("occurred_at", None) or now,
            "created_at": now,
            **fields,
        }
        values.setdefault("tool_calls", [])
        values.setdefault("evidence", [])
        async with self.async_session() as session:
            stmt = (
                insert(ExperienceRecordModel)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["organization_id", "run_key", "kind"]
                )
                .returning(ExperienceRecordModel.id)
            )
            new_id = (await session.execute(stmt)).scalar()
            if new_id is not None:
                await session.commit()
                return int(new_id), True
            existing = await session.scalar(
                select(ExperienceRecordModel.id).where(
                    ExperienceRecordModel.organization_id == organization_id,
                    ExperienceRecordModel.run_key == run_key,
                    ExperienceRecordModel.kind == kind,
                )
            )
            await session.commit()
            return int(existing), False

    async def list_experience(
        self,
        *,
        organization_id: int,
        task_family: str | None = None,
        exclude_family: str | None = None,
        split: str | None = None,
        kinds: Iterable[str] | None = None,
        viewer_user_id: int | None = None,
        include_personal: bool = False,
        skill_version: int | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> Sequence[ExperienceRecordModel]:
        """Records for this workspace, newest first.

        Personal records are left out unless asked for, and then only the
        viewer's own: ``include_personal`` without a viewer returns none of
        them, which is the safe direction for a filter that removes things
        (api/AGENTS.md, Silent Absence -- here the absence is the point).
        """
        async with self.async_session() as session:
            query = select(ExperienceRecordModel).where(
                ExperienceRecordModel.organization_id == organization_id
            )
            if include_personal and viewer_user_id is not None:
                query = query.where(
                    or_(
                        ExperienceRecordModel.scope == "workspace",
                        and_(
                            ExperienceRecordModel.scope == "personal",
                            ExperienceRecordModel.user_id == viewer_user_id,
                        ),
                    )
                )
            else:
                query = query.where(ExperienceRecordModel.scope == "workspace")
            if task_family is not None:
                query = query.where(ExperienceRecordModel.task_family == task_family)
            if exclude_family is not None:
                query = query.where(ExperienceRecordModel.task_family != exclude_family)
            if split is not None:
                query = query.where(ExperienceRecordModel.split == split)
            if kinds is not None:
                query = query.where(ExperienceRecordModel.kind.in_(list(kinds)))
            if skill_version is not None:
                query = query.where(
                    ExperienceRecordModel.skill_version == skill_version
                )
            if since is not None:
                query = query.where(ExperienceRecordModel.occurred_at >= since)
            query = query.order_by(ExperienceRecordModel.id.desc()).limit(limit)
            return (await session.execute(query)).scalars().all()

    async def count_experience(
        self, *, organization_id: int, task_family: str
    ) -> dict[str, int]:
        """Workspace records for one family, by outcome."""
        async with self.async_session() as session:
            rows = await session.execute(
                select(ExperienceRecordModel.outcome, func.count())
                .where(
                    ExperienceRecordModel.organization_id == organization_id,
                    ExperienceRecordModel.task_family == task_family,
                    ExperienceRecordModel.scope == "workspace",
                )
                .group_by(ExperienceRecordModel.outcome)
            )
            return {str(outcome): int(n) for outcome, n in rows.all()}

    # --- skill versions -----------------------------------------------------

    async def create_skill_version(
        self, *, organization_id: int, slug: str, **fields: Any
    ) -> SkillVersionModel:
        """A new version, numbered one past the highest so far.

        The number is taken under the unique index: two writers that both
        read 3 cannot both write 4, and the loser takes 5 instead.
        """
        now = datetime.now(UTC)
        for _ in range(5):
            async with self.async_session() as session:
                highest = await session.scalar(
                    select(func.max(SkillVersionModel.version)).where(
                        SkillVersionModel.organization_id == organization_id,
                        SkillVersionModel.slug == slug,
                    )
                )
                row = SkillVersionModel(
                    organization_id=organization_id,
                    slug=slug,
                    version=int(highest or 0) + 1,
                    created_at=now,
                    updated_at=now,
                    **fields,
                )
                row.evidence = row.evidence or []
                row.cost = row.cost or {}
                row.content = row.content or {}
                session.add(row)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    continue
                await session.refresh(row)
                return row
        raise RuntimeError("Could not number a new skill version")

    async def get_skill_version(
        self, version_id: int, *, organization_id: int
    ) -> SkillVersionModel | None:
        async with self.async_session() as session:
            return await session.scalar(
                select(SkillVersionModel).where(
                    SkillVersionModel.id == version_id,
                    SkillVersionModel.organization_id == organization_id,
                )
            )

    async def list_skill_versions(
        self,
        *,
        organization_id: int,
        slug: str | None = None,
        statuses: Iterable[str] | None = None,
    ) -> Sequence[SkillVersionModel]:
        """Versions oldest first, the order the history reads in."""
        async with self.async_session() as session:
            query = select(SkillVersionModel).where(
                SkillVersionModel.organization_id == organization_id
            )
            if slug is not None:
                query = query.where(SkillVersionModel.slug == slug)
            if statuses is not None:
                query = query.where(SkillVersionModel.status.in_(list(statuses)))
            query = query.order_by(
                SkillVersionModel.slug.asc(), SkillVersionModel.version.asc()
            )
            return (await session.execute(query)).scalars().all()

    async def active_skill_version(
        self, *, organization_id: int, slug: str
    ) -> SkillVersionModel | None:
        """The newest published version: what every prompt reads."""
        async with self.async_session() as session:
            return await session.scalar(
                select(SkillVersionModel)
                .where(
                    SkillVersionModel.organization_id == organization_id,
                    SkillVersionModel.slug == slug,
                    SkillVersionModel.status == "published",
                )
                .order_by(SkillVersionModel.version.desc())
                .limit(1)
            )

    async def skill_version_active_at(
        self, *, organization_id: int, slug: str, at: datetime
    ) -> SkillVersionModel | None:
        """The version a task used: published by ``at`` and not rolled back
        before it. Read off the rows' own timestamps, so it stays true after
        later publishes."""
        async with self.async_session() as session:
            return await session.scalar(
                select(SkillVersionModel)
                .where(
                    SkillVersionModel.organization_id == organization_id,
                    SkillVersionModel.slug == slug,
                    SkillVersionModel.published_at.is_not(None),
                    SkillVersionModel.published_at <= at,
                    or_(
                        SkillVersionModel.rolled_back_at.is_(None),
                        SkillVersionModel.rolled_back_at > at,
                    ),
                )
                .order_by(SkillVersionModel.version.desc())
                .limit(1)
            )

    async def move_skill_version(
        self,
        version_id: int,
        *,
        organization_id: int,
        from_statuses: Iterable[str],
        to_status: str,
        **fields: Any,
    ) -> bool:
        """Compare-and-swap the status. False when it had already moved:
        two presses of Publish publish once."""
        async with self.async_session() as session:
            result = await session.execute(
                update(SkillVersionModel)
                .where(
                    SkillVersionModel.id == version_id,
                    SkillVersionModel.organization_id == organization_id,
                    SkillVersionModel.status.in_(list(from_statuses)),
                )
                .values(status=to_status, updated_at=datetime.now(UTC), **fields)
            )
            await session.commit()
            return bool(result.rowcount)

    async def update_skill_version(
        self, version_id: int, *, organization_id: int, **fields: Any
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                update(SkillVersionModel)
                .where(
                    SkillVersionModel.id == version_id,
                    SkillVersionModel.organization_id == organization_id,
                )
                .values(updated_at=datetime.now(UTC), **fields)
            )
            await session.commit()
            return bool(result.rowcount)

    async def organisations_with_skills(self) -> list[int]:
        """Every workspace that keeps a skill or has a skill version: the
        only ones the learning job has anything to do for."""
        from api.db.models import OrganisationSkillModel

        async with self.async_session() as session:
            kept = await session.execute(
                select(OrganisationSkillModel.organization_id).distinct()
            )
            versioned = await session.execute(
                select(SkillVersionModel.organization_id).distinct()
            )
            return sorted(
                {int(r[0]) for r in kept.all()} | {int(r[0]) for r in versioned.all()}
            )

    async def finished_runs_since(
        self, *, organization_id: int, since: datetime, limit: int = 200
    ) -> Sequence[Any]:
        """Completed runs of this workspace's agents since ``since``."""
        from api.db.models import WorkflowModel, WorkflowRunModel

        async with self.async_session() as session:
            result = await session.execute(
                select(WorkflowRunModel)
                .join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id)
                .where(
                    WorkflowModel.organization_id == organization_id,
                    WorkflowRunModel.is_completed.is_(True),
                    WorkflowRunModel.created_at >= since,
                )
                .order_by(WorkflowRunModel.id.asc())
                .limit(limit)
            )
            return result.scalars().all()
