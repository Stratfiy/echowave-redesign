from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_MAX_CALL_DURATION_SECONDS = 300
# Hard ceiling on configurable call duration. Must stay <= the concurrency
# rate limiter's stale_call_timeout (20 min): a call running past that has
# its slot purged as stale and the org concurrency limit under-counts.
MAX_CALL_DURATION_SECONDS = 1200
DEFAULT_MAX_USER_IDLE_TIMEOUT_SECONDS = 10.0
DEFAULT_SMART_TURN_STOP_SECS = 2.0
DEFAULT_TURN_START_STRATEGY = "default"
DEFAULT_TURN_START_MIN_WORDS = 3
DEFAULT_PROVISIONAL_VAD_PAUSE_SECS = 1.5
# "transcription", on measurement rather than on the argument below.
#
# The argument for "turn_analyzer" is good and it is still here because it may
# well be right on another stack: a local model asks whether the utterance
# *sounds* finished, so it is quick on a completed sentence and patient on an
# ambiguous one, where "transcription" waits a fixed timer either way.
#
# It is not what happens here. From `call_turn_metrics`, the endpointing stage
# (`t_endpoint_fired_ms - t_user_stopped_ms`) measures:
#
#     transcription    1171-1174ms, across 40 turns of 15 calls
#     turn_analyzer    1311-1312ms, runs 77 and 78
#
# Two things follow. The stage never cost the 600ms this comment used to claim
# — that figure was VAD stop_secs plus user_speech_timeout added up from
# reading the code, and the measured stage is ~1172ms whichever strategy runs.
# What actually dominates it is Sarvam STT finalisation: the turn is not
# released until the final transcript lands, and no turn-stop strategy can
# return time the STT has not finished spending. Against that floor the
# analyzer's own inference is pure addition, and it reads as a steady +139ms.
#
# So the real gain on this stage is not here. An STT that emits its own turn
# boundaries (Deepgram Flux, Cartesia ink-2) removes the wait rather than
# tuning it — see `_create_non_realtime_user_turn_stop_strategies`, where such
# a model skips this budget entirely.
#
# "turn_analyzer" stays selectable, and the dependency it needs is installed
# (api/Dockerfile carries pipecat's local-smart-turn-v3 extra). A workflow on a
# faster-finalising STT is exactly where it should win.
DEFAULT_TURN_STOP_STRATEGY = "transcription"
# How long the turn waits after the VAD reports silence, in case the caller was
# only drawing breath. Paid on every turn of the default "transcription"
# strategy, on top of the VAD's own 0.2s — so it sets a floor under perceived
# latency that no faster model downstream can recover.
#
# 0.4 rather than the pipecat library default of 0.6: two fifths of a second is
# still a real pause by the standards of a phone call, and the 200ms saved is
# larger than the entire time-to-first-byte of most speech synthesis. Raise it
# for a workflow whose callers read out numbers or think mid-sentence; lower it
# for short scripted confirmations.
DEFAULT_USER_SPEECH_TIMEOUT = 0.4
# Off by default, and off means the processor is never built into the pipeline
# at all -- an agent nobody configured this on runs exactly the frames it ran
# before. The pause only helps where an interruption was brief; everywhere else
# the caller finishing, the turn being detected, the model answering and speech
# being synthesised already take longer than any sensible value here.
DEFAULT_INTERRUPTION_BACKOFF_SECS = 0.0
DEFAULT_CONTEXT_COMPACTION_ENABLED = False


class FallbackServiceConfiguration(BaseModel):
    """One backup in an ordered chain, tried when the one before it fails.

    Deliberately thin. A backup is chosen to keep a live call alive, not to be
    tuned -- so it names a provider and, where the provider needs them, a model
    and a voice, and takes the provider's defaults for everything else. The key
    comes from the account's vault at dial time, so a backup can only be a
    provider this account can actually authenticate to.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = ""
    voice: str = ""
    language: str = ""


class AmbientNoiseConfigurationDefaults(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    volume: float = 0.3


class WorkflowConfigurationDefaults(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _treat_null_as_unset(cls, data):
        # Stored configs (and older clients) carry explicit JSON nulls for
        # keys the user never configured; dropping them lets the field
        # defaults apply instead of failing validation.
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data

    ambient_noise_configuration: AmbientNoiseConfigurationDefaults = Field(
        default_factory=AmbientNoiseConfigurationDefaults
    )
    max_call_duration: int = Field(
        default=DEFAULT_MAX_CALL_DURATION_SECONDS,
        gt=0,
        le=MAX_CALL_DURATION_SECONDS,
    )
    max_user_idle_timeout: float = DEFAULT_MAX_USER_IDLE_TIMEOUT_SECONDS
    smart_turn_stop_secs: float = DEFAULT_SMART_TURN_STOP_SECS
    # "default" resolves per transcriber: an STT that emits its own turn
    # boundaries owns interruption, otherwise a minimum word count does. "vad"
    # is that older raw-voice-activity fallback, kept selectable for a
    # transcriber that emits no interim results -- see
    # _create_non_realtime_user_turn_start_strategies.
    turn_start_strategy: Literal["default", "min_words", "provisional_vad", "vad"] = (
        DEFAULT_TURN_START_STRATEGY
    )
    turn_start_min_words: int = DEFAULT_TURN_START_MIN_WORDS
    provisional_vad_pause_secs: float = DEFAULT_PROVISIONAL_VAD_PAUSE_SECS
    turn_stop_strategy: Literal["transcription", "turn_analyzer"] = (
        DEFAULT_TURN_STOP_STRATEGY
    )
    # Only applies to the "transcription" strategy. A turn analyzer decides for
    # itself, and an STT that emits its own turn boundaries never waits at all.
    user_speech_timeout: float = Field(
        default=DEFAULT_USER_SPEECH_TIMEOUT, ge=0.15, le=3.0
    )
    dictionary: str = ""
    interruption_backoff_secs: float = Field(
        default=DEFAULT_INTERRUPTION_BACKOFF_SECS, ge=0.0, le=3.0
    )
    context_compaction_enabled: bool = DEFAULT_CONTEXT_COMPACTION_ENABLED
    # Ordered backups, tried in turn when the service in front of them reports
    # a non-fatal error mid-call. Empty means what it always did: one provider,
    # and a failure the caller hears as dead air.
    #
    # Capped because each backup is a live connection held open for a failure
    # that usually never comes; past a couple the cost is certain and the
    # benefit is not.
    fallback_tts: list[FallbackServiceConfiguration] = Field(
        default_factory=list, max_length=2
    )
    fallback_stt: list[FallbackServiceConfiguration] = Field(
        default_factory=list, max_length=2
    )


def get_default_workflow_configurations() -> WorkflowConfigurationDefaults:
    return WorkflowConfigurationDefaults()
