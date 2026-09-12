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

    async def organisation_graph_edges(
        self, *, organization_id: int, days: int = 90
    ) -> dict[str, Any]:
        """The organisation's memory as nodes and edges, read straight off the
        tables that already record it.

        No graph store. Every edge here is a join over rows we keep anyway, so
        there is nothing to index, nothing to sync and nothing that can drift
        from the truth it is drawn from. If traversal ever gets slow -- it will
        not at this size -- that is the day to revisit it, not before.

        Three queries, because facts, actions and agents live in three tables
        and joining them would multiply each agent's fact count by its action
        count. The shape is assembled here rather than in SQL for the same
        reason the team endpoint does it: the grains differ.
        """
        since = datetime.now(UTC) - timedelta(days=days)
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []

        def node(node_id: str, kind: str, label: str, **extra: Any) -> str:
            existing = nodes.get(node_id)
            if existing is None:
                nodes[node_id] = {
                    "id": node_id,
                    "kind": kind,
                    "label": label,
                    **extra,
                }
            return node_id

        org_node = node(f"org:{organization_id}", "organisation", "This business")

        async with self.async_session() as session:
            # What the business knows and cannot answer, and which agent
            # learned each one. The attribution is what makes the graph worth
            # looking at: an agent switched off in March still has edges.
            memory = (
                await session.execute(
                    select(
                        OrganisationFactModel.id,
                        OrganisationFactModel.kind,
                        OrganisationFactModel.key,
                        OrganisationFactModel.value,
                        OrganisationFactModel.status,
                        OrganisationFactModel.times_seen,
                        WorkflowRunModel.workflow_id,
                    )
                    .outerjoin(
                        WorkflowRunModel,
                        OrganisationFactModel.source_run_id == WorkflowRunModel.id,
                    )
                    .where(
                        OrganisationFactModel.organization_id == organization_id,
                        OrganisationFactModel.subject_type == SUBJECT_ORGANISATION,
                        OrganisationFactModel.status != "rejected",
                    )
                )
            ).all()

            for row in memory:
                memory_id = node(
                    f"{row.kind}:{row.id}",
                    row.kind,
                    row.value,
                    key=row.key,
                    status=row.status,
                    times_seen=int(row.times_seen or 0),
                )
                edges.append(
                    {
                        "source": org_node,
                        "target": memory_id,
                        # A gap is the same relation seen from the other side,
                        # and naming it differently is what lets one picture
                        # show both halves of a question.
                        "relation": (
                            "knows" if row.kind == "fact" else "cannot answer"
                        ),
                    }
                )
                if row.workflow_id:
                    edges.append(
                        {
                            "source": f"agent:{row.workflow_id}",
                            "target": memory_id,
                            "relation": "learned",
                        }
                    )

            # Every agent, archived included. Their work happened and their
            # edges are as real as they were; dropping them would be the graph
            # agreeing that knowledge belongs to whoever was holding it.
            agents = (
                await session.execute(
                    select(
                        WorkflowModel.id,
                        WorkflowModel.name,
                        WorkflowModel.status,
                        WorkflowModel.is_live,
                    ).where(WorkflowModel.organization_id == organization_id)
                )
            ).all()
            for row in agents:
                agent_id = node(
                    f"agent:{row.id}",
                    "agent",
                    row.name,
                    archived=str(row.status) != "active",
                    live=bool(row.is_live),
                )
                edges.append(
                    {"source": org_node, "target": agent_id, "relation": "employs"}
                )

            # Which outside systems each agent actually reached, and whether it
            # worked. Counted, so a thick edge means a system the business
            # depends on rather than one it touched once.
            actions = (
                await session.execute(
                    select(
                        AppInteractionModel.workflow_id,
                        AppInteractionModel.app,
                        func.count(AppInteractionModel.id).label("uses"),
                        func.sum(
                            case((AppInteractionModel.status == "error", 1), else_=0)
                        ).label("errors"),
                    )
                    .where(
                        AppInteractionModel.organization_id == organization_id,
                        AppInteractionModel.created_at >= since,
                        AppInteractionModel.app.isnot(None),
                        AppInteractionModel.workflow_id.isnot(None),
                    )
                    .group_by(AppInteractionModel.workflow_id, AppInteractionModel.app)
                )
            ).all()
            for row in actions:
                app_id = node(f"app:{row.app}", "app", row.app)
                edges.append(
                    {
                        "source": f"agent:{row.workflow_id}",
                        "target": app_id,
                        "relation": "acts in",
                        "uses": int(row.uses or 0),
                        "errors": int(row.errors or 0),
                    }
                )

        # An edge whose endpoint was never built is dropped rather than left
        # dangling: an agent deleted before its facts were, or an app recorded
        # outside the window. A renderer handed a dangling edge either throws
        # or silently draws nothing, and both are worse than a smaller graph.
        known = set(nodes)
        return {
            "nodes": list(nodes.values()),
            "edges": [
                edge
                for edge in edges
                if edge["source"] in known and edge["target"] in known
            ],
        }
