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
    async def test_a_user_who_has_since_switched_account_creates_nothing(self):
        # Rows made as somebody else's organisation would be invisible here
        # and wrong there. The next read of the catalogue queues it again.
        user = MagicMock(id=3, selected_organization_id=99)
        with (
            patch.object(
                connector_tools.db_client,
                "get_user_by_id",
                AsyncMock(return_value=user),
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
