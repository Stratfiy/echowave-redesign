"""Bots in a channel are a team: one can hand a question to another by
@-mentioning it, and the hand-off stops before it becomes a loop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import channel_reply

MOD = "api.services.workflow.channel_reply"


def _roster():
    return [
        SimpleNamespace(id=3, name="Front desk", handle="front", folder_id=5),
        SimpleNamespace(id=4, name="Sales bot", handle="sales", folder_id=5),
        SimpleNamespace(id=9, name="Elsewhere", handle="else", folder_id=6),
    ]


class TestWhatABotIsTold:
    def test_names_the_others_here_by_handle(self):
        line = channel_reply.teammates_line(
            [{"id": 4, "handle": "sales", "name": "Sales bot"}]
        )
        assert "@sales (Sales bot)" in line
        assert channel_reply.teammates_line([]) == ""


@pytest.mark.asyncio
class TestHandingOn:
    async def _reply(self, answer: str, *, hop: int, folder_id=5):
        session = SimpleNamespace(revision=1)
        with (
            patch(
                f"{MOD}.db_client.get_workflow_by_id",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        id=3, name="Front desk", organization_id=7
                    )
                ),
            ),
            patch(
                f"{MOD}.db_client.create_workflow_run",
                new=AsyncMock(return_value=SimpleNamespace(id=11)),
            ),
            patch(f"{MOD}.set_current_run_id"),
            patch(
                f"{MOD}.authorize_workflow_run_start",
                new=AsyncMock(
                    return_value=SimpleNamespace(has_quota=True, error_message=None)
                ),
            ),
            patch(
                f"{MOD}.db_client.ensure_workflow_run_text_session",
                new=AsyncMock(return_value=session),
            ),
            patch(
                f"{MOD}.initialize_text_chat_session",
                new=AsyncMock(return_value=session),
            ),
            patch(
                f"{MOD}.execute_pending_text_chat_turn",
                new=AsyncMock(return_value=session),
            ),
            patch(
                f"{MOD}.channel_context.recent_thread", new=AsyncMock(return_value="")
            ),
            patch(
                f"{MOD}.channel_context.recent_bot_thread",
                new=AsyncMock(return_value=""),
            ),
            patch(
                f"{MOD}.db_client.get_all_workflows_for_listing",
                new=AsyncMock(return_value=_roster()),
            ),
            patch(
                f"{MOD}.append_text_chat_user_message",
                new=AsyncMock(return_value=session),
            ) as appended,
            patch(f"{MOD}._last_assistant_text", return_value=answer),
            patch(f"{MOD}.agent_timeline.record", new=AsyncMock()) as record,
            patch("api.tasks.arq.enqueue_job", new=AsyncMock()) as enqueue,
        ):
            run_id = await channel_reply.answer_in_channel(
                3, folder_id, "Book Meera", hop=hop
            )
        return run_id, appended, record, enqueue

    async def test_a_reply_that_mentions_a_teammate_hands_it_on_one_hop_further(self):
        run_id, appended, record, enqueue = await self._reply(
            "Over to @sales for the price.", hop=0
        )
        assert run_id == 11
        # The bot was told who is here.
        assert "@sales (Sales bot)" in appended.await_args.kwargs["user_text"]
        # The reply row says who it asked, and the teammate is queued.
        reply = [
            c for c in record.await_args_list if c.kwargs.get("kind") == "message"
        ][-1]
        assert reply.kwargs["payload"]["asked"] == [4]
        assert enqueue.await_args.args[1:] == (
            4,
            5,
            "Front desk said: Over to @sales for the price.",
            None,
            1,
        )

    async def test_the_hand_off_stops_at_the_hop_limit(self):
        _, _, record, enqueue = await self._reply(
            "Back to @sales.", hop=channel_reply.MAX_HOPS
        )
        reply = [
            c for c in record.await_args_list if c.kwargs.get("kind") == "message"
        ][-1]
        assert reply.kwargs["payload"]["asked"] == []
        assert not enqueue.await_count

    async def test_a_bot_never_hands_to_itself_or_outside_the_channel(self):
        _, _, _, enqueue = await self._reply("Ask @front or @else.", hop=0)
        assert not enqueue.await_count

    async def test_a_direct_message_has_no_teammates(self):
        _, appended, _, enqueue = await self._reply(
            "Over to @sales.", hop=0, folder_id=None
        )
        assert "Bots in this channel" not in appended.await_args.kwargs["user_text"]
        assert not enqueue.await_count
