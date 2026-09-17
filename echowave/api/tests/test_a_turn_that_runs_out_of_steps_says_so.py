"""A turn that hits the tool-round cap says so, instead of saying nothing.

Seen live: "draft a reply to the newest mail from a real person, skip the
automated ones" ran for 37 seconds, read the inbox, and ended on "I have
nothing to add on that." The model had used its rounds finding a human
sender and was then handed no tools, with no word as to why, mid-plan. It
returned empty text and the fallback sentence covered it up.

Three things fixed and guarded here: the cap is six rounds, not four; when
the cap withdraws the tools the model is told so and asked to answer with
what it has; and when it still says nothing after a capped turn, the person
is told the truth -- that the turn ran out of steps -- rather than that
there was nothing to say.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.agent_builder.client import ModelReply, ToolCall
from api.services.workflow import connected_tools, decibyl


def _read(i: int) -> ModelReply:
    """A round that only reads a connected app."""
    return ModelReply(
        text="",
        tool_calls=(
            ToolCall(
                id=f"c{i}", name=f"{connected_tools.PREFIX}gmail_fetch", arguments={}
            ),
        ),
    )


def _harness(replies: list[ModelReply], tool_result: dict | None = None):
    """answer() with everything but the model and the tool loop mocked."""
    complete = AsyncMock(side_effect=replies)
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return complete, (
        patch(
            "api.services.workflow.decibyl.build_context",
            new=AsyncMock(return_value="## Team"),
        ),
        patch(
            "api.services.workflow.decibyl.office_context",
            new=AsyncMock(return_value=""),
        ),
        patch(
            "api.services.workflow.decibyl.db_client.agent_events",
            new=AsyncMock(
                return_value=[
                    SimpleNamespace(
                        actor="human",
                        payload={"body": "draft a reply"},
                        summary="draft a reply",
                        at=datetime.now(UTC),
                    )
                ]
            ),
        ),
        patch(
            "api.services.workflow.decibyl.db_client.async_session",
            return_value=session,
        ),
        patch(
            "api.services.agent_builder.settings.resolve_model",
            new=AsyncMock(
                return_value=SimpleNamespace(provider="openai", model="m", api_key="k")
            ),
        ),
        patch("api.services.agent_builder.client.stream", new=complete),
        patch(
            "api.services.workflow.decibyl.tools_for",
            new=AsyncMock(return_value=[{"name": "t"}]),
        ),
        patch(
            "api.services.workflow.decibyl._tool",
            new=AsyncMock(
                return_value=tool_result or {"status": "success", "data": {}}
            ),
        ),
        patch("api.services.workflow.decibyl.agent_timeline.record", new=AsyncMock()),
        patch("api.services.workflow.decibyl.reply_draft.clear", new=AsyncMock()),
    )


async def _run(replies, tool_result=None):
    complete, patches = _harness(replies, tool_result)
    from contextlib import ExitStack

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        body = await decibyl.answer(7, "draft a reply")
    return body, complete


class TestTheCap:
    def test_six_rounds(self):
        assert decibyl.MAX_TOOL_ROUNDS == 6


@pytest.mark.asyncio
class TestWhenTheCapWithdrawsTheTools:
    async def test_the_model_is_told_it_is_the_last_step(self):
        reads = [_read(i) for i in range(decibyl.MAX_TOOL_ROUNDS)]
        final = ModelReply(
            text="I read the inbox; the newest human mail is from Meera."
        )
        body, complete = await _run(reads + [final])
        assert body == final.text
        # The capped call: no tools, and the notice is the last thing said.
        last = complete.await_args_list[-1].kwargs
        assert last["tools"] is None
        messages = last["conversation"].messages
        assert messages[-1]["role"] == "user"
        assert decibyl.LAST_STEP_NOTICE in str(messages[-1]["content"])

    async def test_below_the_cap_no_notice_and_tools_stay(self):
        replies = [_read(0), ModelReply(text="Done.")]
        body, complete = await _run(replies)
        assert body == "Done."
        second = complete.await_args_list[1].kwargs
        assert second["tools"] is not None
        assert decibyl.LAST_STEP_NOTICE not in str(second["conversation"].messages)

    async def test_a_card_round_ends_the_tools_without_the_notice(self):
        """A proposed card ends the tool phase on purpose -- "propose it, say
        so, end". That is not the cap, and the notice would be a lie."""
        proposed = {"status": "proposed", "card": 1}
        replies = [_read(0), ModelReply(text="Proposed the draft for you.")]
        body, complete = await _run(replies, tool_result=proposed)
        assert body == "Proposed the draft for you."
        second = complete.await_args_list[1].kwargs
        assert second["tools"] is None
        assert decibyl.LAST_STEP_NOTICE not in str(second["conversation"].messages)


@pytest.mark.asyncio
class TestWhatThePersonReadsWhenTheModelSaysNothing:
    async def test_after_a_capped_turn_it_is_the_truth(self):
        reads = [_read(i) for i in range(decibyl.MAX_TOOL_ROUNDS)]
        body, _ = await _run(reads + [ModelReply(text="")])
        assert body == decibyl.RAN_OUT_OF_STEPS

    async def test_with_no_rounds_it_is_the_old_sentence(self):
        """Genuinely nothing to say is still a thing, and is not a step
        problem."""
        body, _ = await _run([ModelReply(text="   ")])
        assert body == "I have nothing to add on that."
