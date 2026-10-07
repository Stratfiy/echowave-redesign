"""What the call pipeline sends to Claude.

The newer Claude models refuse ``temperature`` with a 400, and the factory used
to send ``0.1`` to every one of them -- so a voice agent on Sonnet 5 or Opus 5
failed on its first turn. They take an effort level instead, and on a call that
is the agent's reasoning-effort setting, ``low`` unless somebody raised it.
"""

import pytest
from pipecat.services.anthropic.llm import AnthropicLLMService

from api.services.pipecat import reasoning_effort
from api.services.pipecat.service_factory import create_llm_service_from_provider


def _given(value) -> bool:
    return value is not None and type(value).__name__ not in (
        "NotGiven",
        "_NotGiven",
    )


def _build(model: str, **kwargs) -> AnthropicLLMService:
    service = create_llm_service_from_provider(
        "anthropic", model, "sk-ant-test", **kwargs
    )
    assert isinstance(service, AnthropicLLMService)
    return service


def test_haiku_keeps_its_temperature_and_takes_no_effort():
    settings = _build("claude-haiku-4-5")._settings
    assert settings.temperature == 0.1
    assert "output_config" not in settings.extra


def test_sonnet_5_5_gets_no_sampling_and_low_effort():
    settings = _build("claude-sonnet-5-5", temperature=0.7)._settings
    assert not _given(settings.temperature)
    assert settings.extra["output_config"] == {"effort": "low"}


def test_the_agents_effort_reaches_opus():
    settings = _build("claude-opus-5-5", reasoning_effort="high")._settings
    assert settings.extra["output_config"] == {"effort": "high"}


def test_minimal_is_openais_word_and_becomes_low():
    assert reasoning_effort.claude_effort("minimal") == "low"


def test_which_models_refuse_sampling():
    assert reasoning_effort.claude_rejects_sampling("claude-opus-4-8")
    assert reasoning_effort.claude_rejects_sampling("claude-sonnet-5")
    assert not reasoning_effort.claude_rejects_sampling("claude-haiku-4-5")
    assert not reasoning_effort.claude_rejects_sampling("claude-sonnet-4-6")
    assert reasoning_effort.claude_takes_effort("claude-sonnet-4-6")
    assert not reasoning_effort.claude_takes_effort("claude-haiku-4-5")


# --- Thinking blocks and tool calls on a call --------------------------------


def _context_after_a_thinking_turn(thought_text: str):
    from pipecat.processors.aggregators.llm_context import (
        LLMContext,
        LLMSpecificMessage,
    )

    from api.services.pipecat.anthropic_llm import DecibylAnthropicLLMAdapter

    llm_id = DecibylAnthropicLLMAdapter().id_for_llm_specific_messages
    context = LLMContext(
        messages=[
            {"role": "system", "content": "You answer the phone."},
            {"role": "user", "content": "Book me for four"},
            LLMSpecificMessage(
                llm=llm_id,
                message={"type": "thought", "text": thought_text, "signature": "sig-1"},
            ),
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "book", "arguments": '{"time": "16:00"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "booked"},
        ]
    )
    return context


def test_an_empty_thinking_block_is_not_a_crash():
    """Sonnet 5.5 and Opus 5.5 return thinking with empty text by default.
    pipecat's adapter dropped it unconverted and the next turn raised
    KeyError: 'role' -- dead air for the rest of the call."""
    from api.services.pipecat.anthropic_llm import DecibylAnthropicLLMAdapter

    params = DecibylAnthropicLLMAdapter().get_llm_invocation_params(
        _context_after_a_thinking_turn(""),
        enable_prompt_caching=False,
        system_instruction=None,
    )
    assistant = next(m for m in params["messages"] if m["role"] == "assistant")
    assert "tool_use" in [block["type"] for block in assistant["content"]]
    assert all("role" in m for m in params["messages"])


@pytest.mark.parametrize("thought_text", ["", "Four is free."])
def test_a_thinking_block_is_never_sent_back(thought_text):
    """Found on staging: a research agent's first answer died with
    "Invalid `signature` in `thinking` block. The block is bound to a
    different conversation. Remove the block". A block is bound to the
    conversation as it was when it was written; a node transition or a
    resumed chat changes that, so a replayed block is refused. The API
    accepts the turn without it (and Decibyl's own chat never sends one)."""
    from api.services.pipecat.anthropic_llm import DecibylAnthropicLLMAdapter

    params = DecibylAnthropicLLMAdapter().get_llm_invocation_params(
        _context_after_a_thinking_turn(thought_text),
        enable_prompt_caching=False,
        system_instruction=None,
    )
    kinds = [
        block.get("type")
        for m in params["messages"]
        if isinstance(m.get("content"), list)
        for block in m["content"]
    ]
    assert "thinking" not in kinds
    assert "tool_use" in kinds and "tool_result" in kinds


def test_a_request_with_tools_asks_for_one_call_at_a_time():
    """The stream parser keeps only the last tool_use block, so a second
    call in one reply was silently lost."""
    service = _build("claude-sonnet-5-5")
    context = _context_after_a_thinking_turn("")
    context.set_tools(
        __import__(
            "pipecat.adapters.schemas.tools_schema", fromlist=["ToolsSchema"]
        ).ToolsSchema(
            standard_tools=[
                __import__(
                    "pipecat.adapters.schemas.function_schema",
                    fromlist=["FunctionSchema"],
                ).FunctionSchema(
                    name="book",
                    description="Book a slot",
                    properties={"time": {"type": "string"}},
                    required=["time"],
                )
            ]
        )
    )
    params = service._get_llm_invocation_params(context)
    assert params["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}


def test_claude_is_still_billed_as_anthropic():
    from api.services.billing.usage import provider_from_processor

    assert provider_from_processor("DecibylAnthropicLLMService#0") == "anthropic"
