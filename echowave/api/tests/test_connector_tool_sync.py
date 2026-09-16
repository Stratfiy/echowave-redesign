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
        made = {
            call.args[0].definition.config.tool_slug: call.args[0]
            for call in create.await_args_list
        }
        assert set(made) == {"GMAIL_SEND", "GMAIL_FETCH"}
        assert made["GMAIL_SEND"].name == "Gmail — Send"
        assert made["GMAIL_SEND"].description == "GMAIL_SEND does a thing"
        # Order is no longer the vendor's. Reads are offered before writes,
        # because taking the vendor's alphabetical order gave a real account
        # seven ways to delete mail and no way to send it.
        first = create.await_args_list[0].args[0]
        assert first.definition.config.tool_slug == "GMAIL_FETCH"

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
        four hundred.

        Asserted on the rows created, not on the vendor's page size. The two
        used to be the same number, which is what made the choice
        alphabetical: only twelve were ever fetched, so there was nothing to
        choose between. A ranking cannot improve on a sample it never sees.
        """
        with (
            patch.object(tool_sync, "toolkit_actions", AsyncMock()) as actions,
            patch.object(tool_sync, "existing_slugs", AsyncMock(return_value=set())),
            patch.object(tool_sync, "create_tool_for_user", AsyncMock()) as create,
        ):
            actions.return_value = _actions(
                *[f"A_GET_{i}" for i in range(tool_sync.MAX_PER_APP * 4)]
            )
            result = await tool_sync.ensure_tools(
                organization_id=1, app="gmail", app_name="Gmail", actor=_Actor()
            )
        assert create.await_count == tool_sync.MAX_PER_APP
        assert result.created == tool_sync.MAX_PER_APP
        # And the whole catalogue is fetched, so the dozen is a choice.
        assert actions.await_args.kwargs["limit"] == tool_sync.ACTIONS_CONSIDERED
        assert tool_sync.ACTIONS_CONSIDERED > tool_sync.MAX_PER_APP

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


class TestTheFieldsTheModelFills:
    """A tool row carries the arguments the model is asked to fill.

    Chosen here, at sync time, rather than shown to the model as the vendor's
    whole list. n8n does the same thing in create-node-as-tool.ts, except an
    operator marks the fields by hand with $fromAI(); we have nobody to do
    that, so the choice is made from the published schema.
    """

    @pytest.mark.asyncio
    async def test_the_row_carries_the_chosen_fields(self):
        published = {
            "type": "object",
            "properties": {
                "recipient_email": {"type": "string", "description": "Who"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "is_html": {"type": "boolean"},
                "thread_id": {"type": "string"},
            },
            "required": ["recipient_email"],
        }
        with (
            patch.object(
                tool_sync,
                "toolkit_actions",
                AsyncMock(return_value=_actions("GMAIL_SEND_EMAIL")),
            ),
            patch.object(tool_sync, "existing_slugs", AsyncMock(return_value=set())),
            patch.object(
                tool_sync.tool_schema,
                "input_schema",
                AsyncMock(return_value=published),
            ),
            patch.object(tool_sync, "create_tool_for_user", AsyncMock()) as create,
        ):
            await tool_sync.ensure_tools(
                organization_id=1, app="gmail", app_name="Gmail", actor=_Actor()
            )
        config = create.await_args_list[0].args[0].definition.config
        offered = {p.name for p in config.parameters}
        assert "recipient_email" in offered
        assert "subject" in offered
        assert "is_html" not in offered, "an option nobody dictates is not offered"
        assert "thread_id" not in offered

    @pytest.mark.asyncio
    async def test_a_vendor_that_will_not_answer_still_makes_the_row(self):
        """The old behaviour, exactly: offered by description, loaded later.

        A schema we could not read must not cost the account its tool.
        """
        with (
            patch.object(
                tool_sync,
                "toolkit_actions",
                AsyncMock(return_value=_actions("GMAIL_SEND_EMAIL")),
            ),
            patch.object(tool_sync, "existing_slugs", AsyncMock(return_value=set())),
            patch.object(
                tool_sync.tool_schema,
                "input_schema",
                AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch.object(tool_sync, "create_tool_for_user", AsyncMock()) as create,
        ):
            result = await tool_sync.ensure_tools(
                organization_id=1, app="gmail", app_name="Gmail", actor=_Actor()
            )
        assert result.created == 1
        assert create.await_args_list[0].args[0].definition.config.parameters == []


class TestSlotsAreReservedNotOrdered:
    """Ranking reads above writes and taking the first twelve is not the
    same as reserving slots, and the difference cost a real account its
    send.

    Gmail publishes about fifteen GET actions. Under "reads first, cap at
    twelve" the reads filled every slot and no write was ever reached: a
    live account re-synced under that rule got twelve ways to read mail and
    no way to send one -- the same hole the ranking was written to close,
    arrived at from the other side.
    """

    def _slugs(self, actions, limit=None):
        return [
            a["slug"]
            for a in tool_sync.most_useful(actions, limit or tool_sync.MAX_PER_APP)
        ]

    def _gmail(self):
        reads = [f"GMAIL_GET_{n}" for n in range(14)] + ["GMAIL_FETCH_EMAILS"]
        writes = [
            "GMAIL_SEND_EMAIL",
            "GMAIL_SEND_DRAFT",
            "GMAIL_REPLY_TO_THREAD",
            "GMAIL_CREATE_EMAIL_DRAFT",
        ]
        deletes = ["GMAIL_DELETE_MESSAGE", "GMAIL_DELETE_THREAD"]
        return _actions(*sorted(reads + writes + deletes))

    def test_an_app_with_many_reads_still_gets_its_sends(self):
        """The regression, stated as the case that produced it."""
        assert "GMAIL_SEND_EMAIL" in self._slugs(self._gmail())

    def test_the_cap_still_holds(self):
        assert len(self._slugs(self._gmail())) == tool_sync.MAX_PER_APP

    def test_reads_still_take_most_of_the_dozen(self):
        """Eight is already more ways to look something up than anybody asks
        for; this is a reservation, not a reversal."""
        got = self._slugs(self._gmail())
        reads = [s for s in got if "_GET_" in s or "_FETCH_" in s]
        assert len(reads) == tool_sync.MAX_PER_APP - tool_sync.WRITE_SLOTS

    def test_a_delete_never_displaces_a_send(self):
        """The invariant that matters. Not "deletes are banned" -- an app
        whose whole surface is destructive would then connect and offer
        nothing, with nothing on any screen saying why, and banned looks
        identical to broken. Deletes take only genuinely spare room.
        """
        got = self._slugs(self._gmail())
        assert "GMAIL_SEND_EMAIL" in got
        assert not any("DELETE" in s for s in got)

    def test_a_delete_still_fills_room_nothing_else_wants(self):
        short = _actions("GMAIL_DELETE_THREAD", "GMAIL_FETCH_EMAILS")
        assert set(self._slugs(short)) == {"GMAIL_DELETE_THREAD", "GMAIL_FETCH_EMAILS"}

    def test_an_app_of_only_deletes_is_not_an_empty_app(self):
        """Connected and offering nothing is the failure this avoids."""
        assert self._slugs(_actions("A_DELETE_ONE", "A_DELETE_TWO"))

    def test_an_app_with_few_writes_gives_the_room_back_to_reads(self):
        actions = _actions(*[f"A_GET_{n}" for n in range(20)], "A_SEND_IT")
        got = self._slugs(actions)
        assert len(got) == tool_sync.MAX_PER_APP
        assert "A_SEND_IT" in got

    def test_an_app_with_few_reads_gives_the_room_back_to_writes(self):
        actions = _actions("A_GET_ONE", *[f"A_CREATE_{n}" for n in range(20)])
        got = self._slugs(actions)
        assert len(got) == tool_sync.MAX_PER_APP
        assert "A_GET_ONE" in got

    def test_an_app_smaller_than_the_cap_is_taken_whole(self):
        actions = _actions("A_GET_ONE", "A_SEND_IT")
        assert set(self._slugs(actions)) == {"A_GET_ONE", "A_SEND_IT"}

    def test_the_pick_is_stable_across_a_resync(self):
        """A list that churns every sync is one nobody can point a bot at."""
        actions = self._gmail()
        assert self._slugs(actions) == self._slugs(actions)
