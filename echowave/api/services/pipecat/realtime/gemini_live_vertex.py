"""Decibyl subclass of pipecat's Gemini Live Vertex AI LLM service.

Diamond inheritance: combines the Decibyl engine-integration overrides from
:class:`DecibylGeminiLiveLLMService` with the Vertex-specific tweaks from
upstream's :class:`GeminiLiveVertexLLMService` (no history config,
``NON_BLOCKING`` tools disabled, service-account credentials).

MRO::

    DecibylGeminiLiveVertexLLMService
      -> DecibylGeminiLiveLLMService
      -> GeminiLiveVertexLLMService
      -> GeminiLiveLLMService
      -> LLMService
      -> ...
"""

from api.services.pipecat.realtime.gemini_live import DecibylGeminiLiveLLMService
from pipecat.services.google.gemini_live.vertex.llm import (
    GeminiLiveVertexLLMService,
)


class DecibylGeminiLiveVertexLLMService(
    DecibylGeminiLiveLLMService,
    GeminiLiveVertexLLMService,
):
    """Vertex AI variant of Gemini Live with Decibyl integration quirks."""

    pass


# Guard against MRO regressions: a future refactor that flips inheritance
# order or breaks the diamond would silently bypass the Decibyl overrides.
_mro = DecibylGeminiLiveVertexLLMService.__mro__
assert _mro[1] is DecibylGeminiLiveLLMService, (
    f"Expected DecibylGeminiLiveLLMService at MRO[1], got {_mro[1]}"
)
assert _mro[2] is GeminiLiveVertexLLMService, (
    f"Expected GeminiLiveVertexLLMService at MRO[2], got {_mro[2]}"
)
del _mro
