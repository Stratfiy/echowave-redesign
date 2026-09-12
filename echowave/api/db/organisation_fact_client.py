"""Reads and writes for what an organization's agents have learned."""

import re
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.base_client import BaseDBClient
from api.db.models import OrganisationFactModel

#: The subject facts about the business itself hang off, as opposed to facts
#: about one of its customers.
SUBJECT_ORGANISATION = "organisation"


#: ``key`` is 128 characters and carries the identity of an observation, so a
#: long question has to fold onto a stable short form. Lowercased and stripped
#: of punctuation so "Do you open on Saturday?" and "do you open on saturday"
#: are one row rather than two.
def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return (cleaned or "unknown")[:128]


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

    async def remember_organisation_observations(
        self,
        *,
        organization_id: int,
        source_run_id: Optional[int],
        observations: list[dict[str, str]],
    ) -> int:
        """Record what calls taught the business about itself.

        Mapped onto the same four-column identity the table already enforces:
        ``subject_key`` is which gap ("not_understood"), ``key`` is a stable
        slug of the thing itself, and ``value`` is that thing as a person reads
        it. So the same unanswerable question asked forty times is one row with
        ``times_seen`` at forty, which is the number worth acting on, and forty
        different questions are forty rows, which is the list worth reading.

        Everything written here is ``learned``. Nothing reaches an agent's
        prompt until somebody says yes.
        """
        if not observations:
            return 0

        now = datetime.now(UTC)
        rows = []
        for observation in observations:
            value = str(observation.get("value") or "").strip()
            if not value:
                continue
            rows.append(
                {
                    "organization_id": organization_id,
                    "subject_type": SUBJECT_ORGANISATION,
                    "subject_key": str(observation.get("key") or "")[:255],
                    "key": _slug(value),
                    "value": value,
                    "kind": str(observation.get("kind") or "gap"),
                    "status": "learned",
                    "source_run_id": source_run_id,
                    "times_seen": 1,
                    "first_seen_at": now,
                    "last_seen_at": now,
                }
            )
        if not rows:
            return 0

        statement = pg_insert(OrganisationFactModel).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[
                OrganisationFactModel.organization_id,
                OrganisationFactModel.subject_type,
                OrganisationFactModel.subject_key,
                OrganisationFactModel.key,
            ],
            set_={
                "last_seen_at": statement.excluded.last_seen_at,
                "source_run_id": statement.excluded.source_run_id,
                "times_seen": OrganisationFactModel.__table__.c.times_seen + 1,
            },
            # A gap somebody already rejected stays rejected however often it
            # recurs. Otherwise dismissing something would only silence it
            # until the next call, and a list that will not stay dismissed is
            # a list people stop reading.
            where=OrganisationFactModel.__table__.c.status != "rejected",
        )

        async with self.async_session() as session:
            await session.execute(statement)
            await session.commit()
        return len(rows)

    async def remember_organisation_facts(
        self,
        *,
        organization_id: int,
        facts: dict[str, str],
        source_run_id: Optional[int] = None,
        status: str = "confirmed",
    ) -> int:
        """What the business told us about itself.

        Confirmed by default, because the path into here is a person answering
        a question on the onboarding form or typing it into the chat. That is
        the same person who would confirm it afterwards, so asking twice would
        be ceremony.
        """
        if not facts:
            return 0

        now = datetime.now(UTC)
        rows = [
            {
                "organization_id": organization_id,
                "subject_type": SUBJECT_ORGANISATION,
                "subject_key": "self",
                "key": key,
                "value": value,
                "kind": "fact",
                "status": status,
                "confirmed_at": now if status == "confirmed" else None,
                "source_run_id": source_run_id,
                "times_seen": 1,
                "first_seen_at": now,
                "last_seen_at": now,
            }
            for key, value in facts.items()
            if str(value or "").strip()
        ]
        if not rows:
            return 0

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
                "status": statement.excluded.status,
                "confirmed_at": statement.excluded.confirmed_at,
                "last_seen_at": statement.excluded.last_seen_at,
            },
        )

        async with self.async_session() as session:
            await session.execute(statement)
            await session.commit()
        return len(rows)

    async def organisation_memory(
        self,
        *,
        organization_id: int,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 200,
    ) -> list[Any]:
        """What this business knows and what it still cannot answer.

        Most-seen first: the question forty callers asked is the one worth
        reading, and burying it under thirty-nine one-offs would make the
        screen useless at exactly the size where it starts to matter.
        """
        async with self.async_session() as session:
            query = select(OrganisationFactModel).where(
                OrganisationFactModel.organization_id == organization_id,
                OrganisationFactModel.subject_type == SUBJECT_ORGANISATION,
            )
            if kind:
                query = query.where(OrganisationFactModel.kind == kind)
            if status:
                query = query.where(OrganisationFactModel.status == status)
            query = query.order_by(
                OrganisationFactModel.times_seen.desc(),
                OrganisationFactModel.last_seen_at.desc(),
            ).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def set_organisation_fact_status(
        self,
        *,
        organization_id: int,
        fact_id: int,
        status: str,
    ) -> bool:
        """Believe it, or dismiss it.

        Scoped by organization in the WHERE clause rather than checked after
        the read: an id in a request body proves nothing, and confirming
        somebody else's fact would put their business's answer into your
        agent's mouth.
        """
        async with self.async_session() as session:
            result = await session.execute(
                sa_update(OrganisationFactModel)
                .where(
                    OrganisationFactModel.id == fact_id,
                    OrganisationFactModel.organization_id == organization_id,
                )
                .values(
                    status=status,
                    confirmed_at=(datetime.now(UTC) if status == "confirmed" else None),
                )
            )
            await session.commit()
            return bool(result.rowcount)
