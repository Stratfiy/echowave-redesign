"""The record of a script run: written as it happens, so a box that dies
leaves a row that says what it managed."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import select, update

from api.db.base_client import BaseDBClient
from api.db.models import SandboxJobModel


class SandboxJobClient(BaseDBClient):
    async def create_sandbox_job(
        self,
        *,
        organization_id: int,
        workflow_id: Optional[int],
        workflow_run_id: Optional[int],
        code_hash: str,
        code_chars: int,
    ) -> SandboxJobModel:
        async with self.async_session() as session:
            row = SandboxJobModel(
                organization_id=organization_id,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                code_hash=code_hash,
                code_chars=code_chars,
                status="running",
                calls=0,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def touch_sandbox_job(self, job_id: int, *, calls: int) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(SandboxJobModel)
                .where(SandboxJobModel.id == job_id)
                .values(calls=calls)
            )
            await session.commit()

    async def finish_sandbox_job(
        self,
        job_id: int,
        *,
        status: str,
        exit_code: Optional[int],
        calls: int,
        output: str,
        error: str,
    ) -> None:
        async with self.async_session() as session:
            await session.execute(
                update(SandboxJobModel)
                .where(SandboxJobModel.id == job_id)
                .values(
                    status=status,
                    exit_code=exit_code,
                    calls=calls,
                    output=output,
                    error=error,
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def sandbox_jobs(self, *, organization_id: int, limit: int = 50) -> list[Any]:
        async with self.async_session() as session:
            result = await session.execute(
                select(SandboxJobModel)
                .where(SandboxJobModel.organization_id == organization_id)
                .order_by(SandboxJobModel.id.desc())
                .limit(limit)
            )
            return list(result.scalars().all())
