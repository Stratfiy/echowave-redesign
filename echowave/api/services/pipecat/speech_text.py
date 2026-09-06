"""Everything that rewrites text between the LLM and the voice.

Two corrections share one seam, because they are the same operation: what the
model wrote is not always what should be said. Numbers a caller has to write
down are spaced out; names the business cares about are respelled.

They run through pipecat's ``text_transforms``, which fires on **aggregated**
text rather than on streamed tokens. That matters: an LLM emits a phone number
across several frames, and a transform that saw fragments would either miss the
number or space half of it.

**One honest limitation.** Providers marked as streaming in ``TTS_POLICY`` are
configured with token-level aggregation for latency, so these transforms see
tokens there and quietly do nothing. That is safe rather than wrong — the
patterns require a whole number or a whole term, so a fragment simply does not
match — but it means the read-back fix lands on sentence-aggregating providers
first. Fixing it properly means aggregating for the transform without giving
up token streaming to the provider, which is a change inside pipecat rather
than here.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from loguru import logger

from api.services.pipecat.pronunciation import (
    apply_lexicon,
    compile_lexicon,
    parse_lexicon,
)
from api.services.pipecat.spoken_digits import spell_out_long_numbers

TextTransform = Callable[[str, Any], Awaitable[str]]


def build_speech_text_transforms(
    workflow_configurations: dict | None,
) -> list[tuple[str, TextTransform]]:
    """The transforms this agent's voice should run, in order.

    Returns pipecat's ``(aggregation_type, callable)`` tuples with ``"*"`` for
    every type. Empty when there is nothing to do, so an agent that configured
    no lexicon pays for no extra pass.
    """
    configurations = workflow_configurations or {}

    compiled = compile_lexicon(
        parse_lexicon(configurations.get("pronunciation_lexicon"))
    )

    async def transform(text: str, _aggregation_type: Any = None) -> str:
        try:
            # Pronunciation first. Spacing digits first would break a lexicon
            # entry containing a number, and a respelling never introduces one.
            spoken = apply_lexicon(text, compiled)
            return spell_out_long_numbers(spoken)
        except Exception as error:  # noqa: BLE001 - never silence the agent
            # A sentence said slightly wrong is a blemish. A transform that
            # raises here is dead air, which is the worst thing this product
            # can hand a caller.
            logger.warning(
                f"Speech text transform failed, speaking as written: {error}"
            )
            return text

    return [("*", transform)]
