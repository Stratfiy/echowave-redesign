"""OpenAI's speech options.

OpenAI publishes eleven voices for its speech models and, unlike Sarvam or
Rumik, publishes **no gender for any of them**. That absence is honoured here
rather than filled in: the names are deliberately ambiguous, and guessing from
one would be both wrong and rude -- the same reason ``voice_catalogue`` carries
Sarvam's own gender labels instead of inferring them.

A voice with no gender is not a voice that cannot be offered. The picker groups
by gender where it knows it and lists the rest together, so these appear and
can be heard.
"""

#: The speech models. ``tts-1`` is the one on sale today; the others are what
#: OpenAI's own docs name for the same endpoint.
OPENAI_TTS_MODELS = ("gpt-4o-mini-tts", "tts-1", "tts-1-hd")

#: Every voice the speech endpoint accepts, in OpenAI's own order. Gender is
#: absent from their documentation and therefore absent here.
OPENAI_TTS_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
)
