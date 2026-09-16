"""Connecting an app makes the rows a bot can actually call.

The founder connected Gmail. The Integrations screen said Connected and the
catalogue advertised nine tools. The bot could not use any of them, because
the ``tools`` rows the engine and Decibyl read were created only when
somebody expanded that screen's "9 tools" disclosure -- a step nothing asks
for and nobody knows about.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.routes import connectors
from api.services.workflow import connected_tools
from api.tasks import connector_tools


class TestTheRowsAreQueuedWhenTheyAreMissing:
    @pytest.mark.asyncio
    async def test_a_connected_app_with_no_rows_is_queued(self):
        with (
            patch.object(
                connectors.tool_sync,
                "toolkits_with_rows",
                AsyncMock(return_value=set()),
            ),
            patch.object(connectors, "enqueue_job", AsyncMock()) as queued,
        ):
            await connectors._make_any_missing_tool_rows(7, {"GMAIL"}, 3)
        assert queued.await_count == 1
        assert queued.await_args.kwargs["apps"] == ["gmail"]
        assert queued.await_args.kwargs["organization_id"] == 7
        assert queued.await_args.kwargs["user_id"] == 3

    @pytest.mark.asyncio
    async def test_the_slug_is_folded_to_the_case_the_rows_are_stored_in(self):
        # connected_toolkits() hands back upper-case; the rows are lower-case.
        # Comparing them raw would queue a sync on every single read forever.
        with (
            patch.object(
                connectors.tool_sync,
                "toolkits_with_rows",
                AsyncMock(return_value={"gmail"}),
            ),
            patch.object(connectors, "enqueue_job", AsyncMock()) as queued,
        ):
            await connectors._make_any_missing_tool_rows(7, {"GMAIL"}, 3)
        assert queued.await_count == 0

    @pytest.mark.asyncio
    async def test_nothing_connected_queues_nothing(self):
        with patch.object(connectors, "enqueue_job", AsyncMock()) as queued:
            await connectors._make_any_missing_tool_rows(7, set(), 3)
        assert queued.await_count == 0

    @pytest.mark.asyncio
    async def test_a_failure_here_never_breaks_the_screen(self):
        # The screen is still correct about what is connected, and the
        # disclosure still makes the rows on demand.
        with patch.object(
            connectors.tool_sync,
            "toolkits_with_rows",
            AsyncMock(side_effect=RuntimeError("database is having a day")),
        ):
            await connectors._make_any_missing_tool_rows(7, {"GMAIL"}, 3)


class TestTheJobMakesTheRows:
    @pytest.mark.asyncio
    async def test_it_creates_the_rows_as_the_user_who_confirms_the_account(self):
        user = MagicMock(id=3, selected_organization_id=7)
        synced = MagicMock(created=4, error=None)
        with (
            patch.object(
                connector_tools.db_client,
                "get_user_by_id",
                AsyncMock(return_value=user),
            ),
            patch.object(
                connector_tools, "toolkit_name", AsyncMock(return_value="Gmail")
            ),
            patch.object(
                connector_tools.tool_sync,
                "ensure_tools",
                AsyncMock(return_value=synced),
            ) as ensure,
        ):
            result = await connector_tools.sync_missing_tools(
                organization_id=7, apps=["gmail"], user_id=3
            )
        assert result == {"created": 4, "failed": 0}
        assert ensure.await_args.kwargs["actor"] is user

    @pytest.mark.asyncio
    async def test_nobody_in_the_organisation_means_nothing_is_created(self):
        """The caller switched organisation and no member confirms this one.

        Previously this asserted that a switched-away caller alone stopped
        the sync. It no longer does -- the rows belong to the account, not
        to whoever triggered it, so a member is used instead. The invariant
        that actually matters is unchanged and asserted here: rows are never
        created as somebody outside the organisation, because they would be
        invisible here and wrong there.
        """
        gone = MagicMock(id=3, selected_organization_id=99)
        with (
            patch.object(
                connector_tools.db_client,
                "get_user_by_id",
                AsyncMock(return_value=gone),
            ),
            patch.object(
                connector_tools.db_client,
                "get_organization_users",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                connector_tools.tool_sync, "ensure_tools", AsyncMock()
            ) as ensure,
        ):
            result = await connector_tools.sync_missing_tools(
                organization_id=7, apps=["gmail"], user_id=3
            )
        assert result == {"created": 0, "failed": 0}
        assert ensure.await_count == 0

    @pytest.mark.asyncio
    async def test_one_app_failing_does_not_stop_the_others(self):
        user = MagicMock(id=3, selected_organization_id=7)
        outcomes = [
            MagicMock(created=0, error="Could not read what Gmail can do just now."),
            MagicMock(created=2, error=None),
        ]
        with (
            patch.object(
                connector_tools.db_client,
                "get_user_by_id",
                AsyncMock(return_value=user),
            ),
            patch.object(
                connector_tools, "toolkit_name", AsyncMock(return_value="An app")
            ),
            patch.object(
                connector_tools.tool_sync,
                "ensure_tools",
                AsyncMock(side_effect=outcomes),
            ),
        ):
            result = await connector_tools.sync_missing_tools(
                organization_id=7, apps=["gmail", "googlesheets"], user_id=3
            )
        assert result == {"created": 2, "failed": 1}


class TestAConnectedAppWithNoToolsIsNamedNotContradicted:
    """Decibyl told the founder, in one message, that Gmail was "already
    connected" and that the workspace had "no apps connected" -- then picked
    one to act on. Both were true of their own source: the connect-card path
    asks the vendor, `apps_block` counts tool rows. Between connecting an app
    and its rows existing, they disagree."""

    def test_the_gap_is_stated_rather_than_left_to_be_inferred(self):
        said = connected_tools.apps_block([], awaiting=["gmail"])
        assert "still being set up" in said
        assert "not a conflict" in said
        # The sentence that started it.
        assert "No apps connected." not in said

    def test_the_model_is_told_not_to_offer_a_card_for_it(self):
        # Offering "connect Gmail" to somebody who just connected Gmail is
        # the same contradiction wearing a button.
        said = connected_tools.apps_block([], awaiting=["gmail"])
        assert "already connected" in said

    def test_an_account_with_no_gap_reads_exactly_as_before(self):
        assert connected_tools.apps_block(
            [], awaiting=[]
        ) == connected_tools.apps_block([])
        assert connected_tools.apps_block([]).startswith("No apps connected.")

    def test_the_gap_is_named_alongside_apps_that_do_work(self):
        tool = MagicMock()
        with (
            patch.object(connected_tools, "toolkit_of", return_value="slack"),
            patch.object(connected_tools, "is_read", return_value=True),
            # The block names its tools now; this test is about the gap.
            patch.object(
                connected_tools, "function_name", return_value="app_slack_send_message"
            ),
        ):
            said = connected_tools.apps_block([tool], awaiting=["gmail"])
        assert "Connected: slack" in said
        assert "gmail" in said and "still being set up" in said

    def test_plural_reads_as_plural(self):
        said = connected_tools.apps_block([], awaiting=["gmail", "slack"])
        assert "are connected but" in said


class TestTheRowsAreQueuedFromTheChatToo:
    """Hooking the sync only to the Integrations screen was the hole in the
    first fix: somebody who connects an app and goes straight back to the
    chat never opens that screen."""

    @pytest.mark.asyncio
    async def test_noticing_the_gap_queues_the_rows(self):
        with (
            patch(
                "api.services.integrations.composio.client.connected_toolkits",
                AsyncMock(return_value=["GMAIL"]),
            ),
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as queued,
        ):
            missing = await connected_tools.awaiting_setup(7, [])
        assert missing == ["gmail"]
        assert queued.await_count == 1
        assert queued.await_args.kwargs["apps"] == ["gmail"]

    @pytest.mark.asyncio
    async def test_an_app_that_already_has_tools_is_not_a_gap(self):
        tool = MagicMock()
        with (
            patch(
                "api.services.integrations.composio.client.connected_toolkits",
                AsyncMock(return_value=["GMAIL"]),
            ),
            patch.object(connected_tools, "toolkit_of", return_value="gmail"),
            patch("api.tasks.arq.enqueue_job", AsyncMock()) as queued,
        ):
            missing = await connected_tools.awaiting_setup(7, [tool])
        assert missing == []
        assert queued.await_count == 0

    @pytest.mark.asyncio
    async def test_a_vendor_that_cannot_be_read_reports_no_gap(self):
        # The old behaviour, which is the safe one: never invent a gap.
        with patch(
            "api.services.integrations.composio.client.connected_toolkits",
            AsyncMock(side_effect=RuntimeError("composio is having a day")),
        ):
            assert await connected_tools.awaiting_setup(7, []) == []


class TestTheJobFindsSomebodyToCreateTheRowsAs:
    """The chat has a workspace, not a person at a keyboard. Passing a
    stand-in id would make the job a silent no-op, which is the exact class
    of bug this whole thread has been about."""

    @pytest.mark.asyncio
    async def test_it_falls_back_to_a_member_when_there_is_no_caller(self):
        member = MagicMock(id=9, selected_organization_id=7)
        synced = MagicMock(created=3, error=None)
        with (
            patch.object(
                connector_tools.db_client,
                "get_organization_users",
                AsyncMock(return_value=[member]),
            ),
            patch.object(
                connector_tools, "toolkit_name", AsyncMock(return_value="Gmail")
            ),
            patch.object(
                connector_tools.tool_sync,
                "ensure_tools",
                AsyncMock(return_value=synced),
            ) as ensure,
        ):
            result = await connector_tools.sync_missing_tools(
                organization_id=7, apps=["gmail"], user_id=None
            )
        assert result == {"created": 3, "failed": 0}
        assert ensure.await_args.kwargs["actor"] is member

    @pytest.mark.asyncio
    async def test_a_member_of_another_organisation_is_never_used(self):
        # Rows created as somebody else's organisation would be invisible
        # here and wrong there.
        stranger = MagicMock(id=9, selected_organization_id=99)
        with (
            patch.object(
                connector_tools.db_client,
                "get_organization_users",
                AsyncMock(return_value=[stranger]),
            ),
            patch.object(
                connector_tools.tool_sync, "ensure_tools", AsyncMock()
            ) as ensure,
        ):
            result = await connector_tools.sync_missing_tools(
                organization_id=7, apps=["gmail"], user_id=None
            )
        assert result == {"created": 0, "failed": 0}
        assert ensure.await_count == 0
