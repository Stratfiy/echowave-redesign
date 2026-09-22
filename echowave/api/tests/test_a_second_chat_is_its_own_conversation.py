"""A second chat with Decibyl reads and writes as its own conversation.

#372 added the column and the read filter. It changed nothing anybody could
see, because nothing ever wrote a thread id: every row still landed in the
original thread, so a second chat would have shown the first one's history
and added its own lines to it.

What is tested here is the thing that breaks silently. A turn writes to the
timeline from a dozen places -- the line itself, an activity line per tool,
a proposed card, a failure -- and any one of them left behind puts a row in
the wrong conversation for the one person who started a second chat. So the
thread is set once for the turn and read by every writer, and these tests
are about that default holding: for a row nobody threaded by hand, for a
bot's own history which is not a chat at all, and for the boundary of the
turn, since a thread that outlived its turn would be worse than none.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.enums import AgentEventActor, AgentEventKind
from api.services.workflow import agent_timeline, decibyl
from api.tasks.function_names import FunctionNames


@pytest.mark.asyncio
class TestWhereARowIsWritten:
    @staticmethod
    async def _written(**kwargs) -> dict:
        with patch(
            "api.services.workflow.agent_timeline.db_client.record_agent_event",
            new=AsyncMock(),
        ) as write:
            await agent_timeline.record(
                organization_id=7,
                kind=AgentEventKind.MESSAGE.value,
                summary="hi",
                **kwargs,
            )
        return write.await_args.kwargs

    async def test_the_turns_thread_is_the_default(self):
        """The point of the whole change: a writer that says nothing about
        threads still lands in the conversation being spoken in."""
        with agent_timeline.in_thread("t-2"):
            row = await self._written()
        assert row["thread_id"] == "t-2"

    async def test_no_thread_set_writes_the_original(self):
        assert (await self._written())["thread_id"] is None

    async def test_an_explicit_thread_beats_the_turns(self):
        with agent_timeline.in_thread("t-2"):
            row = await self._written(thread_id="t-9")
        assert row["thread_id"] == "t-9"

    async def test_a_bots_own_history_is_never_threaded(self):
        """Threads are Decibyl's. A bot's timeline is read with no thread
        predicate at all, so an id on one of its rows is a value nothing
        reads and a column that looks meaningful to the next person."""
        with (
            patch(
                "api.services.workflow.agent_timeline._folder_for",
                new=AsyncMock(return_value=None),
            ),
            agent_timeline.in_thread("t-2"),
        ):
            row = await self._written(workflow_id=3)
        assert row["thread_id"] is None

    async def test_a_channel_row_is_never_threaded(self):
        with agent_timeline.in_thread("t-2"):
            row = await self._written(folder_id=5, in_channel=False)
        assert row["thread_id"] is None

    async def test_the_thread_does_not_outlive_its_turn(self):
        """Two turns share a process. A thread that leaked would file one
        person's chat under another's, which is the worst version of this
        bug and the one no screen would show."""
        with agent_timeline.in_thread("t-2"):
            assert agent_timeline.current_thread() == "t-2"
        assert agent_timeline.current_thread() is None

    async def test_an_activity_line_can_be_threaded_by_hand(self):
        with patch(
            "api.services.workflow.agent_timeline.record", new=AsyncMock()
        ) as record:
            await agent_timeline.record_activity(
                organization_id=7, summary="Read 3 passages", thread_id="t-2"
            )
        assert record.await_args.kwargs["thread_id"] == "t-2"


@pytest.mark.asyncio
class TestTheLineAndTheTurn:
    async def test_the_line_is_recorded_in_its_thread_and_the_job_carries_it(self):
        with (
            patch(
                "api.services.workflow.decibyl.db_client.get_all_workflows_for_listing",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ) as record,
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            await decibyl.ask(
                organization_id=7,
                user_id=42,
                text="what happened this week?",
                attachments=[],
                line="what happened this week?",
                preset=None,
                thread_id="t-2",
            )
        row = record.await_args.kwargs
        assert row["kind"] == AgentEventKind.MESSAGE.value
        assert row["actor"] == AgentEventActor.HUMAN.value
        assert row["thread_id"] == "t-2"
        assert enqueue.await_args.args[0] == FunctionNames.ANSWER_DECIBYL_MESSAGE
        # The reply runs in another process, so the thread has to travel with
        # the job or the answer comes back in the wrong chat.
        assert enqueue.await_args.kwargs["thread_id"] == "t-2"

    async def test_a_line_with_no_thread_still_goes_where_it_always_did(self):
        with (
            patch(
                "api.services.workflow.decibyl.db_client.get_all_workflows_for_listing",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()
            ) as record,
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            await decibyl.ask(
                organization_id=7,
                user_id=42,
                text="hello",
                attachments=[],
                line="hello",
                preset=None,
            )
        assert record.await_args.kwargs["thread_id"] is None
        assert enqueue.await_args.kwargs["thread_id"] is None

    async def test_the_history_the_model_is_given_is_that_threads(self):
        """A second chat that was shown the first one's turns would not be a
        second chat."""
        with patch(
            "api.services.workflow.decibyl.db_client.agent_events",
            new=AsyncMock(return_value=[]),
        ) as read:
            await decibyl._history(7, "t-2")
        assert read.await_args.kwargs["thread_id"] == "t-2"
        assert read.await_args.kwargs["assistant_thread"] is True

    async def test_the_turn_sets_the_thread_for_everything_it_writes(self):
        """answer() is the entry point, so the thread is set there and every
        writer the turn reaches -- including ones not written yet -- is right
        without being told."""
        seen: dict[str, str | None] = {}

        async def _inner(*args, **kwargs):
            seen["thread"] = agent_timeline.current_thread()
            return "said"

        with patch("api.services.workflow.decibyl._answer", new=_inner):
            body = await decibyl.answer(7, "hello", thread_id="t-2")
        assert body == "said"
        assert seen["thread"] == "t-2"


@pytest.mark.asyncio
class TestTheListOfChats:
    @staticmethod
    async def _listed(rows, titles):
        from api.db import db_client

        captured: dict[str, str] = {}
        calls: list[object] = []

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, query):
                calls.append(query)
                result = MagicMock()
                if len(calls) == 1:
                    captured["sql"] = str(
                        query.compile(compile_kwargs={"literal_binds": True})
                    )
                    result.all.return_value = rows
                else:
                    result.all.return_value = titles
                return result

        with patch.object(db_client, "async_session", lambda: _Session()):
            listed = await db_client.assistant_threads(organization_id=7)
        return listed, captured["sql"]

    async def test_a_chat_is_named_by_its_first_message(self):
        from datetime import UTC, datetime

        at = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
        rows = [
            SimpleNamespace(thread_id="t-2", last_at=at, messages=4, first_id=11),
            SimpleNamespace(thread_id=None, last_at=at, messages=9, first_id=2),
        ]
        listed, _ = await self._listed(
            rows, [(11, "draft a reply", {}), (2, "hello", {})]
        )
        assert [t["title"] for t in listed] == ["draft a reply", "hello"]
        assert [t["messages"] for t in listed] == [4, 9]

    async def test_the_original_conversation_is_in_the_list(self):
        """It is the only chat every account already has. A list of older
        chats that cannot show it would hide everybody's whole history."""
        from datetime import UTC, datetime

        at = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
        listed, _ = await self._listed(
            [SimpleNamespace(thread_id=None, last_at=at, messages=9, first_id=2)],
            [(2, "hello", {})],
        )
        assert listed[0]["thread_id"] is None

    async def test_only_messages_count_as_a_chat(self):
        """An activity line and a proposed card belong to a turn somebody
        started by speaking. Counting them would make "4 messages" mean
        something nobody said, and a title of "Read 3 passages"."""
        _, sql = await self._listed([], [])
        assert f"kind = '{AgentEventKind.MESSAGE.value}'" in sql
        assert "workflow_id IS NULL" in sql
        assert "folder_id IS NULL" in sql
        assert "organization_id = 7" in sql


