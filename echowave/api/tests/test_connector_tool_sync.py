"""Connecting an app brings its tools with it.

The defect: an authorization that worked, an app that showed as connected,
and a Tools screen that was still empty, because a tool is a separate row
somebody had to create by hand in the builder, one action at a time, by
slug. People connected an app, saw nothing change, and concluded it had
not worked.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.integrations.composio import tool_sync


class _Actor:
    def __init__(self, organization_id=1, user_id=7):
        self.id = user_id
        self.selected_organization_id = organization_id


def _actions(*slugs):
    return [{"slug": s, "does": f"{s} does a thing"} for s in slugs]


class TestNames:
    def test_the_app_prefix_is_dropped(self):
        assert tool_sync.action_words("GMAIL_SEND_EMAIL") == "Send Email"

    def test_a_slug_with_no_prefix_survives(self):
        assert tool_sync.action_words("SEARCH") == "Search"

    def test_an_empty_slug_is_never_an_empty_name(self):
        """A row with no name is the silent kind of missing."""
        assert tool_sync.action_words("") == ""
        assert tool_sync.action_words("X") == "X"


class TestSync:
    @pytest.mark.asyncio
    async def test_it_creates_one_tool_per_action(self):
        with (
            patch.object(
                tool_sync,
                "toolkit_actions",
                AsyncMock(return_value=_actions("GMAIL_SEND", "GMAIL_FETCH")),
            ),
            patch.object(tool_sync, "existing_slugs", AsyncMock(return_value=set())),
            patch.object(tool_sync, "create_tool_for_user", AsyncMock()) as create,
        ):
            result = await tool_sync.ensure_tools(
                organization_id=1, app="gmail", app_name="Gmail", actor=_Actor()
            )
        assert result.created == 2
        assert result.error is None
        made = [call.args[0] for call in create.await_args_list]
        assert made[0].name == "Gmail — Send"
        assert made[0].definition.config.tool_slug == "GMAIL_SEND"
        assert made[0].description == "GMAIL_SEND does a thing"

    @pytest.mark.asyncio
    async def test_a_second_sync_adds_only_what_is_missing(self):
        with (
            patch.object(
                tool_sync,
                "toolkit_actions",
                AsyncMock(return_value=_actions("GMAIL_SEND", "GMAIL_FETCH")),
            ),
            patch.object(
                tool_sync, "existing_slugs", AsyncMock(return_value={"GMAIL_SEND"})
            ),
            patch.object(tool_sync, "create_tool_for_user", AsyncMock()) as create,
        ):
            result = await tool_sync.ensure_tools(
                organization_id=1, app="gmail", app_name="Gmail", actor=_Actor()
            )
        assert result.created == 1
        assert result.existing == 1
        assert result.total == 2
        assert create.await_count == 1

    @pytest.mark.asyncio
    async def test_it_never_makes_more_than_the_cap(self):
        """Some toolkits expose hundreds; a business wants neither none nor
        four hundred."""
        with (
            patch.object(tool_sync, "toolkit_actions", AsyncMock()) as actions,
            patch.object(tool_sync, "existing_slugs", AsyncMock(return_value=set())),
            patch.object(tool_sync, "create_tool_for_user", AsyncMock()),
        ):
            actions.return_value = _actions(
                *[f"A_{i}" for i in range(tool_sync.MAX_PER_APP)]
            )
            await tool_sync.ensure_tools(
                organization_id=1, app="gmail", app_name="Gmail", actor=_Actor()
            )
        assert actions.await_args.kwargs["limit"] == tool_sync.MAX_PER_APP

    @pytest.mark.asyncio
    async def test_one_bad_action_does_not_lose_the_others(self):
        calls = {"n": 0}

        async def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("no")

        with (
            patch.object(
                tool_sync,
                "toolkit_actions",
                AsyncMock(return_value=_actions("A_ONE", "A_TWO")),
            ),
            patch.object(tool_sync, "existing_slugs", AsyncMock(return_value=set())),
            patch.object(tool_sync, "create_tool_for_user", flaky),
        ):
            result = await tool_sync.ensure_tools(
                organization_id=1, app="gmail", app_name="Gmail", actor=_Actor()
            )
        assert result.created == 1

    @pytest.mark.asyncio
    async def test_a_vendor_that_will_not_answer_is_a_retry_not_an_empty_app(self):
        """``toolkit_actions`` returns None for a failed read and [] for an
        app with nothing: they are different sentences and stay different."""
        with patch.object(tool_sync, "toolkit_actions", AsyncMock(return_value=None)):
            result = await tool_sync.ensure_tools(
                organization_id=1, app="gmail", app_name="Gmail", actor=_Actor()
            )
        assert result.created == 0
        assert result.error and "Could not read" in result.error

    @pytest.mark.asyncio
    async def test_an_app_that_exposes_nothing_is_not_an_error(self):
        with (
            patch.object(tool_sync, "toolkit_actions", AsyncMock(return_value=[])),
            patch.object(tool_sync, "existing_slugs", AsyncMock(return_value=set())),
        ):
            result = await tool_sync.ensure_tools(
                organization_id=1, app="gmail", app_name="Gmail", actor=_Actor()
            )
        assert result.created == 0
        assert result.error is None

    @pytest.mark.asyncio
    async def test_rows_are_never_made_for_somebody_elses_organisation(self):
        with patch.object(tool_sync, "create_tool_for_user", AsyncMock()) as create:
            result = await tool_sync.ensure_tools(
                organization_id=1,
                app="gmail",
                app_name="Gmail",
                actor=_Actor(organization_id=2),
            )
        assert result.error
        create.assert_not_awaited()
