"""Reads and writes for the timeline every screen reads from."""

from datetime import UTC, datetime
from typing import Any, Optional, Sequence

from sqlalchemy import select

from api.db.base_client import BaseDBClient
from api.db.models import AgentEventModel
from api.enums import AgentEventVisibility

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
    ) -> None:
        """Append one event.

        Returns nothing, the same choice ``create_app_interaction`` makes and
        for the same reason: the caller is usually a live call that has already
        said its sentence to the caller, there is no id it could use, and
        returning a model would invite somebody to await this mid-conversation.
        """
        async with self.async_session() as session:
            session.add(
                AgentEventModel(
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
                )
            )
            await session.commit()

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
        before_id: Optional[int] = None,
    ) -> list[AgentEventModel]:
        """The timeline, newest first.

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

        query = select(AgentEventModel).where(
            AgentEventModel.organization_id == organization_id,
            AgentEventModel.visibility.in_(allowed),
        )

        if workflow_id is not None:
            query = query.where(AgentEventModel.workflow_id == workflow_id)
        if workflow_run_id is not None:
            query = query.where(AgentEventModel.workflow_run_id == workflow_run_id)
        if folder_id is not None:
            query = query.where(AgentEventModel.folder_id == folder_id)
        if kinds:
            query = query.where(AgentEventModel.kind.in_(list(kinds)))
        if deliverables_only:
            query = query.where(AgentEventModel.is_deliverable.is_(True))

        reading_one_call = workflow_run_id is not None
        if reading_one_call:
            query = query.order_by(AgentEventModel.at.asc(), AgentEventModel.id.asc())
        else:
            # Paged by id rather than by timestamp: two events in the same
            # millisecond are ordinary on a busy call, and a timestamp cursor
            # would either skip one or return it twice.
            if before_id is not None:
                query = query.where(AgentEventModel.id < before_id)
            query = query.order_by(AgentEventModel.at.desc(), AgentEventModel.id.desc())

        async with self.async_session() as session:
            result = await session.execute(query.limit(max(1, min(limit, 500))))
            return list(result.scalars().all())

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