@pytest.mark.asyncio
class TestWhatElseTheTurnReads:
    """Two readers inside a turn had no thread and would have read the wrong
    chat's rows while answering in another."""

    async def test_a_waiting_card_in_another_chat_does_not_swallow_this_ask(self):
        """One ask, one card (#371) is about one conversation. Scoped to the
        account, a card pending in the first chat would suppress the card
        somebody asked for in the second -- an ask that gets nothing, which
        is the failure that fix exists to stop, in a new place."""
        from api.services.workflow import actions

        with (
            patch(
                "api.services.workflow.actions.db_client.agent_events",
                new=AsyncMock(return_value=[]),
            ) as read,
            agent_timeline.in_thread("t-2"),
        ):
            await actions._already_proposed(
                organization_id=7, workflow_id=None, payload={}, in_channel=False
            )
        assert read.await_args.kwargs["assistant_thread"] is True
        assert read.await_args.kwargs["thread_id"] == "t-2"

    async def test_a_bots_own_cards_are_not_scoped_to_a_thread(self):
        from api.services.workflow import actions

        with (
            patch(
                "api.services.workflow.actions.db_client.agent_events",
                new=AsyncMock(return_value=[]),
            ) as read,
            agent_timeline.in_thread("t-2"),
        ):
            await actions._already_proposed(
                organization_id=7, workflow_id=3, payload={}
            )
        assert read.await_args.kwargs["assistant_thread"] is False
        assert read.await_args.kwargs["thread_id"] is None

    async def test_what_you_asked_before_means_in_this_chat(self):
        from api.services.workflow import home_openers

        with (
            patch(
                "api.services.workflow.home_openers.db_client.agent_events",
                new=AsyncMock(return_value=[]),
            ) as read,
            agent_timeline.in_thread("t-2"),
        ):
            await home_openers.recent_questions(7)
        assert read.await_args.kwargs["thread_id"] == "t-2"

    async def test_off_a_turn_it_is_the_original_chat(self):
        """The home screen reads outside any turn, exactly as before."""
        from api.services.workflow import home_openers

        with patch(
            "api.services.workflow.home_openers.db_client.agent_events",
            new=AsyncMock(return_value=[]),
        ) as read:
            await home_openers.recent_questions(7)
        assert read.await_args.kwargs["thread_id"] is None
