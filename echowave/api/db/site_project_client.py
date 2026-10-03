"""Studio sites. Every read and write is scoped by ``organization_id`` in the
query itself, except the lookup by preview token, which is the one thing the
public preview has to go on."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import defer

from api.db.base_client import BaseDBClient
from api.db.site_project_models import SiteProjectModel


class SiteProjectClient(BaseDBClient):
    async def create_site_project(
        self,
        *,
        organization_id: int,
        created_by_user_id: int | None,
        name: str,
        framework: str,
        files: dict[str, str],
        preview_token: str,
    ) -> SiteProjectModel:
        async with self.async_session() as session:
            row = SiteProjectModel(
                organization_id=organization_id,
                created_by_user_id=created_by_user_id,
                name=name,
                framework=framework,
                files=files,
                agent_workflow_ids=[],
                preview_token=preview_token,
                build_status="none",
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_site_projects(self, organization_id: int) -> list[SiteProjectModel]:
        """Newest first, without the file maps: a list never needs them."""
        async with self.async_session() as session:
            result = await session.execute(
                select(SiteProjectModel)
                .options(defer(SiteProjectModel.files), defer(SiteProjectModel.dist))
                .where(SiteProjectModel.organization_id == organization_id)
                .order_by(SiteProjectModel.updated_at.desc())
            )
            return list(result.scalars().all())

    async def get_site_project(
        self, site_id: int, *, organization_id: int
    ) -> SiteProjectModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(SiteProjectModel).where(
                    SiteProjectModel.id == site_id,
                    SiteProjectModel.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    async def get_site_project_by_preview_token(
        self, preview_token: str
    ) -> SiteProjectModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(SiteProjectModel).where(
                    SiteProjectModel.preview_token == preview_token
                )
            )
            return result.scalar_one_or_none()

    async def update_site_project(
        self,
        site_id: int,
        *,
        organization_id: int,
        name: str | None = None,
        files: dict[str, str] | None = None,
        agent_workflow_ids: list[int] | None = None,
    ) -> SiteProjectModel | None:
        values: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if name is not None:
            values["name"] = name
        if files is not None:
            values["files"] = files
        if agent_workflow_ids is not None:
            values["agent_workflow_ids"] = agent_workflow_ids
        async with self.async_session() as session:
            await session.execute(
                update(SiteProjectModel)
                .where(
                    SiteProjectModel.id == site_id,
                    SiteProjectModel.organization_id == organization_id,
                )
                .values(**values)
            )
            await session.commit()
        return await self.get_site_project(site_id, organization_id=organization_id)

    async def claim_site_build(
        self, site_id: int, *, organization_id: int, stale_before: datetime
    ) -> bool:
        """Mark the site ``building`` unless a build is already running.

        One atomic UPDATE, so two clicks cannot start two builds. A build
        that started before ``stale_before`` is taken to have died with its
        worker and may be claimed again.
        """
        async with self.async_session() as session:
            result = await session.execute(
                update(SiteProjectModel)
                .where(
                    SiteProjectModel.id == site_id,
                    SiteProjectModel.organization_id == organization_id,
                    or_(
                        SiteProjectModel.build_status != "building",
                        SiteProjectModel.build_started_at.is_(None),
                        SiteProjectModel.build_started_at < stale_before,
                    ),
                )
                .values(build_status="building", build_started_at=datetime.now(UTC))
                .returning(SiteProjectModel.id)
            )
            claimed = result.scalar_one_or_none() is not None
            await session.commit()
            return claimed

    async def finish_site_build(
        self,
        site_id: int,
        *,
        organization_id: int,
        succeeded: bool,
        log: str,
        seconds: float | None,
        dist: dict[str, str] | None,
    ) -> None:
        """Record the outcome. ``dist`` is replaced only on success, so a
        failed rebuild leaves the last good preview up."""
        values: dict[str, Any] = {
            "build_status": "succeeded" if succeeded else "failed",
            "build_log": log,
            "build_seconds": seconds,
        }
        if succeeded:
            values["dist"] = dist or {}
            values["built_at"] = datetime.now(UTC)
        async with self.async_session() as session:
            await session.execute(
                update(SiteProjectModel)
                .where(
                    SiteProjectModel.id == site_id,
                    SiteProjectModel.organization_id == organization_id,
                )
                .values(**values)
            )
            await session.commit()

    async def delete_site_project(self, site_id: int, *, organization_id: int) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(SiteProjectModel)
                .where(
                    SiteProjectModel.id == site_id,
                    SiteProjectModel.organization_id == organization_id,
                )
                .returning(SiteProjectModel.id)
            )
            deleted = result.scalar_one_or_none() is not None
            await session.commit()
            return deleted
