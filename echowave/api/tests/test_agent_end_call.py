"""The agent hanging up, when the operator has said it may.

A tester rang, said "No, I am not there", and the agent replied "Okay, I will
close the call" -- then kept talking for another twenty-four seconds. Saying it
was the only thing it could do: ending a call was the caller's job alone, and
the phrase watcher cannot help when nobody is there to say goodbye.

The tests that matter are in two groups: the tool is not offered to an agent
nobody switched it on for, and when it is called the call actually ends rather
than being described as ending.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.pipecat import agent_end_call
from api.services.workflow.pipecat_engine_context_composer import (
    compose_functions_for_node,
)


def node(*, out_edges=(), documents=(), tools=()):
    return SimpleNamespace(
        out_edges=list(out_edges),
        document_uuids=list(documents),
        tool_uuids=list(tools),
        mcp_tool_filters=None,
    )


class TestItIsOffUnlessAskedFor:
    """An agent that can hang up will sometimes hang up on someone it should
    not have. An operator who never asked for that would rather a caller sat
    through one confused turn."""

    @pytest.mark.parametrize(
        "config",
        [
            None,
            {},
            {"agent_can_end_call": False},
            {"agent_can_end_call": "true"},
            "yes",
        ],
    )
    def test_anything_short_of_an_explicit_yes_is_off(self, config):
        assert agent_end_call.wants_agent_end_call(config) is False

    def test_an_explicit_yes_is_on(self):
        assert agent_end_call.wants_agent_end_call({"agent_can_end_call": True}) is True


@pytest.mark.asyncio
class TestTheToolIsOnlyOfferedWhenAllowed:
    async def test_it_is_absent_by_default(self):
        functions = await compose_functions_for_node(
            node=node(), custom_tool_manager=None
        )

        assert [f for f in functions if f.name == agent_end_call.TOOL_NAME] == []

    async def test_it_is_present_when_switched_on(self):
        functions = await compose_functions_for_node(
            node=node(), custom_tool_manager=None, agent_can_end_call=True
        )

        offered = [f for f in functions if f.name == agent_end_call.TOOL_NAME]
        assert len(offered) == 1

    async def test_it_is_offered_on_a_node_with_no_way_out(self):
        """An end node has no transitions, and a caller who has gone quiet on
        the last step is exactly who this exists for."""
        functions = await compose_functions_for_node(
            node=node(out_edges=()), custom_tool_manager=None, agent_can_end_call=True
        )

        assert any(f.name == agent_end_call.TOOL_NAME for f in functions)

    async def test_the_description_tells_the_model_that_saying_it_is_not_doing_it(self):
        """The failure this was built for was a model narrating a hang-up it
        had no way to perform."""
        assert "does not end it" in agent_end_call.DESCRIPTION


class TestTheReasonIsGroupable:
    """A free-text reason would be a fresh phrase every call and useless to
    group by, so a run cannot be found later."""

    @pytest.mark.parametrize("reason", agent_end_call.REASONS)
    def test_a_known_reason_survives(self, reason):
        assert agent_end_call.resolve_reason(reason) == reason

    @pytest.mark.parametrize(
        "raw", ["because I felt like it", "", None, 7, "CALLER_DONE "]
    )
    def test_anything_else_falls_back_rather_than_inventing_a_disposition(self, raw):
        resolved = agent_end_call.resolve_reason(raw)
        assert resolved in agent_end_call.REASONS

    def test_case_and_padding_are_forgiven(self):
        assert agent_end_call.resolve_reason("  Caller_Absent ") == "caller_absent"


@pytest.mark.asyncio
class TestCallingItActuallyEndsTheCall:
    def engine(self, farewell="Thanks for calling. Goodbye!"):
        from api.services.workflow.pipecat_engine import PipecatEngine

        engine = PipecatEngine.__new__(PipecatEngine)
        engine._call_disposed = False
        engine._gathered_context = {}
        engine._agent_end_call_farewell = farewell
        engine.task = SimpleNamespace(queue_frame=AsyncMock())
        engine.end_call_with_reason = AsyncMock()
        return engine

    def params(self, **arguments):
        return SimpleNamespace(
            arguments=arguments or {"reason": "caller_absent"},
            result_callback=AsyncMock(),
        )

    async def test_the_call_is_ended_not_merely_described(self):
        engine = self.engine()

        await engine._end_call_tool_handler(self.params())

        engine.end_call_with_reason.assert_awaited_once()

    async def test_the_farewell_is_spoken_before_the_line_drops(self):
        engine = self.engine()

        await engine._end_call_tool_handler(self.params())

        engine.task.queue_frame.assert_awaited_once()
        spoken = engine.task.queue_frame.await_args.args[0]
        assert "Goodbye" in spoken.text
        # Queued first, so it plays out rather than being cut off by the end.
        assert (
            engine.end_call_with_reason.await_args.kwargs["abort_immediately"] is False
        )

    async def test_with_no_farewell_it_hangs_up_immediately(self):
        engine = self.engine(farewell=None)

        await engine._end_call_tool_handler(self.params())

        engine.task.queue_frame.assert_not_awaited()
        assert (
            engine.end_call_with_reason.await_args.kwargs["abort_immediately"] is True
        )

    async def test_the_tool_call_is_answered_before_the_call_ends(self):
        """An unanswered tool call leaves the model waiting on a turn that
        never comes, heard as seconds of silence before the line drops."""
        engine = self.engine()
        params = self.params()

        await engine._end_call_tool_handler(params)

        params.result_callback.assert_awaited_once()

    async def test_the_disposition_says_the_agent_did_it(self):
        engine = self.engine()

        await engine._end_call_tool_handler(self.params(reason="caller_absent"))

        assert (
            engine._gathered_context["call_disposition"] == agent_end_call.DISPOSITION
        )
        assert "end_reason:caller_absent" in engine._gathered_context["call_tags"]

    async def test_a_second_call_does_not_end_the_call_twice(self):
        engine = self.engine()
        engine._call_disposed = True

        await engine._end_call_tool_handler(self.params())

        engine.end_call_with_reason.assert_not_awaited()
