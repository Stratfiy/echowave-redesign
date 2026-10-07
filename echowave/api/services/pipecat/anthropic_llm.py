"""Claude on a call, with two fixes pipecat's Anthropic service needs.

**Empty thinking.** Claude Sonnet 5.5 and Opus 5.5 always think, and by
default return each thinking block with empty text and a signature. The
aggregator stores that as ``{"type": "thought", "text": "", "signature": ...}``;
pipecat's adapter only converts a thought whose text is truthy, so the empty
one fell through as a role-less dict and the next turn raised ``KeyError:
'role'`` -- after the first reply that thought, the caller heard nothing
again. A thought with a signature is converted whatever its text, and one with
no signature (which the API could not accept back anyway) is dropped.

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
            signature = body.get("signature")
            if not signature:
                # Unsigned thinking cannot be sent back. An empty assistant
                # turn merges into the reply that follows it.
                return {"role": "assistant", "content": []}
            return {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": body.get("text") or "",
                        "signature": signature,
                    }
                ],
            }
        return super()._from_anthropic_specific_message(message)

    def get_llm_invocation_params(self, *args, **kwargs):
        params = super().get_llm_invocation_params(*args, **kwargs)
        # An assistant turn that was only an unsigned thought is empty and,
        # if nothing merged into it, not a legal message.
        params["messages"] = [
            m for m in params["messages"] if m.get("content") not in ([], None, "")
        ]
        return params


class DecibylAnthropicLLMService(AnthropicLLMService):
    adapter_class = DecibylAnthropicLLMAdapter

    def _get_llm_invocation_params(self, context: LLMContext):
        params = super()._get_llm_invocation_params(context)
        if params.get("tools"):
            params["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        return params
