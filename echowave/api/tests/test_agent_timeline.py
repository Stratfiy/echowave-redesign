"""One timeline every screen reads from.

A customer asked why their agent refused a free noon slot. The facts were all
recorded -- across `workflow_runs`, `app_interactions` and the container's
logs, at three different grains -- and answering took an SSH session and a
psql prompt. `agent_activity` is three separate queries for the same reason.

So this table is the narrative: one row per notable moment, carrying the
sentence a person reads, filterable four ways with no joins. The tests here
are mostly about the rules that keep it trustworthy rather than merely
present.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.db.models import AgentEventModel
from api.enums import AgentEventKind, AgentEventVisibility
from api.services.workflow import agent_timeline


class TestTheShape:
    def test_four_read_paths_are_each_indexed(self):
        """A screen per filter, and every one orders by time descending. An
        unindexed path re-sorts a clinic's whole history to show twenty rows."""
        names = {index.name for index in AgentEventModel.__table__.indexes}
        assert names == {
            "ix_agent_events_org_at",
            "ix_agent_events_workflow_at",
            "ix_agent_events_run_at",
            "ix_agent_events_folder_at",
            "ix_agent_events_org_deliverables",
        }

    def test_the_tenant_column_is_not_nullable(self):
        """An event with no organisation cannot be read back by anyone who
        should see it, and could be read by someone who should not."""
        assert AgentEventModel.__table__.c.organization_id.nullable is False

    @pytest.mark.parametrize(
        "column", ["workflow_id", "workflow_run_id", "folder_id", "definition_id"]
    )
    def test_everything_else_is_nullable(self, column):
        """A routine tick has no call, a credit top-up has no bot, a bot may be
        in no team. A schema demanding them pushes writers into inventing
        values, which is how a log stops being trustworthy."""
        assert AgentEventModel.__table__.c[column].nullable is True

    def test_kind_is_a_string_not_a_database_enum(self):
        """So a kind added next year needs no migration, and an unrecognised
        one read back from an older writer renders as itself."""
        from sqlalchemy import String

        assert isinstance(AgentEventModel.__table__.c.kind.type, String)


class TestWhatGetsWritten:
    async def _record(self, **overrides):
        args = {
            "organization_id": 42,
            "kind": AgentEventKind.OUTCOME_FILED.value,
            "summary": "Booked Mrs Lakshmi for Monday at noon",
            "workflow_id": 7,
        }
        args.update(overrides)
        with (
            patch.object(agent_timeline, "db_client") as db,
            patch.object(agent_timeline, "_folder_for", AsyncMock(return_value=3)),
        ):
            db.record_agent_event = AsyncMock()
            await agent_timeline.record(**args)
            return db.record_agent_event

    @pytest.mark.asyncio
    async def test_an_outcome_is_a_deliverable(self):
        """It is the thing the call existed for, so it is handed to somebody
        rather than merely logged."""
        written = await self._record()
        assert written.await_args.kwargs["is_deliverable"] is True

    @pytest.mark.asyncio
    async def test_a_credit_hold_is_not(self):
        written = await self._record(kind=AgentEventKind.CREDITS_HELD.value)
        assert written.await_args.kwargs["is_deliverable"] is False

    @pytest.mark.asyncio
    async def test_the_team_is_stored_rather_than_joined_later(self):
        """A bot moved to another team must not rewrite its own history. The
        row records which team it was working for when it happened."""
        written = await self._record()
        assert written.await_args.kwargs["folder_id"] == 3

    @pytest.mark.asyncio
    async def test_an_event_with_no_organisation_is_dropped(self):
        with patch.object(agent_timeline, "db_client") as db:
            db.record_agent_event = AsyncMock()
            await agent_timeline.record(
                organization_id=None,
                kind=AgentEventKind.CALL_ENDED.value,
                summary="Call ended",
            )
        db.record_agent_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_blank_summary_is_refused(self):
        """A blank line on a timeline reads as a broken screen rather than as
        nothing having happened."""
        with patch.object(agent_timeline, "db_client") as db:
            db.record_agent_event = AsyncMock()
            await agent_timeline.record(
                organization_id=42,
                kind=AgentEventKind.CALL_ENDED.value,
                summary="   ",
            )
        db.record_agent_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_database_that_will_not_write_never_ends_a_call(self):
        """The cost of this choice is a hole in a history. The cost of the
        alternative is a caller hearing the line go dead."""
        with (
            patch.object(agent_timeline, "db_client") as db,
            patch.object(agent_timeline, "_folder_for", AsyncMock(return_value=None)),
        ):
            db.record_agent_event = AsyncMock(side_effect=RuntimeError("on fire"))
            await agent_timeline.record(
                organization_id=42,
                kind=AgentEventKind.OUTCOME_FILED.value,
                summary="Booked Mrs Lakshmi",
                workflow_id=7,
            )  # must not raise


