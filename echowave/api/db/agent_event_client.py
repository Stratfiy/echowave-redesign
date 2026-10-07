"""Reads and writes for the timeline every screen reads from."""

from datetime import UTC, datetime
from typing import Any, Optional, Sequence

from sqlalchemy import func, or_, select, tuple_, update

from api.db.base_client import BaseDBClient
from api.db.models import AgentEventModel
from api.enums import AgentEventActor, AgentEventKind, AgentEventVisibility

#: The summary column's width. Truncated rather than refused: a sentence a
#: little too long is a row that still reads, and a rejected insert is a hole
#: in a history somebody may rely on.
MAX_SUMMARY = 500


class AgentEventClient(BaseDBClient):
    async def record_agent_event(
        self,
        *,
        organization_id: int,
        kind: str,
        actor: str,
        summary: str,
        workflow_id: Optional[int] = None,
        definition_id: Optional[int] = None,
        workflow_run_id: Optional[int] = None,
        folder_id: Optional[int] = None,
        payload: Optional[dict[str, Any]] = None,
        is_deliverable: bool = False,
        visibility: str = AgentEventVisibility.ALWAYS.value,
        at: Optional[datetime] = None,
        thread_id: Optional[str] = None,
    ) -> Optional[int]:
        """Append one event. Returns its id.

        The id, and deliberately not the model -- that was the original
        choice, made because returning a row invites somebody to await this
        mid-conversation and use its relationships. An integer invites
        nothing, and something outside this system now needs to name the
        event it was told about: a webhook receiver has to be able to dedupe
        a retried delivery, and "the event this is about" is the only key
        that survives a retry.
        """
        async with self.async_session() as session:
            row = AgentEventModel(
                organization_id=organization_id,
                workflow_id=workflow_id,
                definition_id=definition_id,
                workflow_run_id=workflow_run_id,
                folder_id=folder_id,
                at=at or datetime.now(UTC),
                kind=kind,
                actor=actor,
                summary=(summary or "")[:MAX_SUMMARY],
                payload=payload or {},
                is_deliverable=is_deliverable,
                visibility=visibility,
                thread_id=thread_id,
            )
            session.add(row)
            # Read the id before committing: the session expires its rows on
            # commit, and touching row.id afterwards is a lazy load outside
            # the greenlet -- it raised MissingGreenlet on every write, so
            # every caller lost the id it was promised (and the work hung on
            # it: action cards, inbox notices, outbound event webhooks).
            await session.flush()
            event_id = int(row.id) if row.id is not None else None
            await session.commit()
            return event_id

    async def get_agent_event(
        self, event_id: int, *, organization_id: int
    ) -> Optional[AgentEventModel]:
        """One row, or None. Scoped to the tenant: an id somebody typed is not
        enough to read another account's timeline."""
        async with self.async_session() as session:
            return await session.scalar(
                select(AgentEventModel).where(
                    AgentEventModel.id == event_id,
                    AgentEventModel.organization_id == organization_id,
                )
            )

    async def set_agent_event_payload(
        self, event_id: int, *, organization_id: int, payload: dict[str, Any]
    ) -> bool:
        """Replace one row's payload. The one write to a row after it is
        appended, and it exists for a single reason: a decision is answered
        on the card that asked, and a second row would leave the card that
        people see saying "waiting" forever."""
        async with self.async_session() as session:
            result = await session.execute(
                update(AgentEventModel)
                .where(
                    AgentEventModel.id == event_id,
                    AgentEventModel.organization_id == organization_id,
                )
                .values(payload=payload)
            )
            await session.commit()
            return result.rowcount == 1

    async def transition_agent_event_payload(
        self,
        event_id: int,
        *,
        organization_id: int,
        from_state: str,
        payload: dict[str, Any],
    ) -> bool:
        """Replace one row's payload only if its ``state`` is still
        ``from_state``. False means somebody else moved it first.

        A compare-and-swap in one statement: two presses of Confirm, or a
        retried job, read the same state, and only the first write may win.
        A payload with no state yet is a fresh proposal."""
        current = func.coalesce(
            AgentEventModel.payload["state"].as_string(), "proposed"
        )
        async with self.async_session() as session:
            result = await session.execute(
                update(AgentEventModel)
                .where(
                    AgentEventModel.id == event_id,
                    AgentEventModel.organization_id == organization_id,
                    current == from_state,
                )
                .values(payload=payload)
            )
            await session.commit()
            return result.rowcount == 1

    async def agent_events(
        self,
        *,
        organization_id: int,
        workflow_id: Optional[int] = None,
        workflow_run_id: Optional[int] = None,
        folder_id: Optional[int] = None,
        kinds: Optional[Sequence[str]] = None,
        deliverables_only: bool = False,
        include_on_request: bool = False,
        limit: int = 200,
        before_at: Optional[datetime] = None,
        before_id: Optional[int] = None,
        after_id: Optional[int] = None,
        assistant_thread: bool = False,
        thread_id: Optional[str] = None,
        viewer_id: Optional[int] = None,
    ) -> list[AgentEventModel]:
        """The timeline, newest first.

        ``viewer_id`` is who is reading. A row whose payload names a
        ``private_to`` person (stream `reach`: a connect chip, a price
        comparison, an order or outside-tool card and the lines under it) is
        returned only to that person, and to no caller that names nobody --
        deny by default, so a reader added later cannot show one person's
        connections to a colleague by forgetting an argument.

        ``assistant_thread`` is Decibyl's own conversation: rows with neither
        a workflow nor a folder. Nothing else in the table has both empty
        with the ``message`` kind, so the thread is a filter, not a table.

        One method for all four screens. The filters narrow; none of them is
        required beyond the organisation, which is not optional -- an unscoped
        read here would hand one tenant another's calls.

        ``include_on_request`` is off by default, and that default is the
        point. Recordings, transcripts and caller details are
        ``ON_REQUEST``: a caller was told what the recording was for, and a
        screen that shows them because nobody thought about it is a screen
        that broke that. A caller passes it deliberately, having checked the
        organisation's consent settings.

        ``OFF`` is never returned, whatever is passed. There is no argument
        that unhides it, because a suppression somebody can opt past is not a
        suppression.

        Ascending when reading one call, descending everywhere else. A call is
        a story and reads forwards; a history is a feed and reads backwards.
        Getting this wrong would show a conversation ending before it began.
        """
        allowed = [AgentEventVisibility.ALWAYS.value]
        if include_on_request:
            allowed.append(AgentEventVisibility.ON_REQUEST.value)

        private_to = AgentEventModel.payload["private_to"].as_string()
        query = select(AgentEventModel).where(
            AgentEventModel.organization_id == organization_id,
            AgentEventModel.visibility.in_(allowed),
            or_(private_to.is_(None), private_to == str(viewer_id))
            if viewer_id is not None
            else private_to.is_(None),
        )

        if workflow_id is not None:
            query = query.where(AgentEventModel.workflow_id == workflow_id)
        if workflow_run_id is not None:
            query = query.where(AgentEventModel.workflow_run_id == workflow_run_id)
        if folder_id is not None:
            query = query.where(AgentEventModel.folder_id == folder_id)
        if assistant_thread:
            query = query.where(
                AgentEventModel.workflow_id.is_(None),
                AgentEventModel.folder_id.is_(None),
            )
            # Which conversation. NULL is the one the account has always had,
            # so a caller that names no thread reads exactly what it read
            # before threads existed -- and a caller that names one gets that
            # one and nothing else.
            query = query.where(
                AgentEventModel.thread_id.is_(None)
                if thread_id is None
                else AgentEventModel.thread_id == thread_id
            )
        if kinds:
            query = query.where(AgentEventModel.kind.in_(list(kinds)))
        if deliverables_only:
            query = query.where(AgentEventModel.is_deliverable.is_(True))
        if after_id is not None:
            # A watermark, not a cursor. The channel-context fold covers every
            # row with id <= watermark by construction, so filtering on id
            # alone is exact here even though the rows sort by (at, id) -- the
            # predicate/sort mismatch the cursor note below warns about only
            # bites when the same value is used to page, and this never pages.
            query = query.where(AgentEventModel.id > after_id)

        reading_one_call = workflow_run_id is not None
        if reading_one_call:
            query = query.order_by(AgentEventModel.at.asc(), AgentEventModel.id.asc())
        else:
            # The cursor compares the SAME pair the rows are ordered by, and
            # that is the whole point.
            #
            # It used to filter on `id < before_id` alone while ordering by
            # `at DESC, id DESC`, on the reasoning that two events in the same
            # millisecond are ordinary and an `at` cursor would skip or repeat
            # one. True as far as it goes, and it made the predicate disagree
            # with the sort.
            #
            # They disagree whenever insert order and timestamp order differ,
            # which two workers manage without trying: A reads now() at
            # .100 and B at .050, B commits first and takes the lower id. Order
            # by `at` puts A first; a page ending on A then filters
            # `id < A.id`, and B -- which belongs on the NEXT page -- is
            # excluded from that page and from every page after it, because
            # each one filters by a smaller id still.
            #
            # No error, no log, no gap anybody can see: a row that simply never
            # appears in a history somebody may rely on in a dispute. The
            # silent-absence failure api/AGENTS.md is about, in the one place
            # that can least afford it.
            #
            # A row-value comparison is exact and still uses the index the
            # ORDER BY wants, so the fix costs nothing.
            if before_at is not None and before_id is not None:
                query = query.where(
                    tuple_(AgentEventModel.at, AgentEventModel.id)
                    < tuple_(before_at, before_id)
                )
            query = query.order_by(AgentEventModel.at.desc(), AgentEventModel.id.desc())

        async with self.async_session() as session:
            result = await session.execute(query.limit(max(1, min(limit, 500))))
            return list(result.scalars().all())

    async def thread_author(
        self, *, organization_id: int, thread_id: Optional[str]
    ) -> Optional[int]:
        """Who started a Decibyl conversation: the ``author_id`` on its first
        human line, or None when no line carries one (the conversation every
        account had before threads, or one written before the stamp)."""
        query = (
            select(AgentEventModel.payload)
            .where(
                AgentEventModel.organization_id == organization_id,
                AgentEventModel.workflow_id.is_(None),
                AgentEventModel.folder_id.is_(None),
                AgentEventModel.kind == AgentEventKind.MESSAGE.value,
                AgentEventModel.actor == AgentEventActor.HUMAN.value,
                AgentEventModel.thread_id.is_(None)
                if thread_id is None
                else AgentEventModel.thread_id == thread_id,
            )
            .order_by(AgentEventModel.id.asc())
            .limit(1)
        )
        async with self.async_session() as session:
            payload = await session.scalar(query)
        try:
            value = (payload or {}).get("author_id")
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    async def assistant_threads(
        self,
        *,
        organization_id: int,
        limit: int = 50,
        viewer_id: Optional[int] = None,
        viewer_is_admin: bool = False,
    ) -> list[dict[str, Any]]:
        """Decibyl's conversations, most recently spoken in first.

        A thread is not a row anywhere -- it is the id a set of events share
        -- so the list is derived from the events themselves. That is the
        whole reason there is no create-thread call: a new chat exists the
        moment its first message is written, and an id nobody ever wrote to
        is not a conversation anybody would want listed.

        The NULL group is the thread every account has always had. It is a
        row in this list like any other, because "older chats" that cannot
        show the only chat that existed before threads would be a list that
        hides the one conversation everybody has.

        Messages only. An activity line and a proposed card belong to a turn
        that began with somebody speaking, so counting them would make
        "4 messages" mean something nobody said, and taking a title from one
        would head a chat with "Read 3 passages". The first message is the
        title because it is what the person came in asking for.

        Two queries, not a window function. The aggregate gives the group and
        the id of its first message; the second reads those rows for a title.
        A window over the whole table to carry one summary through would scan
        rows this cannot use.

        ``viewer_id`` narrows the list to the viewer's own conversations when
        private threads are on (D-1b): a thread is the person's who wrote its
        first line, and one with no author on record is an Admin's to see.
        None lists everything, which is what the flag being off means.
        """
        from sqlalchemy import func

        grouped = (
            select(
                AgentEventModel.thread_id.label("thread_id"),
                func.max(AgentEventModel.at).label("last_at"),
                func.count().label("messages"),
                func.min(AgentEventModel.id).label("first_id"),
            )
            .where(
                AgentEventModel.organization_id == organization_id,
                AgentEventModel.workflow_id.is_(None),
                AgentEventModel.folder_id.is_(None),
                AgentEventModel.kind == AgentEventKind.MESSAGE.value,
                AgentEventModel.visibility == AgentEventVisibility.ALWAYS.value,
            )
            .group_by(AgentEventModel.thread_id)
            .order_by(func.max(AgentEventModel.at).desc())
            .limit(max(1, min(limit, 200)))
        )

        async with self.async_session() as session:
            rows = list((await session.execute(grouped)).all())
            first_ids = [r.first_id for r in rows if r.first_id is not None]
            titles: dict[int, str] = {}
            authors: dict[int, Optional[int]] = {}
            if first_ids:
                opening = await session.execute(
                    select(
                        AgentEventModel.id,
                        AgentEventModel.summary,
                        AgentEventModel.payload,
                    ).where(AgentEventModel.id.in_(first_ids))
                )
                for i, t, payload in opening.all():
                    titles[i] = t or ""
                    try:
                        raw = (payload or {}).get("author_id")
                        authors[i] = int(raw) if raw is not None else None
                    except (TypeError, ValueError):
                        authors[i] = None

        def visible(first_id: Optional[int]) -> bool:
            if viewer_id is None:
                return True
            author = authors.get(first_id) if first_id is not None else None
            return author == viewer_id if author is not None else viewer_is_admin

        return [
            {
                "thread_id": r.thread_id,
                "last_at": r.last_at,
                "messages": int(r.messages or 0),
                "title": titles.get(r.first_id, ""),
            }
            for r in rows
            if visible(r.first_id)
        ]

    async def recent_agent_conversations(
        self, *, organization_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        """The agents somebody has been talking to, most recent first.

        Messages only, and ``ALWAYS`` only: what was *said* in an agent's
        chat, not its activity lines, so an agent that merely ran a routine
        is not a conversation. One row per agent with its latest message.
        """
        from sqlalchemy import func

        ranked = (
            select(
                AgentEventModel.workflow_id.label("workflow_id"),
                AgentEventModel.summary.label("summary"),
                AgentEventModel.at.label("at"),
                func.row_number()
                .over(
                    partition_by=AgentEventModel.workflow_id,
                    order_by=(AgentEventModel.at.desc(), AgentEventModel.id.desc()),
                )
                .label("rank"),
            )
            .where(
                AgentEventModel.organization_id == organization_id,
                AgentEventModel.workflow_id.is_not(None),
                AgentEventModel.kind == AgentEventKind.MESSAGE.value,
                AgentEventModel.visibility == AgentEventVisibility.ALWAYS.value,
            )
            .subquery()
        )
        query = (
            select(ranked)
            .where(ranked.c.rank == 1)
            .order_by(ranked.c.at.desc())
            .limit(max(1, min(limit, 100)))
        )
        async with self.async_session() as session:
            rows = (await session.execute(query)).all()
        return [
            {"workflow_id": r.workflow_id, "summary": r.summary or "", "at": r.at}
            for r in rows
        ]

    async def latest_event_per_workflow(
        self, *, organization_id: int, workflow_ids: Sequence[int]
    ) -> dict[int, dict[str, Any]]:
        """The most recent event for each of these bots, for the sidebar.

        One query with a window function rather than one per bot: the sidebar
        renders every bot an account has, and a query per row is the N+1 that
        only shows up once somebody has enough bots to notice -- the same
        mistake ``get_all_workflows_for_listing`` carries a comment about.

        Returns plain dicts rather than models. The caller wants a line and a
        time to render beside a name, and handing back detached ORM instances
        built from a window query invites somebody to mutate one and wonder why
        nothing saved.

        ``ALWAYS`` only. A sidebar preview is glanceable by definition, and a
        transcript line is not something to put where somebody's colleague can
        read it over their shoulder.
        """
        from sqlalchemy import func

        wanted = [int(w) for w in workflow_ids if w]
        if not wanted:
            return {}

        ranked = (
            select(
                AgentEventModel.workflow_id.label("workflow_id"),
                AgentEventModel.kind.label("kind"),
                AgentEventModel.actor.label("actor"),
                AgentEventModel.summary.label("summary"),
                AgentEventModel.at.label("at"),
                func.row_number()
                .over(
                    partition_by=AgentEventModel.workflow_id,
                    order_by=(AgentEventModel.at.desc(), AgentEventModel.id.desc()),
                )
                .label("rank"),
            )
            .where(
                AgentEventModel.organization_id == organization_id,
                AgentEventModel.workflow_id.in_(wanted),
                AgentEventModel.visibility == AgentEventVisibility.ALWAYS.value,
            )
            .subquery()
        )

        async with self.async_session() as session:
            rows = (
                await session.execute(select(ranked).where(ranked.c.rank == 1))
            ).all()

        return {
            row.workflow_id: {
                "kind": row.kind,
                "actor": row.actor,
                "summary": row.summary,
                "at": row.at,
            }
            for row in rows
            if row.workflow_id is not None
        }
