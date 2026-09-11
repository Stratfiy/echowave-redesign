from types import SimpleNamespace
from unittest.mock import patch

from api.services.configuration.registry import (
    CARTESIA_TTS_MODELS,
    CartesiaTTSConfiguration,
    ServiceProviders,
)
from api.services.pipecat.service_factory import create_tts_service


def test_cartesia_tts_configuration_defaults_to_sonic_3_5():
    config = CartesiaTTSConfiguration(api_key="test-key")

    assert config.provider == ServiceProviders.CARTESIA
    assert config.model == "sonic-3.5"
    assert CARTESIA_TTS_MODELS == ["sonic-3.5", "sonic-3"]


def test_create_cartesia_tts_service_passes_selected_model():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.CARTESIA.value,
            api_key="test-key",
            model="sonic-3.5",
            voice="test-voice-id",
            speed=1.0,
            volume=1.0,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.CartesiaTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    assert mock_service.call_count == 1
    kwargs = mock_service.call_args.kwargs
    assert kwargs["api_key"] == "test-key"
    assert kwargs["settings"].model == "sonic-3.5"
    assert kwargs["settings"].voice == "test-voice-id"


def test_cartesia_tts_configuration_default_language_is_english():
    config = CartesiaTTSConfiguration(api_key="test-key")

    assert config.language == "en"


def test_create_cartesia_tts_service_passes_language_to_settings():
    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.CARTESIA.value,
            api_key="test-key",
            model="sonic-3.5",
            voice="test-voice-id",
            speed=1.0,
            volume=1.0,
            language="tr",
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )

    with patch(
        "api.services.pipecat.service_factory.CartesiaTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["settings"].language == "tr"


def test_create_cartesia_tts_service_names_its_buffer_delay():
    """In token mode pipecat leaves ``max_buffer_delay_ms`` unset so that
    Cartesia's server default applies -- and that default is 3000 ms. Three
    seconds of silence after the brain has started answering, on a vendor
    connected the same day. The factory names it instead."""
    from api.services.pipecat.service_factory import CARTESIA_MAX_BUFFER_DELAY_MS

    user_config = SimpleNamespace(
        tts=SimpleNamespace(
            provider=ServiceProviders.CARTESIA.value,
            api_key="test-key",
            model="sonic-3.5",
            voice="test-voice-id",
            speed=1.0,
            volume=1.0,
        )
    )
    audio_config = SimpleNamespace(
        transport_out_sample_rate=24000,
        transport_in_sample_rate=16000,
    )
    with patch(
        "api.services.pipecat.service_factory.CartesiaTTSService"
    ) as mock_service:
        create_tts_service(user_config, audio_config)

    kwargs = mock_service.call_args.kwargs
    assert kwargs["max_buffer_delay_ms"] == CARTESIA_MAX_BUFFER_DELAY_MS
    # Short enough that a caller does not hear it as a pause; nonzero so
    # Cartesia still smooths token boundaries rather than stacking buffers.
    assert 0 < CARTESIA_MAX_BUFFER_DELAY_MS <= 500
