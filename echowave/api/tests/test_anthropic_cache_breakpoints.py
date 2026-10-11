"""Where the call pipeline puts its prompt-cache breakpoints.

Every agent-chat turn wrote ~12.3k tokens to the cache and read none: pipecat
marks only the last two user messages, a one-shot chat has a single one, so the
only breakpoint covered tools + system + the user's words and no other call
could match it. A test with a breakpoint on the system prompt read 9021 of
9027 tokens. These tests defend the request the adapter now builds.
"""

import copy
import json
import re
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import NOT_GIVEN, LLMContext

from api.services.pipecat.anthropic_llm import DecibylAnthropicLLMAdapter
from api.services.pipecat.system_clock import CLOCK_PARAGRAPH, split_clock
from api.services.workflow.pipecat_engine_context_composer import (
    compose_system_prompt_for_node,
    compose_today_line,
)

EPHEMERAL = {"type": "ephemeral"}


def _tools(n: int = 3) -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name=f"tool_{i}",
                description=f"tool {i}",
                properties={"x": {"type": "string"}},
                required=["x"],
            )
            for i in range(n)
        ]
    )


def _params(system: str, *, tools=True, messages=None, caching=True):
    context = LLMContext(
        messages=[{"role": "system", "content": system}]
        + (messages or [{"role": "user", "content": "hello"}]),
        tools=_tools() if tools else NOT_GIVEN,
    )
    return DecibylAnthropicLLMAdapter().get_llm_invocation_params(
        context, enable_prompt_caching=caching
    )


def _breakpoints(params) -> int:
    return json.dumps(params, default=str).count('"cache_control"')


def _at(minute: int) -> str:
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 11, 9, minute, tzinfo=tz or ZoneInfo("UTC"))

    with patch(
        "api.services.workflow.pipecat_engine_context_composer.datetime", _Clock
    ):
        return compose_today_line("Asia/Kolkata")


class _Node:
    prompt = "You are a helpful agent."
    add_global_prompt = False
    document_uuids = None


class _Workflow:
    global_node_id = None
    nodes: dict = {}


def _system(today_line: str) -> str:
    return compose_system_prompt_for_node(
        node=_Node(),
        workflow=_Workflow(),
        format_prompt=lambda p: p,
        has_recordings=False,
        today_line=today_line,
        caller="WHO IS CALLING.",
        known_values={},
    )


class TestTheRequest:
    def test_the_system_is_a_marked_text_block(self):
        params = _params(_system(_at(0)))
        stable = params["system"][0]
        assert stable["type"] == "text"
        assert stable["cache_control"] == EPHEMERAL
        assert "You are a helpful agent." in stable["text"]

    def test_the_last_tool_is_marked_and_no_other(self):
        tools = _params(_system(_at(0)))["tools"]
        assert len(tools) == 3
        assert tools[-1]["cache_control"] == EPHEMERAL
        assert all("cache_control" not in t for t in tools[:-1])

    def test_no_tools_no_tool_marker(self):
        params = _params(_system(_at(0)), tools=False)
        assert params["tools"] == []
        assert params["system"][0]["cache_control"] == EPHEMERAL

    def test_the_user_message_markers_are_kept(self):
        params = _params(_system(_at(0)))
        assert params["messages"][-1]["content"][-1]["cache_control"] == EPHEMERAL

    def test_at_most_four_breakpoints_however_long_the_conversation(self):
        messages = []
        for i in range(6):
            messages += [
                {"role": "user", "content": f"q{i}"},
                {"role": "assistant", "content": f"a{i}"},
            ]
        messages.append({"role": "user", "content": "last"})
        params = _params(_system(_at(0)), messages=messages)
        # tools + system + the last two user messages.
        assert _breakpoints(params) == 4

    def test_one_shot_chat_has_three(self):
        assert _breakpoints(_params(_system(_at(0)))) == 3

    def test_caching_off_sends_what_pipecat_sends(self):
        params = _params(_system(_at(0)), caching=False)
        assert isinstance(params["system"], str)
        assert _breakpoints(params) == 0


class TestTheClock:
    def test_two_calls_a_minute_apart_send_the_same_system_block(self):
        first = _params(_system(_at(0)))["system"][0]
        second = _params(_system(_at(1)))["system"][0]
        assert first == second
        assert json.dumps(first) == json.dumps(second)

    def test_the_tools_are_identical_too(self):
        assert _params(_system(_at(0)))["tools"] == _params(_system(_at(1)))["tools"]

    def test_the_model_still_sees_the_time_after_the_cached_block(self):
        a = _params(_system(_at(0)))["system"]
        b = _params(_system(_at(1)))["system"]
        assert len(a) == 2 and "cache_control" not in a[1]
        assert "09:00" in a[1]["text"] and "09:01" in b[1]["text"]
        assert a[1] != b[1]
        assert "Right now it is" in a[1]["text"]
        assert "Right now it is" not in a[0]["text"]
        # Nothing else was lost.
        assert "WHO IS CALLING." in a[0]["text"]

    def test_the_composed_line_is_the_one_the_adapter_finds(self):
        line = compose_today_line("Europe/London")
        assert CLOCK_PARAGRAPH.fullmatch(line)
        assert split_clock(f"a\n\n{line}\n\nb") == ("a\n\nb", line)

    def test_a_system_without_a_clock_is_one_block(self):
        system = "just instructions"
        assert split_clock(system) == (system, None)
        blocks = _params(system)["system"]
        assert len(blocks) == 1 and blocks[0]["text"] == system

    def test_an_operator_sentence_that_resembles_it_is_left_alone(self):
        system = "Right now it is quiet in the shop.\n\nThe rest."
        assert split_clock(system) == (system, None)

    def test_it_does_not_change_the_callers_context(self):
        system = _system(_at(0))
        context = LLMContext(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": "hi"},
            ],
            tools=_tools(),
        )
        before = copy.deepcopy(context.messages)
        DecibylAnthropicLLMAdapter().get_llm_invocation_params(
            context, enable_prompt_caching=True
        )
        assert context.messages == before
        assert re.search(r"Right now it is", system)
