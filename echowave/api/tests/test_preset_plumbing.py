"""The pieces between a preset chip and a running call, tested without a call.

Three seams, each of which failed quietly when it was wrong:

* a managed voice sentinel ("default", "female") reaching a vendor that has
  no default speaker -- ElevenLabs refuses the call, Rumik invents a voice;
* the create route reading what a new agent needs off the wizard's body;
* the model row reading what an existing agent needs off its definition.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class TestAManagedVoiceOnAVendorWithNoDefault:
    def _factory(self):
        from api.services.pipecat import service_factory

        return service_factory

    def _tts(self, provider: str, model: str):
        return SimpleNamespace(provider=provider, model=model)

    def test_default_becomes_the_first_published_voice(self):
        f = self._factory()
        out = f._managed_voice_or(
            "default", self._tts("elevenlabs", "eleven_flash_v2_5"), language=None
        )
        assert out == "21m00Tcm4TlvDq8ikWAM"  # Rachel, first on the list

    def test_empty_becomes_the_first_published_voice_too(self):
        f = self._factory()
        out = f._managed_voice_or("", self._tts("rumik", "mulberry"), language="hi")
        assert out == "emma"

    def test_a_gender_picks_a_voice_of_that_gender(self):
        f = self._factory()
        out = f._managed_voice_or(
            "male", self._tts("elevenlabs", "eleven_flash_v2_5"), language=None
        )
        assert out == "pNInz6obpgDQGcFmaJgB"  # Adam
        out = f._managed_voice_or(
            "female", self._tts("rumik", "mulberry"), language=None
        )
        assert out == "emma"

    def test_a_real_voice_id_passes_through_untouched(self):
        f = self._factory()
        assert (
            f._managed_voice_or(
                "my-cloned-voice", self._tts("elevenlabs", "x"), language=None
            )
            == "my-cloned-voice"
        )

    def test_muga_has_no_voices_so_default_stays_unresolved(self):
        """Muga is directed by tone tags; there is nothing to pick, and the
        branch that follows sends no speaker at all."""
        f = self._factory()
        assert (
            f._managed_voice_or("default", self._tts("rumik", "muga"), language=None)
            == "default"
        )

    def test_a_vendor_without_a_catalogue_keeps_what_it_was_given(self):
        f = self._factory()
        assert (
            f._managed_voice_or("abc", self._tts("cartesia", "sonic-3"), language=None)
            == "abc"
        )


class TestWhatANewAgentStartsOn:
    def _request(self, **kw):
        from api.routes.workflow import CreateWorkflowTemplateRequest

        base = {"call_type": "inbound", "use_case": "x"}
        return CreateWorkflowTemplateRequest(**{**base, **kw})

    def test_a_named_preset_wins(self):
        from api.routes.workflow import _preset_for_create

        assert _preset_for_create(self._request(preset="global")).slug == "global"

    def test_an_unknown_preset_is_refused(self):
        from fastapi import HTTPException

        from api.routes.workflow import _preset_for_create

        with pytest.raises(HTTPException):
            _preset_for_create(self._request(preset="everyday"))

    def test_nothing_named_is_recommended_from_the_languages(self):
        from api.routes.workflow import _preset_for_create

        assert (
            _preset_for_create(self._request(languages=["Hindi", "English"])).slug
            == "basic"
        )
        assert (
            _preset_for_create(self._request(languages=["Hindi", "Telugu"])).slug
            == "standard"
        )

    def test_an_old_caller_naming_only_a_brain_still_lands_somewhere_sane(self):
        from api.routes.workflow import _preset_for_create

        assert _preset_for_create(self._request(llm_tier="accurate")).slug == "smart"


class TestWhatAnExistingAgentNeeds:
    def test_languages_and_tools_are_read_off_the_agent(self):
        from api.routes.workflow import _requirement_of

        definition = {
            "nodes": [
                {"id": "a", "data": {"prompt": "hi"}},
                {"id": "b", "data": {"tool_uuids": ["t1"]}},
            ]
        }
        req = _requirement_of(definition, {"agent_languages": ["ta", "en"]})
        assert req.languages == ("ta", "en")
        assert req.uses_tools is True

    def test_documents_count_as_tools(self):
        from api.routes.workflow import _requirement_of

        req = _requirement_of({"nodes": [{"data": {"document_uuids": ["d"]}}]}, None)
        assert req.uses_tools is True

    def test_nothing_known_is_an_empty_requirement(self):
        from api.routes.workflow import _requirement_of

        req = _requirement_of(None, None)
        assert req.languages == ()
        assert req.uses_tools is False
