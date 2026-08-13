"""How long a turn waits before anything downstream starts.

This is the largest single stage of a turn and the one nothing downstream can
recover: a faster model cannot give back time already spent sitting in silence.
Three strategies pay wildly different amounts for it, and which one a workflow
gets is decided by its STT model rather than by anything named "latency".

The default path waits out the VAD's 0.2s and then ``user_speech_timeout`` on
every turn. That second timer was previously the library default of 0.6s with
nothing able to configure it — so every workflow paid it and none could trade
it against the risk of cutting a hesitant caller off.
"""

import pytest

from api.schemas.workflow_configurations import (
    DEFAULT_USER_SPEECH_TIMEOUT,
    WorkflowConfigurationDefaults,
)
from api.services.pipecat.run_pipeline import (
    _create_non_realtime_user_turn_stop_strategies as build_stop,
)
from api.services.pipecat.run_pipeline import (
    _resolve_user_speech_timeout,
)


class TestTheWaitIsConfigurable:
    def test_the_default_is_shorter_than_the_library_default(self):
        """0.4 against pipecat's 0.6. The 200ms saved is larger than the whole
        time-to-first-byte of most speech synthesis — it is not a rounding
        adjustment, it is the biggest single win available on this path."""
        assert DEFAULT_USER_SPEECH_TIMEOUT == 0.4

    def test_a_workflow_can_raise_it_for_hesitant_callers(self):
        """A caller reading out an account number pauses mid-utterance. Being
        cut off there costs the whole turn, which is worth far more than the
        fraction of a second the shorter wait saves."""
        assert _resolve_user_speech_timeout({"user_speech_timeout": 1.2}) == 1.2

    def test_it_is_floored_rather_than_free(self):
        """Below roughly 150ms the timer stops being a grace period for someone
        drawing breath and starts ending turns inside their sentences."""
        assert _resolve_user_speech_timeout({"user_speech_timeout": 0.0}) == 0.15
        assert _resolve_user_speech_timeout({"user_speech_timeout": -5}) == 0.15

    def test_an_unset_value_takes_the_default(self):
        assert _resolve_user_speech_timeout({}) == DEFAULT_USER_SPEECH_TIMEOUT

    def test_the_schema_rejects_a_value_that_would_break_conversation(self):
        """Bounded in the schema as well as the resolver: the resolver protects
        the pipeline, the schema stops the value being stored at all."""
        with pytest.raises(ValueError):
            WorkflowConfigurationDefaults(user_speech_timeout=0.01)
        with pytest.raises(ValueError):
            WorkflowConfigurationDefaults(user_speech_timeout=30)

    def test_the_default_survives_a_config_that_never_set_it(self):
        """Stored configs predate the field, and older clients send explicit
        nulls for keys nobody configured."""
        assert (
            WorkflowConfigurationDefaults().user_speech_timeout
            == DEFAULT_USER_SPEECH_TIMEOUT
        )
        assert (
            WorkflowConfigurationDefaults(user_speech_timeout=None).user_speech_timeout
            == DEFAULT_USER_SPEECH_TIMEOUT
        )


class TestTheStrategyMatchesTheConfiguration:
    def test_the_timeout_reaches_the_strategy(self):
        """The point of the whole change. A configured value that never leaves
        the config object is the bug this is guarding against."""
        strategies = build_stop(
            {"user_speech_timeout": 0.25}, uses_external_turns=False
        )

        assert strategies[0]._user_speech_timeout == 0.25

    def test_an_stt_that_owns_turns_waits_on_no_silence_at_all(self):
        """Deepgram Flux and Cartesia ink-2 decide a turn ended from acoustic
        and semantic context. The entire VAD-plus-timeout budget disappears,
        which is worth more than any tuning of the other paths."""
        strategies = build_stop({"user_speech_timeout": 2.0}, uses_external_turns=True)

        assert not hasattr(strategies[0], "_user_speech_timeout")

    def test_the_turn_analyzer_path_ignores_the_speech_timeout(self):
        """It ends the turn when the utterance sounds complete, so the fixed
        timer plays no part — configuring one there would imply a control that
        does nothing."""
        strategies = build_stop(
            {"turn_stop_strategy": "turn_analyzer", "user_speech_timeout": 0.2},
            uses_external_turns=False,
        )

        assert not hasattr(strategies[0], "_user_speech_timeout")
