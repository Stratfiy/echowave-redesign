"""Sarvam's voice, fed a clause at a time.

Sarvam is deliberately given whole sentences rather than tokens: token feeding
handed it two or three Tamil words with no context and the words came out
clipped, and clarity is the product. The cost of that choice is the wait for
the first sentence, which with a brain like gpt-5-mini is often thirty words.

The sentence aggregator is not a constructor argument in pipecat; the base
service builds a ``SimpleTextAggregator`` for itself. So the swap happens here,
after the parent has set itself up, the same way Cartesia's and Rime's own
services swap theirs. Everything else about the service is inherited unchanged.

The class name matters to billing. Usage keys carry the processor's class
name, and ``provider_from_processor`` maps ``decibylsarvam`` to ``sarvam``
so this subclass prices at Sarvam's rate rather than silently at nothing.
"""

from __future__ import annotations

from api.services.pipecat.clause_aggregator import ClauseTextAggregator
from pipecat.services.sarvam.tts import SarvamTTSService


class DecibylSarvamTTSService(SarvamTTSService):
    """``SarvamTTSService`` with clause boundaries added to its sentence feed."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._text_aggregator = ClauseTextAggregator(
            aggregation_type=self._text_aggregation_mode
        )
