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

**Streaming stays off, and the reason is tool calls rather than whitespace.**
``SarvamLLMService.get_chat_completions`` forces ``stream=False`` and rebuilds
the reply as one synthetic chunk. Read only its own docstring and it looks like
a cosmetic guard against Sarvam's deltas losing leading whitespace -- well worth
trading for the seconds a one-shot completion costs. It is not. The same method
also stamps an ``index`` onto every tool call, which OpenAI-style streaming
aggregation needs to assemble a call from its deltas and which Sarvam does not
supply. Its commit says so in the title: "Preserve spaces *and tool calls*".

Every node transition in a workflow is a tool call. Restoring streaming here
therefore does not merely risk joined words -- transitions stop resolving, and
the agent falls silent on the first turn that should move it to another node.
Observed on runs 78-80, each recording exactly one turn where runs 75-76
recorded five. It was missed because the two halves were tested separately:
tool calls against a non-streaming call, streaming against a call with no tools.

The cost is real and now paid deliberately: time-to-first-token is
time-to-whole-response, and the TTS's ``TextAggregationMode.TOKEN`` and
``min_buffer_size`` tuning have nothing to stream. Reclaiming it means
assembling the tool-call deltas with an index of our own, not removing the
guard. The model change below is the larger half of the win regardless --
6,045ms to ~1,000ms -- and it is independent of this.
"""

from pipecat.services.sarvam.llm import SarvamLLMService

#: The conversational model, absent from the upstream allow-list. Unknown names
#: raise in ``_validate_model`` at construction, so the model cannot simply be
#: configured — the set has to be widened before it is selectable.
SARVAM_CONVERSATIONS_MODEL = "sarvam-105b-conversations"


class DecibylSarvamLLMService(SarvamLLMService):
    """Sarvam LLM that can run the conversational model.

    Deliberately does *not* override ``get_chat_completions``: the parent's
    one-shot completion is what keeps tool calls -- and therefore node
    transitions -- working. See the module docstring.
    """

    _SUPPORTED_MODELS = frozenset(
        SarvamLLMService._SUPPORTED_MODELS | {SARVAM_CONVERSATIONS_MODEL}
    )
