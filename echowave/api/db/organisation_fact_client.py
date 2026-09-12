"""Reads and writes for what an organization's agents have learned."""

from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.base_client import BaseDBClient
from api.db.models import OrganisationFactModel


class OrganisationFactClient(BaseDBClient):
    async def remember_facts(
        self,
        *,
        organization_id: int,
        subject_type: str,
        subject_key: str,
        facts: dict[str, str],
        source_run_id: Optional[int],
    ) -> int:
        """Write what this call taught us, one row per fact.

        An upsert rather than a read-then-write. Two calls from the same number
        can finish inside the same second -- an agent calling back while the
        first call's post-processing is still running is normal, not exotic --
        and a read-then-write would lose one of them or raise on the unique
        index. The database settles it.

        ``times_seen`` increments only when the value is unchanged. A caller who
        corrects their address is not corroborating the old one, and counting it
        as corroboration is how a wrong fact becomes an entrenched one.
        """
        if not facts:
            return 0

        now = datetime.now(UTC)
        rows = [
            {
                "organization_id": organization_id,
                "subject_type": subject_type,
                "subject_key": subject_key,
                "key": key,
                "value": value,
                "source_run_id": source_run_id,
                "times_seen": 1,
                "first_seen_at": now,
                "last_seen_at": now,
            }
            for key, value in facts.items()
        ]

        statement = pg_insert(OrganisationFactModel).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[
                OrganisationFactModel.organization_id,
                OrganisationFactModel.subject_type,
                OrganisationFactModel.subject_key,
                OrganisationFactModel.key,
            ],
            set_={
                "value": statement.excluded.value,
                "source_run_id": statement.excluded.source_run_id,
                "last_seen_at": statement.excluded.last_seen_at,
                "times_seen": OrganisationFactModel.__table__.c.times_seen
                + (
                    OrganisationFactModel.__table__.c.value == statement.excluded.value
                ).cast(OrganisationFactModel.__table__.c.times_seen.type),
            },
        )

        async with self.async_session() as session:
            await session.execute(statement)
            await session.commit()
        return len(rows)

    async def recall_facts(
        self, *, organization_id: int, subject_type: str, subject_key: str
    ) -> dict[str, Any]:
        """Everything known about one subject, as a flat mapping."""
        async with self.async_session() as session:
            result = await session.execute(
                select(OrganisationFactModel).where(
                    OrganisationFactModel.organization_id == organization_id,
                    OrganisationFactModel.subject_type == subject_type,
                    OrganisationFactModel.subject_key == subject_key,
                )
            )
            return {row.key: row.value for row in result.scalars().all()}
