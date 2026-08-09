"""Translating a managed language into the vendor's vocabulary.

Both cases here were found on a live call, not in review: transcription refused
the call outright, and synthesis silently answered in Hindi.
"""

import pytest

from api.services.configuration import managed_language as ml


class TestSpeechToText:
    def test_multi_becomes_sarvams_autodetect(self):
        # The exact failure from the first real call:
        # "Language 'multi' is not supported by saarika:v2.5 model"
        assert ml.for_stt("sarvam", "saarika:v2.5", "multi") == "unknown"

    def test_no_language_becomes_autodetect(self):
        assert ml.for_stt("sarvam", "saarika:v2.5", None) == "unknown"
        assert ml.for_stt("sarvam", "saarika:v2.5", "  ") == "unknown"

    def test_a_bare_iso_code_gains_its_region(self):
        assert ml.for_stt("sarvam", "saarika:v2.5", "te") == "te-IN"
        assert ml.for_stt("sarvam", "saarika:v2.5", "hi") == "hi-IN"
        assert ml.for_stt("sarvam", "saarika:v2.5", "en") == "en-IN"

    def test_odia_is_not_a_suffix_rule(self):
        # ISO 639-1 says "or"; Sarvam says "od-IN". A naive f"{lang}-IN"
        # would produce "or-IN" and be refused.
        assert ml.for_stt("sarvam", "saarika:v2.5", "or") == "od-IN"

    def test_an_already_tagged_code_is_left_alone(self):
        assert ml.for_stt("sarvam", "saarika:v2.5", "ta-IN") == "ta-IN"

    def test_a_regional_variant_is_normalised_to_india(self):
        # A customer picking "en-US" on a managed slot is choosing English, not
        # an American Sarvam endpoint, which does not exist.
        assert ml.for_stt("sarvam", "saarika:v2.5", "en-US") == "en-IN"

    def test_a_language_the_model_cannot_do_falls_back_to_autodetect(self):
        # Assamese is saaras:v3 only. On v2.5 it must not be sent through —
        # but a live caller must not be dropped for it either.
        assert ml.for_stt("sarvam", "saarika:v2.5", "as") == "unknown"

    def test_the_bigger_model_accepts_more(self):
        assert ml.for_stt("sarvam", "saaras:v3", "as") == "as-IN"

    def test_an_unknown_language_falls_back_rather_than_raising(self):
        assert ml.for_stt("sarvam", "saarika:v2.5", "klingon") == "unknown"

    def test_other_providers_are_untouched(self):
        # Deepgram already speaks this vocabulary; rewriting it would break it.
        assert ml.for_stt("deepgram", "nova-3", "multi") == "multi"
        assert ml.for_stt("openai", "whisper-1", "en") == "en"


class TestTextToSpeech:
    def test_multi_becomes_indian_english_not_hindi(self):
        # The silent bug: service_factory maps unknown codes to Language.HI, so
        # an untranslated code answered every caller in Hindi without a log line.
        assert ml.for_tts("sarvam", "bulbul:v2", "multi") == "en-IN"

    def test_no_language_becomes_indian_english(self):
        assert ml.for_tts("sarvam", "bulbul:v2", None) == "en-IN"

    def test_a_chosen_language_is_honoured(self):
        assert ml.for_tts("sarvam", "bulbul:v2", "te") == "te-IN"
        assert ml.for_tts("sarvam", "bulbul:v2", "mr") == "mr-IN"

    def test_synthesis_never_returns_autodetect(self):
        # There is nothing to detect — synthesis has to speak something.
        for language in ("multi", None, "klingon", "unknown"):
            assert ml.for_tts("sarvam", "bulbul:v2", language) != "unknown"

    def test_other_providers_are_untouched(self):
        assert ml.for_tts("elevenlabs", "eleven_turbo_v2", "multi") == "multi"


class _Section:
    def __init__(self, **kwargs):
        self.provider = "decibyl"
        self.model = None
        self.api_key = None
        self.use_platform_key = False
        for key, value in kwargs.items():
            setattr(self, key, value)

    @property
    def is_managed(self) -> bool:
        return self.provider == "decibyl" or bool(self.use_platform_key)


class _Effective:
    def __init__(self, stt=None, tts=None):
        self.stt = stt
        self.tts = tts
        self.llm = None
        self.realtime = None
        self.embeddings = None


@pytest.mark.asyncio
class TestAppliedDuringResolution:
    """The translation has to happen where the section stops being managed."""

    async def test_a_managed_stt_section_is_translated(self, monkeypatch):
        from api.services.configuration import managed_resolution

        async def fake_key(session, *, component, provider):
            return "a-platform-key"

        monkeypatch.setattr(
            managed_resolution.platform_credentials, "resolve_api_key", fake_key
        )

        effective = _Effective(stt=_Section(language="multi"))
        await managed_resolution.apply(effective)

        assert effective.stt.provider == "sarvam"
        assert effective.stt.language == "unknown", (
            "a managed STT section must not carry Decibyl's 'multi' to Sarvam"
        )

    async def test_a_managed_tts_section_does_not_default_to_hindi(self, monkeypatch):
        from api.services.configuration import managed_resolution

        async def fake_key(session, *, component, provider):
            return "a-platform-key"

        monkeypatch.setattr(
            managed_resolution.platform_credentials, "resolve_api_key", fake_key
        )

        effective = _Effective(tts=_Section(language="multi"))
        await managed_resolution.apply(effective)

        assert effective.tts.language == "en-IN"
