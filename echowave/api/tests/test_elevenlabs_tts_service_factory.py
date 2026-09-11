"""ElevenLabs is fed whole sentences, and says so explicitly.

The policy table marks ElevenLabs as streaming, which would hand it tokens. In
token mode pipecat must leave ElevenLabs' server-side scheduler on, and pipecat
exposes no chunk schedule to tune it, so ElevenLabs' documented default of
``[120, 160, 250, 290]`` applies: the first byte arrives after 120 characters
have accumulated. Twenty words. It is the 700 ms this voice measures on the
Sales Assistant.

A sentence is usually shorter than that, and sending one lets pipecat derive
``auto_mode=True`` -- what ElevenLabs built auto mode for. It also makes the
speech-text transforms run: they act on aggregated text and quietly do nothing
on tokens, so on the Global tier every registration number and respelled name
was going to the voice unfixed.
"""

from types import SimpleNamespace
from unittest.mock import patch

from pipecat.services.tts_service import TextAggregationMode

from api.services.configuration.registry import ServiceProviders
from api.services.pipecat.service_factory import create_tts_service


def _build(**tts):
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.ELEVENLABS.value,
            api_key="test-key",
            model="eleven_flash_v2_5",
            voice="21m00Tcm4TlvDq8ikWAM",
            speed=1.0,
            **tts,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=16000, transport_in_sample_rate=16000
    )
    with patch(
        "api.services.pipecat.service_factory.ElevenLabsTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)
    return mock_service.call_args.kwargs


class TestElevenLabsIsFedSentences:
    def test_the_call_site_says_sentence_and_overrides_the_policy(self):
        kwargs = _build()
        assert kwargs["text_aggregation_mode"] is TextAggregationMode.SENTENCE

    def test_auto_mode_is_left_for_pipecat_to_derive(self):
        """Pipecat derives auto_mode from the aggregation mode, and derives
        True for anything but tokens. Passing it explicitly would be a second
        source of truth for one decision."""
        kwargs = _build()
        assert "auto_mode" not in kwargs

    def test_the_voice_settings_still_arrive(self):
        kwargs = _build(stability=0.6, similarity_boost=0.9, style=0.1)
        settings = kwargs["settings"]
        assert settings.voice == "21m00Tcm4TlvDq8ikWAM"
        assert settings.model == "eleven_flash_v2_5"
        assert settings.stability == 0.6
        assert settings.similarity_boost == 0.9
