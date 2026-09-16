"""The backfill for apps connected before the ranking existed.

The ranking fixed which twelve actions a *new* connection brings. Nothing
reaches an old one: the only thing that enqueues a sync asks whether an app
has no rows at all, so an app holding the alphabet's twelve -- seven ways to
delete mail, no way to send one -- is indistinguishable from an app holding
the right twelve and is never looked at again.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import ToolCategory, ToolStatus
from scripts import resync_connector_tools as resync


def _row(organization_id: int, toolkit: str, slug: str):
    return SimpleNamespace(
        organization_id=organization_id,
        category=ToolCategory.COMPOSIO.value,
        status=ToolStatus.ACTIVE.value,
        definition={
            "type": "composio",
            "config": {"toolkit": toolkit, "tool_slug": slug},
        },
    )


def _actions(*slugs):
    return [{"slug": s, "does": f"{s} does a thing"} for s in slugs]


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def execute(self, _query):
        rows = self._rows
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


def _with_rows(rows):
    return patch.object(resync.db_client, "async_session", lambda: _Session(rows))


class TestPopulation:
    @pytest.mark.asyncio
    async def test_it_groups_toolkits_by_organisation(self):
        rows = [
            _row(1, "gmail", "GMAIL_FETCH_EMAILS"),
            _row(1, "gmail", "GMAIL_DELETE_MESSAGE"),
            _row(1, "calendly", "CALENDLY_LIST_EVENTS"),
            _row(2, "gmail", "GMAIL_ADD_LABEL"),
        ]
        with _with_rows(rows):
            found = await resync._connected_by_organisation(organization_id=None)
        assert found == {1: {"gmail", "calendly"}, 2: {"gmail"}}

    @pytest.mark.asyncio
    async def test_a_row_with_no_toolkit_is_skipped_not_counted(self):
        """An HTTP tool is not a connected app, and must not become one."""
        bad = SimpleNamespace(
            organization_id=1,
            category=ToolCategory.COMPOSIO.value,
            status=ToolStatus.ACTIVE.value,
            definition={"type": "http_api", "config": {}},
        )
        with _with_rows([bad, _row(1, "gmail", "GMAIL_FETCH_EMAILS")]):
            found = await resync._connected_by_organisation(organization_id=None)
        assert found == {1: {"gmail"}}


class TestWhatIsMissing:
    @pytest.mark.asyncio
    async def test_it_names_the_ranked_actions_the_account_lacks(self):
        """The real Gmail case: it holds the deletes, it lacks SEND_EMAIL."""
        with (
            patch.object(
                resync,
                "toolkit_actions",
                AsyncMock(
                    return_value=_actions(
                        "GMAIL_DELETE_MESSAGE",
                        "GMAIL_FETCH_EMAILS",
                        "GMAIL_SEND_EMAIL",
                    )
                ),
            ),
            patch.object(
                resync.tool_sync,
                "existing_slugs",
                AsyncMock(return_value={"GMAIL_DELETE_MESSAGE", "GMAIL_FETCH_EMAILS"}),
            ),
        ):
            missing = await resync._would_create(1, "gmail")
        assert missing == ["GMAIL_SEND_EMAIL"]

    @pytest.mark.asyncio
    async def test_an_account_holding_the_right_twelve_is_missing_nothing(self):
        with (
            patch.object(
                resync,
                "toolkit_actions",
                AsyncMock(return_value=_actions("GMAIL_FETCH_EMAILS")),
            ),
            patch.object(
                resync.tool_sync,
                "existing_slugs",
                AsyncMock(return_value={"GMAIL_FETCH_EMAILS"}),
            ),
        ):
            assert await resync._would_create(1, "gmail") == []

    @pytest.mark.asyncio
    async def test_a_vendor_that_will_not_answer_is_not_reported_as_nothing_missing(
        self,
    ):
        """ "Could not ask" and "nothing to add" are the same word otherwise."""
        with patch.object(
            resync, "toolkit_actions", AsyncMock(side_effect=RuntimeError("down"))
        ):
            assert await resync._would_create(1, "gmail") is None

    @pytest.mark.asyncio
    async def test_it_never_offers_more_than_the_cap(self):
        many = _actions(*[f"GMAIL_GET_{n}" for n in range(40)])
        with (
            patch.object(resync, "toolkit_actions", AsyncMock(return_value=many)),
            patch.object(
                resync.tool_sync, "existing_slugs", AsyncMock(return_value=set())
            ),
        ):
            missing = await resync._would_create(1, "gmail")
        assert len(missing) == resync.tool_sync.MAX_PER_APP


class TestDryRunIsADryRun:
    @pytest.mark.asyncio
    async def test_without_confirm_nothing_is_created(self):
        with (
            _with_rows([_row(1, "gmail", "GMAIL_DELETE_MESSAGE")]),
            patch.object(
                resync,
                "toolkit_actions",
                AsyncMock(return_value=_actions("GMAIL_SEND_EMAIL")),
            ),
            patch.object(
                resync.tool_sync, "existing_slugs", AsyncMock(return_value=set())
            ),
            patch.object(resync, "sync_missing_tools", AsyncMock()) as sync,
        ):
            await resync._run(organization_id=None, confirm=False)
        sync.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_confirm_every_organisation_is_synced(self):
        rows = [
            _row(1, "gmail", "GMAIL_DELETE_MESSAGE"),
            _row(2, "calendly", "CALENDLY_LIST_EVENTS"),
        ]
        with (
            _with_rows(rows),
            patch.object(
                resync,
                "sync_missing_tools",
                AsyncMock(return_value={"created": 3, "failed": 0}),
            ) as sync,
        ):
            await resync._run(organization_id=None, confirm=True)
        assert sync.await_count == 2
        assert {c.kwargs["organization_id"] for c in sync.await_args_list} == {1, 2}
        assert all(c.kwargs["user_id"] is None for c in sync.await_args_list)

    @pytest.mark.asyncio
    async def test_an_empty_estate_says_so_rather_than_failing(self):
        with _with_rows([]):
            assert await resync._run(organization_id=None, confirm=True) == 0