class TestAFailureIsAnEvent:
    """The defect this log exists to end is the silent one: an agent asked to
    confirm a booking by phone, unable to, saying nothing. A timeline that
    records only successes is the same lie in a new place."""

    async def _action(self, status, error=None):
        with (
            patch.object(agent_timeline, "db_client") as db,
            patch.object(agent_timeline, "_folder_for", AsyncMock(return_value=None)),
        ):
            db.record_agent_event = AsyncMock()
            await agent_timeline.record_action(
                organization_id=42,
                workflow_id=7,
                definition_id=2,
                workflow_run_id=99,
                name="book_appointment",
                app="Google Calendar",
                status=status,
                error=error,
            )
            return db.record_agent_event.await_args.kwargs

    @pytest.mark.asyncio
    async def test_a_failed_action_is_its_own_kind(self):
        written = await self._action("error", error="That time is already booked")
        assert written["kind"] == AgentEventKind.COULD_NOT.value
        assert written["is_deliverable"] is True

    @pytest.mark.asyncio
    async def test_a_successful_action_is_not_shouted_about(self):
        written = await self._action("success")
        assert written["kind"] == AgentEventKind.AGENT_ACTED.value
        assert written["is_deliverable"] is False

    @pytest.mark.asyncio
    async def test_the_error_travels_with_it(self):
        written = await self._action("error", error="That time is already booked")
        assert "already booked" in written["payload"]["error"]

    @pytest.mark.asyncio
    async def test_the_line_reads_as_what_happened_not_as_an_api_call(self):
        """The screen this feeds is read by somebody who has never seen an
        API."""
        written = await self._action("success")
        assert written["summary"] == "Book appointment in Google Calendar"
        failed = await self._action("error")
        assert failed["summary"] == (
            "Tried to book appointment in Google Calendar and could not"
        )


class TestVisibilityIsCompliance:
    """A clinic's transcripts carry patient information and the caller was told
    what the recording was for. The middle tier is not a tidiness setting."""

    def test_what_the_caller_said_is_hidden_until_asked_for(self):
        assert (
            agent_timeline.default_visibility(AgentEventKind.CALLER_WANTED.value)
            == AgentEventVisibility.ON_REQUEST.value
        )

    def test_the_businesss_own_record_is_shown(self):
        for kind in (
            AgentEventKind.OUTCOME_FILED.value,
            AgentEventKind.CREDITS_SETTLED.value,
            AgentEventKind.COULD_NOT.value,
        ):
            assert (
                agent_timeline.default_visibility(kind)
                == AgentEventVisibility.ALWAYS.value
            ), kind

    def test_the_deliverable_list_is_an_allowlist_and_that_is_deliberate(self):
        """This codebase normally warns against allowlists. Here the failure
        direction is the safe one: wrongly excluded is a card that renders as
        a line, which somebody notices; wrongly included would put a recording
        in a list somebody screenshots."""
        assert (
            AgentEventKind.CALLER_WANTED.value not in agent_timeline.DELIVERABLE_KINDS
        )
        assert AgentEventKind.OUTCOME_FILED.value in agent_timeline.DELIVERABLE_KINDS


