"""Claude on a call, with two fixes pipecat's Anthropic service needs.

**Thinking is not sent back.** Claude Sonnet 5.5 and Opus 5.5 always think.
The aggregator stores each block as ``{"type": "thought", ...}``; pipecat's
adapter only converted one with text, so an empty one fell through as a
role-less dict and the next turn raised ``KeyError: 'role'``. Sending signed
blocks back fixed that and broke something else: a block is bound to the
conversation as it stood when it was written, and a node transition or a
resumed chat changes that, so the API refused the turn ("Invalid `signature`
in `thinking` block. The block is bound to a different conversation. Remove
the block") -- a research agent's first answer died on it on staging. The API
takes the turn without the block, as Decibyl's own chat has always sent it,
so no thought is sent back.

**Parallel tool calls.** The stream parser keeps a single ``tool_use`` block,
overwritten on each new one, so when Claude called two tools in one reply only
the last ran -- an extraction lost, or a step change that never happened.
Until the parser tracks blocks by index, requests that carry tools ask for one
call at a time (``disable_parallel_tool_use`` with ``auto``, which every
current model accepts; forced tool choice is refused by the 5.5 models).

Both belong upstream in the pipecat fork; this subclass keeps them in the app
until they land there.
"""

from __future__ import annotations

from typing import Any

from pipecat.adapters.services.anthropic_adapter import AnthropicLLMAdapter
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.anthropic.llm import AnthropicLLMService


class DecibylAnthropicLLMAdapter(AnthropicLLMAdapter):
    def _from_anthropic_specific_message(self, message) -> Any:
        body = message.message
        if isinstance(body, dict) and body.get("type") == "thought":
            # Never sent back (see the module note). An empty assistant turn
            # merges into the reply that follows it.
            return {"role": "assistant", "content": []}
        return super()._from_anthropic_specific_message(message)

    def get_llm_invocation_params(self, *args, **kwargs):
        params = super().get_llm_invocation_params(*args, **kwargs)
        # An assistant turn that was only a thought is empty and,
        # if nothing merged into it, not a legal message.
        params["messages"] = [
            m for m in params["messages"] if m.get("content") not in ([], None, "")
        ]
        if not params["messages"]:
            # An opening turn with instructions and nothing said yet (a task
            # graph has no greeting): Claude refuses a request with no
            # message at all, so the turn starts with one.
            params["messages"] = [{"role": "user", "content": "Begin."}]
        return params


class DecibylAnthropicLLMService(AnthropicLLMService):
    adapter_class = DecibylAnthropicLLMAdapter

    def _get_llm_invocation_params(self, context: LLMContext):
        params = super()._get_llm_invocation_params(context)
        if params.get("tools"):
            params["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        return params


class DecibylAnthropicAWSLLMService(DecibylAnthropicLLMService):
    """The same service on Claude Platform on AWS (stream aws-gateway).

    Built with the SDK's AWS client by the factory; nothing else differs.
    A class of its own because usage is priced by the processor's class
    name, and a call on AWS is billed through AWS (``anthropic_aws``), not
    on Anthropic's first-party invoice.
    """
