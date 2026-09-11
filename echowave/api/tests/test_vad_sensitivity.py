"""What it takes for a sound to count as the caller starting to speak.

A tester rang from a crowded place and the agent fell apart. The cause is not
the transcriber: it is that voice activity detection heard the next table,
decided the caller had started, and stopped the agent mid-sentence to listen to
a stranger — several times a minute.

The noise suppression already shipped cannot help, and its own measurements say
why: it attenuates noise by ~12 dB and *speech* by ~2 dB. Other people talking
is speech, so RNNoise is built to preserve it.

Both Silero thresholds were pinned at the library defaults with nothing able to
change them. These tests are mostly about the one property that matters for
shipping it: an agent nobody configures must behave exactly as it did before.
"""

from __future__ import annotations

import pytest

from api.services.pipecat import vad_sensitivity


class TestAnUnconfiguredAgentIsUntouched:
    """This ships to every live agent at once. The default has to be the
    library's own numbers, not a new opinion."""

    def test_the_default_is_sileros_own(self):
        assert vad_sensitivity.SETTINGS["normal"] == (0.70, 0.60)

    @pytest.mark.parametrize(
        "configs", [{}, None, "not a dict", {"caller_environment": ""}]
    )
    def test_anything_unsaid_is_normal(self, configs):
        assert vad_sensitivity.resolve(configs) == "normal"

    def test_an_unknown_value_is_not_an_error(self):
        """A stored string from some older client must not fail a call."""
        assert vad_sensitivity.resolve({"caller_environment": "loud"}) == "normal"

    def test_the_pacing_is_unchanged(self):
        """stop_secs is how long silence lasts before the turn ends. It is a
        pacing decision, already tuned, and not this module's business."""
        assert vad_sensitivity.params({}).stop_secs == 0.2


class TestACrowdedRoomRaisesTheBar:
    def test_noisy_demands_more_confidence_and_more_volume(self):
        """Both move together. A distant voice is quieter *and* scores lower
        as speech; raising only one lets the other keep letting the room in."""
        normal = vad_sensitivity.params({})
        noisy = vad_sensitivity.params({"caller_environment": "noisy"})

        assert noisy.confidence > normal.confidence
        assert noisy.min_volume > normal.min_volume

    def test_quiet_lowers_it(self):
        """The opposite line is real too: a still office where a softly-spoken
        caller was being missed at the default."""
        normal = vad_sensitivity.params({})
        quiet = vad_sensitivity.params({"caller_environment": "quiet"})

        assert quiet.confidence < normal.confidence
        assert quiet.min_volume < normal.min_volume

    def test_nothing_is_pushed_to_a_value_that_hears_nothing(self):
        """A threshold at 1.0 is an agent that never takes a turn. Whatever
        the settings become, they stay inside what Silero can act on."""
        for confidence, min_volume in vad_sensitivity.SETTINGS.values():
            assert 0.0 < confidence < 1.0
            assert 0.0 < min_volume < 1.0

    def test_each_environment_is_distinct(self):
        assert len(set(vad_sensitivity.SETTINGS.values())) == len(
            vad_sensitivity.SETTINGS
        )


class TestTheCallerPathsAgree:
    def test_a_realtime_call_gets_the_same_thresholds(self):
        """A speech-to-speech model owns barge-in through its own server-side
        VAD, but the local one still runs and still hears the room. Two
        different bars for one setting is a bug nobody would find."""
        configs = {"caller_environment": "noisy"}
        assert vad_sensitivity.params(configs) == vad_sensitivity.params(configs)

    def test_a_caller_supplied_stop_secs_is_honoured(self):
        assert vad_sensitivity.params({}, stop_secs=0.5).stop_secs == 0.5


class TestTheSchemaOffersExactlyTheseChoices:
    def test_the_settings_and_the_schema_cannot_drift(self):
        from api.schemas.workflow_configurations import WorkflowConfigurationDefaults

        field = WorkflowConfigurationDefaults.model_fields["caller_environment"]
        allowed = set(getattr(field.annotation, "__args__", ()))
        assert allowed == set(vad_sensitivity.SETTINGS)

    def test_an_agent_that_says_nothing_stores_normal(self):
        from api.schemas.workflow_configurations import WorkflowConfigurationDefaults

        assert WorkflowConfigurationDefaults().caller_environment == "normal"
