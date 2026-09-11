"""A vendor whose voice *is* its model can still be put on sale.

The operator's catalogue screen showed Deepgram TTS as having nothing at all,
under the message "Install a key for this provider first" — while the Deepgram
TTS key sat in the vault, active, and passing its health check that morning.

Both halves were wrong in a way that cost real time. There was no missing key.
And the models were not missing either: Deepgram names its model through the
voice — ``aura-2-thalia-en`` *is* the aura-2 model — so the configuration class
computes ``model`` from the voice instead of carrying a field for it, and
``known_models`` reads examples off the *field*. A computed property has none,
so the answer was an empty tuple, and the screen turned that into a sentence
about keys.

xAI TTS is the same shape and was equally invisible.
"""

from __future__ import annotations

from api.services.configuration import voice_catalogue
from api.services.configuration.model_discovery import known_models
from api.services.configuration.options.deepgram import (
    DEEPGRAM_AURA_VOICES,
    DEEPGRAM_TTS_MODELS,
)


class TestAVendorThatNamesItsModelThroughTheVoice:
    def test_deepgram_tts_offers_its_models(self):
        assert known_models("tts", "deepgram") == DEEPGRAM_TTS_MODELS

    def test_xai_tts_offers_its_one_model(self):
        """One value is still a value. Without it the slot cannot be sold at
        all, because the catalogue is keyed by model id."""
        assert known_models("tts", "xai") == ("xai-tts",)

    def test_a_vendor_with_an_ordinary_model_field_is_untouched(self):
        assert "nova-3-general" in known_models("stt", "deepgram")
        assert "bulbul:v3" in known_models("tts", "sarvam")

    def test_an_unknown_vendor_still_answers_empty(self):
        assert known_models("tts", "nobody") == ()


class TestTheVoicesAreThereToPick:
    def test_deepgram_publishes_its_speakers(self):
        catalogue = voice_catalogue.for_provider("deepgram", model="aura-2")
        assert len(catalogue.voices) == len(DEEPGRAM_AURA_VOICES)
        assert catalogue.unavailable_reason is None

    def test_every_voice_has_a_gender_the_picker_groups_by(self):
        """The picker filters on gender and drops anything else, so a voice
        with a vendor's own word for it disappears without a trace."""
        voices = voice_catalogue.for_provider("deepgram").voices
        assert {v.gender for v in voices} == {"female", "male"}

    def test_both_genders_are_actually_offered(self):
        voices = voice_catalogue.for_provider("deepgram").voices
        assert sum(v.gender == "female" for v in voices) >= 5
        assert sum(v.gender == "male" for v in voices) >= 5

    def test_the_model_argument_changes_nothing(self):
        """Unlike Sarvam, where v2 and v3 have different speakers: here the
        model is read off the front of the voice, so there is one list."""
        assert (
            voice_catalogue.for_provider("deepgram", model="aura-1").voices
            == voice_catalogue.for_provider("deepgram", model="aura-2").voices
        )

    def test_a_default_voice_can_be_resolved(self):
        """What the pipeline asks for when a managed slot says only "female"."""
        assert (
            voice_catalogue.default_voice_id(
                "deepgram", model="aura-2", gender="female"
            )
            is not None
        )


class TestOnlyWhatItCanActuallySay:
    """Aura-2 speaks no Indian language. Offering one of its English voices to
    a Hindi line would read Hindi with an American accent, which is a worse
    outcome than the vendor not being on the list."""

    def test_every_published_voice_is_english(self):
        assert {
            v.language for v in voice_catalogue.for_provider("deepgram").voices
        } == {"en"}

    def test_no_voice_id_claims_another_language(self):
        assert all(voice_id.endswith("-en") for voice_id, _, _ in DEEPGRAM_AURA_VOICES)

    def test_the_ids_are_the_vendors_own(self):
        """A name we invented is a 400 from Deepgram at the first word."""
        assert all(
            voice_id.startswith("aura-2-") for voice_id, _, _ in DEEPGRAM_AURA_VOICES
        )
