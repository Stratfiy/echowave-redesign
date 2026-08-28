"""Sarvam LLM wrapper: the conversational model, and streaming turned back on.

Two upstream decisions cost roughly six seconds on every turn of every call,
and both are corrected here rather than in the submodule so the fix ships with
the application.

**The model.** ``sarvam-105b`` is a reasoning model. It emits
``reasoning_content`` — chain-of-thought — before a single word of the answer,
on every request, and ``reasoning_effort`` cannot switch it off (Sarvam accepts
only ``low``/``medium``/``high``, and ``low`` measured *slower* than
``medium``). Against a 600-token cap it spent the entire budget thinking and
returned no content at all. Measured on run 77, that stage was 6,045ms of a
7,554ms turn.

``sarvam-105b-conversations`` is the same generation without the reasoning
step: zero ``reasoning_content`` tokens, first content token at 0.24s, and it
still emits ``tool_calls`` — which the workflow engine depends on, since every
node transition is a function call.

**Streaming.** ``SarvamLLMService.get_chat_completions`` forces
``stream=False`` and returns the whole reply as one synthetic chunk. That was
added to stop Sarvam's streaming deltas losing leading whitespace, and on the
reasoning model the concern was real. It also made time-to-first-token equal to
time-to-*whole-response*, and silently cancelled two other optimisations: the
TTS is configured for ``TextAggregationMode.TOKEN`` so it can synthesise while
the LLM is still talking, and Sarvam's ``min_buffer_size`` is tuned for the same
incremental text. Neither has anything to stream when the LLM speaks once, at
the end.

On the conversational model the whitespace fault does not reproduce: a
three-sentence reply streamed back as 60 deltas and 48 correctly separated
words. Usage arrives in the stream too — ``CompletionUsage(completion_tokens=62,
prompt_tokens=30, ...)`` — despite the parent stripping ``stream_options``, so
per-call cost attribution survives the change.

Delegating past ``SarvamLLMService`` reinstates the parent's streaming path
while keeping Sarvam's own parameter cleanup (it drops ``stream_options``,
``max_completion_tokens`` and ``service_tier``, which Sarvam rejects) and its
retry-on-timeout handling.
"""

from pipecat.services.sarvam.llm import SarvamLLMService

#: The conversational model, absent from the upstream allow-list. Unknown names
#: raise in ``_validate_model`` at construction, so the model cannot simply be
#: configured — the set has to be widened before it is selectable.
SARVAM_CONVERSATIONS_MODEL = "sarvam-105b-conversations"


class DecibylSarvamLLMService(SarvamLLMService):
    """Sarvam LLM that can run the conversational model, and streams its reply."""

    _SUPPORTED_MODELS = frozenset(
        SarvamLLMService._SUPPORTED_MODELS | {SARVAM_CONVERSATIONS_MODEL}
    )

    async def get_chat_completions(self, context):
        """Stream the completion, skipping the parent's one-shot override.

        ``super(SarvamLLMService, self)`` rather than a named base class: it
        resolves to whatever sits above ``SarvamLLMService`` in the MRO, so an
        upstream reparenting does not silently route this somewhere else.
        """
        return await super(SarvamLLMService, self).get_chat_completions(context)
