"""Reads and writes for what an organization's agents have learned."""

import re
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db.base_client import BaseDBClient
from api.db.models import OrganisationFactModel

#: The subject facts about the business itself hang off, as opposed to facts
#: about one of its customers.
SUBJECT_ORGANISATION = "organisation"

#: ``workflow_id IS NULL`` -- the organisation's own memory, which every bot
#: reads. The predicate is spelled the same way as the partial unique index it
#: infers against, and with no bound parameter, so Postgres either matches the
#: index or refuses the statement. A near-miss here would write duplicate rows
#: rather than raise, which is the one failure this table must not have.
ORG_SCOPE = OrganisationFactModel.workflow_id.is_(None)
#: ``workflow_id IS NOT NULL`` -- one bot's own memory.
BOT_SCOPE = OrganisationFactModel.workflow_id.isnot(None)


def _scope(workflow_id: Optional[int]):
    """The conflict target for a write at this scope.

    Two partial unique indexes, so an upsert has to say which one it means.
    Returned together because getting the pair out of step is the way to write
    a row that collides with nothing.
    """
    if workflow_id is None:
        return (
            [
                OrganisationFactModel.organization_id,
                OrganisationFactModel.subject_type,
                OrganisationFactModel.subject_key,
                OrganisationFactModel.key,
            ],
            ORG_SCOPE,
        )
    return (
        [
            OrganisationFactModel.organization_id,
            OrganisationFactModel.workflow_id,
            OrganisationFactModel.subject_type,
            OrganisationFactModel.subject_key,
            OrganisationFactModel.key,
        ],
        BOT_SCOPE,
    )


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
        workflow_id: Optional[int] = None,
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

        ``workflow_id`` defaults to None, which is the organisation's memory --
        what a call learns about a caller belongs to the business, not to
        whichever bot happened to answer. Pass a workflow id only for something
        true of that bot alone.
        """
        if not facts:
            return 0

        now = datetime.now(UTC)
        rows = [
            {
                "organization_id": organization_id,
                "workflow_id": workflow_id,
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

        index_elements, index_where = _scope(workflow_id)
        statement = pg_insert(OrganisationFactModel).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=index_elements,
            index_where=index_where,
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
        self,
        *,
        organization_id: int,
        subject_type: str,
        subject_key: str,
        workflow_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Everything known about one subject, as a flat mapping.

        With a ``workflow_id`` this is the organisation's memory UNION that
        bot's own, and the bot's wins on a shared key. That direction is the
        whole point of the scope: an organisation fact is the default every bot
        inherits, and a bot fact is that bot having been told otherwise. If the
        organisation's answer won, the bot's own memory could never say
        anything, and the column would be decoration.

        Without one it is the organisation's memory alone -- which is what
        every existing caller gets, unchanged.
        """
        scope = (
            ORG_SCOPE
            if workflow_id is None
            else or_(ORG_SCOPE, OrganisationFactModel.workflow_id == workflow_id)
        )
        async with self.async_session() as session:
            result = await session.execute(
                select(OrganisationFactModel)
                .where(
                    OrganisationFactModel.organization_id == organization_id,
                    OrganisationFactModel.subject_type == subject_type,
                    OrganisationFactModel.subject_key == subject_key,
                    scope,
                )
                # The bot's rows land last and overwrite the organisation's in
                # the dict comprehension below. Ordering is load-bearing, which
                # is why it is not left to whatever the planner returns.
                .order_by(OrganisationFactModel.workflow_id.is_(None).desc())
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
                    # A gap is something the BUSINESS cannot answer. Scoping it
                    # to the bot that ran into it would hide from every other
                    # bot the one question worth answering.
                    "workflow_id": None,
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

        index_elements, index_where = _scope(None)
        statement = pg_insert(OrganisationFactModel).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=index_elements,
            index_where=index_where,
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
        workflow_id: Optional[int] = None,
    ) -> int:
        """What the business told us about itself -- or about one of its bots.

        Confirmed by default, because the path into here is a person answering
        a question on the onboarding form or typing it into the chat. That is
        the same person who would confirm it afterwards, so asking twice would
        be ceremony.

        With a ``workflow_id`` the same write becomes that bot's own standing
        instruction: "read the morning numbers in Hindi" is true of this bot
        and false of the one answering the phone, and storing it on the
        organisation would put it in both their mouths.
        """
        if not facts:
            return 0

        now = datetime.now(UTC)
        rows = [
            {
                "organization_id": organization_id,
                "workflow_id": workflow_id,
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

        index_elements, index_where = _scope(workflow_id)
        statement = pg_insert(OrganisationFactModel).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=index_elements,
            index_where=index_where,
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
        workflow_id: Optional[int] = None,
        include_bots: bool = False,
        limit: int = 200,
    ) -> list[Any]:
        """What this business knows and what it still cannot answer.

        Most-seen first: the question forty callers asked is the one worth
        reading, and burying it under thirty-nine one-offs would make the
        screen useless at exactly the size where it starts to matter.

        Three scopes, and the caller has to mean one of them:

        * neither argument -- the organisation's own memory, which is what
          every caller before bot scope existed was already getting.
        * ``workflow_id`` -- the organisation's plus that bot's, the union a
          bot's own screen shows.
        * ``include_bots`` -- everything, for the screen that has to be able to
          review a bot fact somebody wants to correct. Rows carry
          ``workflow_id``, so that screen can say whose each one is rather than
          presenting a bot's private instruction as the business's position.

        Deliberately no scope that hides bot facts from every screen. A fact
        nobody can find is a fact nobody can correct, and it would still be in
        a prompt.
        """
        if include_bots:
            scope = None
        elif workflow_id is None:
            scope = ORG_SCOPE
        else:
            scope = or_(ORG_SCOPE, OrganisationFactModel.workflow_id == workflow_id)

        async with self.async_session() as session:
            query = select(OrganisationFactModel).where(
                OrganisationFactModel.organization_id == organization_id,
                OrganisationFactModel.subject_type == SUBJECT_ORGANISATION,
            )
            if scope is not None:
                query = query.where(scope)
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
