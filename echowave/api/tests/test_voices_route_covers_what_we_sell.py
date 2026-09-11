"""The voice route has to answer for every voice we actually sell.

The model picker asks this route for the chosen model's voices. It was guarded
by a hand-written ``Literal`` of six providers, and the catalogue had long since
outgrown it: OpenAI, Rumik, Google and Smallest are all on sale and all answered
422. The picker treated that as "keep what you had", so choosing OpenAI showed
*ElevenLabs* voices, silently, with no error anywhere a user could see.

A list maintained by hand beside a list that grows is a bug with a delay on it.
This one reads the registry, so the two cannot drift.
"""

from __future__ import annotations

import pytest

from api.routes.user import _tts_providers
from api.services.configuration import voice_catalogue


class TestEveryProviderWeSpeakWithIsAskable:
    def test_the_route_knows_what_the_registry_knows(self):
        from api.services.configuration.registry import REGISTRY, ServiceType

        assert _tts_providers() == {
            getattr(key, "value", str(key)) for key in REGISTRY[ServiceType.TTS]
        }

    def test_it_answers_with_ids_not_enum_repr(self):
        """``str()`` on an enum member gives "ServiceProviders.OPENAI", which
        would reject every provider while looking like a list of them."""
        assert "openai" in _tts_providers()
        assert not any(name.startswith("ServiceProviders") for name in _tts_providers())

    @pytest.mark.parametrize(
        "provider", ["openai", "rumik", "google", "smallest", "deepgram", "sarvam"]
    )
    def test_the_providers_that_used_to_be_rejected(self, provider):
        assert provider in _tts_providers()


class TestThePickerHasVoicesToShow:
    """A provider on sale whose voice panel is empty is the same failure as a
    422, one screen further along."""

    @pytest.mark.parametrize(
        "provider,model",
        [
            ("openai", "tts-1"),
            ("rumik", "mulberry"),
            ("deepgram", "aura-2"),
            ("sarvam", "bulbul:v3"),
            ("elevenlabs", "eleven_flash_v2_5"),
        ],
    )
    def test_a_sold_model_publishes_voices(self, provider, model):
        catalogue = voice_catalogue.for_provider(provider, model=model)
        assert catalogue.voices, catalogue.unavailable_reason

    def test_openai_voices_are_openais_own(self):
        from api.services.configuration.options.openai import OPENAI_TTS_VOICES

        ids = {v.voice_id for v in voice_catalogue.for_provider("openai").voices}
        assert ids == set(OPENAI_TTS_VOICES)

    def test_openai_voices_claim_no_gender(self):
        """OpenAI publishes none, and guessing from a name would be both wrong
        and rude — the same reason Sarvam's are carried from the vendor rather
        than inferred. The picker groups what it knows and lists the rest."""
        assert all(
            v.gender is None for v in voice_catalogue.for_provider("openai").voices
        )
