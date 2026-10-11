"""A direct message is answered without the scheduled-task opening turn.

A channel message still runs the opening turn (initialize + execute) before
the message turn; a direct message (``folder_id`` None) runs only the message
turn, so it makes one LLM call fewer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import channel_reply

MOD = "api.services.workflow.channel_reply"


async def _answer(folder_id):
    session = SimpleNamespace(revision=1)
    execute = AsyncMock(return_value=session)
    initialize = AsyncMock(return_value=session)
    with (
        patch(
            f"{MOD}.db_client.get_workflow_by_id",
            new=AsyncMock(
                return_value=SimpleNamespace(id=3, name="Front desk", organization_id=7)
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
        patch(f"{MOD}.initialize_text_chat_session", new=initialize),
        patch(f"{MOD}.execute_pending_text_chat_turn", new=execute),
        patch(f"{MOD}.channel_context.recent_thread", new=AsyncMock(return_value="")),
        patch(
            f"{MOD}.channel_context.recent_bot_thread",
            new=AsyncMock(return_value=""),
        ),
        patch(
            f"{MOD}.db_client.get_all_workflows_for_listing",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            f"{MOD}.append_text_chat_user_message",
            new=AsyncMock(return_value=session),
        ) as appended,
        patch(f"{MOD}._last_assistant_text", return_value="Done."),
        patch(f"{MOD}.agent_timeline.record", new=AsyncMock()),
        patch(
            f"{MOD}.billing_events.charge_in_own_session",
            new=AsyncMock(return_value=0),
        ),
        patch(f"{MOD}.one_shot_run.close", new=AsyncMock()),
    ):
        run_id = await channel_reply.answer_in_channel(3, folder_id, "Draft an invoice")
    return run_id, initialize, execute, appended


@pytest.mark.asyncio
async def test_a_direct_message_runs_only_the_message_turn():
    run_id, initialize, execute, appended = await _answer(None)
    assert run_id == 11
    initialize.assert_not_awaited()
    assert execute.await_count == 1
    assert "Draft an invoice" in appended.await_args.kwargs["user_text"]


@pytest.mark.asyncio
async def test_a_channel_message_still_runs_the_opening_turn():
    run_id, initialize, execute, _ = await _answer(5)
    assert run_id == 11
    initialize.assert_awaited_once()
    assert execute.await_count == 2


@pytest.mark.asyncio
async def test_a_direct_message_makes_one_llm_call_fewer():
    _, _, direct, _ = await _answer(None)
    _, _, channel, _ = await _answer(5)
    assert channel.await_count - direct.await_count == 1