class TestReading:
    """The real query, captured at the session boundary.

    Compiled rather than run -- there is no Postgres here -- but it is
    `AgentEventClient.agent_events` doing the building. An earlier version of
    this class rebuilt the query itself and therefore proved nothing about the
    code that ships.
    """

    def _query(self, **kwargs):
        import asyncio

        from sqlalchemy.dialects import postgresql

        from api.db.agent_event_client import AgentEventClient

        captured = {}

        class _Result:
            def scalars(self):
                return SimpleNamespace(all=lambda: [])

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, query):
                captured["query"] = query
                return _Result()

        class _Client(AgentEventClient):
            def __init__(self):
                pass

            def async_session(self):
                return _Session()

        asyncio.run(_Client().agent_events(organization_id=42, **kwargs))
        return str(
            captured["query"].compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )

    def test_one_call_reads_forwards(self):
        """A call is a story. Descending would show it ending before it
        began."""
        sql = self._query(workflow_run_id=9)
        assert "at ASC" in sql
        assert "at DESC" not in sql

    def test_a_history_reads_backwards(self):
        sql = self._query()
        assert "at DESC" in sql

    def test_every_read_is_tenant_scoped(self):
        assert "organization_id = 42" in self._query()

    def test_on_request_rows_are_excluded_unless_asked_for(self):
        assert "on_request" not in self._query()
        assert "on_request" in self._query(include_on_request=True)

    def test_off_is_never_returned(self):
        """A suppression somebody can opt past is not a suppression, so no
        argument unhides it."""
        for kwargs in ({}, {"include_on_request": True}, {"deliverables_only": True}):
            assert AgentEventVisibility.OFF.value not in self._query(**kwargs), kwargs

    def test_paging_uses_the_id_not_the_timestamp(self):
        """Two events in the same millisecond are ordinary on a busy call, and
        a timestamp cursor would either skip one or return it twice."""
        sql = self._query(before_id=5000)
        assert "agent_events.id < 5000" in sql

    def test_the_limit_is_capped(self):
        """A caller asking for a million rows gets five hundred, not a
        timeout."""
        assert "LIMIT 500" in self._query(limit=1_000_000)
        assert "LIMIT 1" in self._query(limit=0)


class TestItFillsItself:
    """Wired at the one choke point every tool action already passes through,
    so a tool kind added next year gets a timeline entry without anybody
    thinking about it."""

    @pytest.mark.asyncio
    async def test_recording_an_interaction_also_writes_the_line(self):
        from api.services.workflow import app_interactions

        with (
            patch.object(app_interactions, "record", AsyncMock()) as interaction,
            patch.object(
                app_interactions.agent_timeline, "record_action", AsyncMock()
            ) as line,
        ):
            await app_interactions._safe_record(
                AsyncMock(
                    return_value={
                        "organization_id": 42,
                        "workflow_id": 7,
                        "definition_id": 2,
                        "workflow_run_id": 99,
                    }
                ),
                kind="google_calendar",
                app="googlecalendar",
                name="book_appointment",
                status="success",
                error=None,
                duration_ms=120,
            )

        interaction.assert_awaited_once()
        line.assert_awaited_once()
        assert line.await_args.kwargs["name"] == "book_appointment"

    @pytest.mark.asyncio
    async def test_a_broken_timeline_does_not_cost_us_the_support_row(self):
        from api.services.workflow import app_interactions

        with (
            patch.object(app_interactions, "record", AsyncMock()) as interaction,
            patch.object(
                app_interactions.agent_timeline,
                "record_action",
                AsyncMock(side_effect=RuntimeError("on fire")),
            ),
        ):
            await app_interactions._safe_record(
                AsyncMock(return_value={"organization_id": 42}),
                kind="http_api",
                app=None,
                name="ping",
                status="success",
                error=None,
                duration_ms=1,
            )  # must not raise

        interaction.assert_awaited_once()
