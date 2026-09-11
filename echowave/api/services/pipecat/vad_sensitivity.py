"""How loud and how certain a sound must be before the agent treats it as the caller.

A tester rang one of these agents from a crowded place and it fell apart. The
noise suppression we ship is RNNoise, and this module's own measurements say
exactly why it cannot help: it attenuates noise by ~12 dB and speech by ~2 dB.
**Other people talking is speech.** RNNoise is built to preserve it, so a room
full of voices arrives at the transcriber essentially untouched.

What goes wrong then is not mainly mis-transcription, it is *turn-taking*. Voice
activity detection hears the next table, decides the caller has started
speaking, and the agent stops mid-sentence to listen to a stranger. Then the
transcriber is handed someone else's words and the model answers them. One
crowded call produces this several times a minute and the caller gives up.

Silero already scores every frame with a confidence and a volume, and both
thresholds were hardcoded at the library's defaults with nothing able to change
them. Raising them is the cheapest thing that works: distant chatter is quieter
and less confidently speech than the person holding the phone, so a higher bar
sorts the two without any new vendor, model or millisecond.

**What this does not do.** It changes what counts as the *start* of a turn. Once
the caller is genuinely speaking the microphone is open and whatever else is in
the room goes to the transcriber with them. Separating one voice from a crowd
mid-utterance is a different problem and needs a model trained for it -- Krisp
VIVA or ai-coustics, both licensed SDKs this deployment does not carry. This
narrows the failure; it does not close it, and it should not be sold as if it
did.
"""

from __future__ import annotations

from typing import Any

from pipecat.audio.vad.vad_analyzer import VADParams

#: The per-agent key in ``workflow_configurations``.
CONFIG_KEY = "caller_environment"

#: Kept out of the settings this maps to: how long silence must last before the
#: turn ends is a pacing decision, not a noise one, and it is already tuned.
STOP_SECS = 0.2

#: Confidence and minimum volume per environment.
#:
#: ``normal`` is Silero's own default (0.7 / 0.6), so an agent nobody configures
#: behaves exactly as every agent did before this existed.
#:
#: ``noisy`` is the one this was built for. Both numbers move: a distant voice
#: is quieter *and* scores lower as speech, and raising only one lets the other
#: keep letting the room in.
#:
#: ``quiet`` exists because the opposite line is real too -- a landline in a
#: still office, where a softly-spoken caller was being missed at the default.
SETTINGS: dict[str, tuple[float, float]] = {
    "quiet": (0.60, 0.50),
    "normal": (0.70, 0.60),
    "noisy": (0.85, 0.75),
}

DEFAULT_ENVIRONMENT = "normal"


def resolve(run_configs: Any) -> str:
    """Which environment this agent is set up for. ``normal`` unless told."""
    if not isinstance(run_configs, dict):
        return DEFAULT_ENVIRONMENT
    chosen = str(run_configs.get(CONFIG_KEY) or "").strip().lower()
    return chosen if chosen in SETTINGS else DEFAULT_ENVIRONMENT


def params(run_configs: Any, *, stop_secs: float = STOP_SECS) -> VADParams:
    """The voice-detection thresholds for this call.

    ``stop_secs`` stays a parameter because the realtime path and the cascade
    path each pass their own, and neither should inherit the other's pacing
    from a module about noise.
    """
    confidence, min_volume = SETTINGS[resolve(run_configs)]
    return VADParams(confidence=confidence, stop_secs=stop_secs, min_volume=min_volume)
