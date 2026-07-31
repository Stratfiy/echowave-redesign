"""What language a realtime model is told to speak.

The models shipped here are native-audio Gemini Live models. They detect the
caller's language and switch between languages mid-conversation on their own,
and Google documents ``speech_config.language_code`` as unsupported for them —
so the correct thing to send is nothing at all.

**Sending nothing is not the same as leaving the field out**, which is the only
reason these tests exist. Gemini Live fills an omitted language with ``en-US``,
so a factory that helpfully skips the keyword pins English on every call.
"""

import pytest

from api.services.configuration.registry import (
    GoogleRealtimeLLMConfiguration,
    GoogleVertexRealtimeLLMConfiguration,
)
from api.services.pipecat.service_factory import _realtime_language_setting


class TestUnpinningTheLanguage:
    @pytest.mark.parametrize("given", ["auto", "AUTO", " auto ", "multi", "", None])
    def test_nothing_pinned_becomes_an_explicit_none(self, given):
        """None, not absent. Absent is how English gets pinned by accident."""
        assert _realtime_language_setting(given) is None

    @pytest.mark.parametrize("given", ["hi", "en-US", "ta-IN"])
    def test_a_real_language_is_passed_through(self, given):
        """Still supported: a half-cascade model does honour a pinned code, and
        an operator who wants one call locked to one language should get it."""
        assert _realtime_language_setting(given) == given


class TestTheDefaultsFollowTheCaller:
    """A default that pins English would make every new Gemini Live agent
    monolingual, on models chosen precisely because they are not."""

    def test_gemini_live_defaults_to_auto(self):
        config = GoogleRealtimeLLMConfiguration(api_key="k")

        assert config.language == "auto"
        assert _realtime_language_setting(config.language) is None

    def test_vertex_defaults_to_auto(self):
        config = GoogleVertexRealtimeLLMConfiguration(project_id="p")

        assert config.language == "auto"
        assert _realtime_language_setting(config.language) is None

    def test_the_vertex_default_model_is_a_native_audio_one(self):
        """The reason the default above is correct. If this model is ever
        changed to a half-cascade one, revisit the default with it."""
        assert (
            "native-audio" in GoogleVertexRealtimeLLMConfiguration(project_id="p").model
        )
