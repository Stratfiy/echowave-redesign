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
    DEFAULT_TURN_STOP_STRATEGY,
    DEFAULT_USER_SPEECH_TIMEOUT,
    WorkflowConfigurationDefaults,
)
from api.services.pipecat.run_pipeline import (
    _create_non_realtime_user_turn_stop_strategies as build_stop,
)
from api.services.pipecat.run_pipeline import (
    _resolve_user_speech_timeout,
)


def _strategy_types(run_configs: dict):
    return [type(s) for s in build_stop(run_configs, uses_external_turns=False)]


def _expected_for(strategy: str):
    """The strategy types `build_stop` yields for a strategy named explicitly.

    Types rather than instances: the strategy objects define no `__eq__`, so
    comparing them directly compares identity and passes only by accident.
    Comparing against a freshly built pair means the assertion tracks the
    default wherever it moves, instead of encoding today's choice.
    """
    return _strategy_types({"turn_stop_strategy": strategy})


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

    def test_a_workflow_that_configured_nothing_gets_the_default_strategy(self):
        """The case every workflow in production actually hits.

        `run_configs` is the stored JSON rather than a validated
        `WorkflowConfigurationDefaults`, and `create_workflow` persists `{}` —
        so an unconfigured workflow reaches the pipeline with no
        `turn_stop_strategy` key at all. Read with a bare `.get()` it took the
        silence-timeout path whatever the schema said, which made the declared
        default unreachable.

        Asserted against the constant rather than against a named strategy: the
        point is that the two agree, not which one currently wins. The default
        has already moved once on measurement and may move again.
        """
        assert _strategy_types({}) == _expected_for(DEFAULT_TURN_STOP_STRATEGY)

    def test_an_explicit_null_is_treated_as_unset_rather_than_as_a_choice(self):
        """Older clients send nulls for keys nobody configured. A null must
        take the default rather than being read as a choice."""
        assert _strategy_types({"turn_stop_strategy": None}) == _expected_for(
            DEFAULT_TURN_STOP_STRATEGY
        )

    def test_transcription_remains_selectable_for_a_workflow_that_wants_it(self):
        """Flipping the default must not remove the choice. A workflow whose
        callers pause mid-sentence can still buy the fixed grace period."""
        strategies = build_stop(
            {"turn_stop_strategy": "transcription", "user_speech_timeout": 0.5},
            uses_external_turns=False,
        )

        assert strategies[0]._user_speech_timeout == 0.5

    def test_the_default_constant_is_the_one_the_pipeline_reads(self):
        """Guards the specific decoupling that caused this: a default declared
        in the schema, never imported by the code that branches on it."""
        from api.services.pipecat import run_pipeline

        assert run_pipeline.DEFAULT_TURN_STOP_STRATEGY == DEFAULT_TURN_STOP_STRATEGY


class TestTheSTTSafetyNetIsTheLargestStage:
    """`turn_detect` was 1171-1174ms across 40 turns of 15 calls, +/-1ms.

    A stage that constant is not measuring work. It is
    `SpeechTimeoutUserTurnStopStrategy`'s second timer: a safety net sized from
    the STT's published P99 time-to-final-segment, which the turn waits out
    whenever the STT does not mark a transcript `finalized`. Sarvam never does,
    so every turn paid the full published figure.

        turn_detect = stop_secs + max(user_speech_timeout, ttfs_p99 - stop_secs)

    Pipecat publishes SARVAM_TTFS_P99 = 1.17, which is the measurement, exactly.
    """

    @staticmethod
    def _turn_detect(ttfs_p99: float, stop_secs: float = 0.2) -> float:
        from api.schemas.workflow_configurations import DEFAULT_USER_SPEECH_TIMEOUT

        return stop_secs + max(
            DEFAULT_USER_SPEECH_TIMEOUT, max(0.0, ttfs_p99 - stop_secs)
        )

    def test_the_published_sarvam_figure_reproduces_the_measurement(self):
        from pipecat.services.stt_latency import SARVAM_TTFS_P99

        assert round(self._turn_detect(SARVAM_TTFS_P99) * 1000) == 1170

    def test_the_configured_value_costs_what_upstreams_deepgram_costs(self):
        """Dograh defaults transcription to Deepgram, whose published 0.35
        collapses this timer under the speech timeout entirely. Matching that
        is the whole of the regression from moving to Sarvam."""
        from pipecat.services.stt_latency import DEEPGRAM_TTFS_P99

        from api.services.pipecat.service_factory import sarvam_stt_ttfs_p99

        assert self._turn_detect(sarvam_stt_ttfs_p99()) == self._turn_detect(
            DEEPGRAM_TTFS_P99
        )

    def test_the_configured_value_is_well_under_the_published_default(self):
        from pipecat.services.stt_latency import SARVAM_TTFS_P99

        from api.services.pipecat.service_factory import sarvam_stt_ttfs_p99

        assert sarvam_stt_ttfs_p99() < SARVAM_TTFS_P99

    def test_an_operator_can_retune_it_without_a_release(self, monkeypatch):
        """It is a starting point, not a measurement. Once it stops dominating,
        `turn_detect` reports Sarvam's real latency and that number should
        replace it."""
        from api.services.pipecat import service_factory

        monkeypatch.setenv("SARVAM_STT_TTFS_P99", "0.75")
        assert service_factory.sarvam_stt_ttfs_p99() == 0.75

    def test_a_nonsense_override_falls_back_rather_than_crashing_the_call(
        self, monkeypatch
    ):
        from api.services.pipecat import service_factory

        monkeypatch.setenv("SARVAM_STT_TTFS_P99", "soon")
        assert (
            service_factory.sarvam_stt_ttfs_p99()
            == service_factory.SARVAM_STT_TTFS_P99_DEFAULT
        )

    def test_a_negative_override_is_floored_rather_than_inverted(
        self, monkeypatch
    ):
        from api.services.pipecat import service_factory

        monkeypatch.setenv("SARVAM_STT_TTFS_P99", "-3")
        assert service_factory.sarvam_stt_ttfs_p99() == 0.0
