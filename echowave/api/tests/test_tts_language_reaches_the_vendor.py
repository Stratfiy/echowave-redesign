"""Every vendor that accepts a language code was being handed English.

Two routes, neither of which made a sound. ElevenLabs and MiniMax had no
language field on their configuration at all, so there was nothing to pass.
Smallest had one and the factory swallowed it: a code the enum did not
recognise -- ``ta-IN`` where it wanted ``ta`` -- became ``Language.EN`` inside
a bare ``except ValueError``.

The symptom is not subtle. A Tamil agent read Tamil text with English
phonetics, which is the first thing a caller in Hosur notices and the last
thing any log mentioned.
"""

from __future__ import annotations

from types import SimpleNamespace

from pipecat.transcriptions.language import Language

from api.services.pipecat.service_factory import (
    MINIMAX_LANGUAGE_BOOST,
    _minimax_language_boost,
    _tts_language,
)


def _config(language):
    return SimpleNamespace(tts=SimpleNamespace(language=language))


class TestResolvingALanguage:
    def test_a_plain_code_resolves(self):
        assert _tts_language(_config("ta"), "X") is Language.TA

    def test_a_regional_code_resolves(self):
        """ta-IN is what a configuration actually holds."""
        assert _tts_language(_config("ta-IN"), "X") is Language.TA_IN

    def test_case_does_not_decide_the_language(self):
        assert _tts_language(_config("TA"), "X") is Language.TA


class TestNothingSilentlyBecomesEnglish:
    """The bug, stated as its absence."""

    def test_an_unresolvable_code_returns_nothing_rather_than_english(self):
        assert _tts_language(_config("klingon"), "X") is None

    def test_an_unset_language_returns_nothing(self):
        """A vendor given no language uses its voice's own, which is honest.
        A vendor told 'en' for Tamil text is a mispronunciation we asked for."""
        assert _tts_language(_config(None), "X") is None

    def test_an_empty_language_returns_nothing(self):
        assert _tts_language(_config(""), "X") is None


class TestMiniMaxWantsANameNotACode:
    """language_boost takes 'Tamil', and an ISO code there is ignored, not
    refused -- the silent kind of wrong this change is about."""

    def test_a_code_becomes_the_name(self):
        assert _minimax_language_boost(_config("ta-IN")) == "Tamil"

    def test_hindi_too(self):
        assert _minimax_language_boost(_config("hi")) == "Hindi"

    def test_no_language_sends_no_hint(self):
        assert _minimax_language_boost(_config(None)) is None

    def test_a_language_we_have_no_name_for_sends_no_hint(self):
        """Better MiniMax's own inference than a name it does not know."""
        assert _minimax_language_boost(_config("cy")) is None

    def test_every_indian_language_this_product_speaks_has_a_name(self):
        for code in ("en", "hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa"):
            assert code in MINIMAX_LANGUAGE_BOOST, code


class TestTheConfigurationCanHoldOne:
    """The other half of the bug: there was no field to fill in."""

    def test_elevenlabs_has_a_language_field(self):
        from api.services.configuration.registry import ElevenlabsTTSConfiguration

        assert "language" in ElevenlabsTTSConfiguration.model_fields

    def test_minimax_has_a_language_field(self):
        from api.services.configuration.registry import MiniMaxTTSConfiguration

        assert "language" in MiniMaxTTSConfiguration.model_fields

    def test_neither_defaults_to_english(self):
        """A default of 'en' is the Smallest bug written into the schema."""
        from api.services.configuration.registry import (
            ElevenlabsTTSConfiguration,
            MiniMaxTTSConfiguration,
        )

        for model in (ElevenlabsTTSConfiguration, MiniMaxTTSConfiguration):
            assert model.model_fields["language"].default is None
