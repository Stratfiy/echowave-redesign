"""The first agent an account hears answers in the voice it just pressed play on."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from api.routes import agent_templates as route
from api.schemas.ai_model_configuration import OrganizationAIModelConfigurationV3
from api.services.agent_templates.catalogue import get_template
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY,
)


@pytest.fixture
def managed_account(monkeypatch):
    async def _config(organization_id):
        return SimpleNamespace(
            decibyl=SimpleNamespace(
                llm_tier="default",
                stt_tier="default",
                tts_tier="default",
                realtime_tier="",
            )
        )

    monkeypatch.setattr(route, "get_organization_ai_model_configuration_v2", _config)


def _template():
    template = get_template("clinic_appointment")
    assert template is not None and template.suggested_voices
    return template


def _stack(override):
    v3 = OrganizationAIModelConfigurationV3.model_validate(
        override[WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY]
    )
    return v3.stack


class TestTheFirstAgentSpeaksOnElevenLabs:
    async def test_the_voice_pressed_on_the_card_is_the_one_the_agent_gets(
        self, managed_account
    ):
        template = _template()
        picked = next(v for v in template.suggested_voices if v.gender == "male")
        request = route.CreateFromTemplateRequest(
            voice_gender="male", voice_id=picked.voice_id, source="first_agent"
        )
        override = await route._voice_override(
            request, organization_id=1, template=template
        )
        stack = _stack(override)
        assert stack.tts.provider == "elevenlabs"
        assert stack.tts.model == route.FIRST_AGENT_VOICE_MODEL
        assert stack.tts.voice == picked.voice_id
        assert stack.tts.use_platform_key is True
        # Only the voice is pinned; brain and ears stay managed tiers.
        assert stack.managed_slots() == ["stt", "llm"]

    async def test_no_chip_pressed_means_the_first_suggested_voice_of_that_gender(
        self, managed_account
    ):
        template = _template()
        request = route.CreateFromTemplateRequest(
            voice_gender="female", source="first_agent"
        )
        override = await route._voice_override(
            request, organization_id=1, template=template
        )
        stack = _stack(override)
        first_female = next(
            v for v in template.suggested_voices if v.gender == "female"
        )
        assert stack.tts.provider == "elevenlabs"
        assert stack.tts.voice == first_female.voice_id

    async def test_the_template_grid_is_unchanged(self, managed_account):
        """Only the first-agent flow pins ElevenLabs. A one-click create from
        the grid keeps the gender sentinel on the managed tier."""
        template = _template()
        request = route.CreateFromTemplateRequest(
            voice_gender="female", source="template_grid"
        )
        override = await route._voice_override(
            request, organization_id=1, template=template
        )
        stack = _stack(override)
        assert stack.tts.provider == "decibyl"
        assert stack.tts.voice == "female"

    async def test_an_unknown_voice_id_falls_back_to_the_gender(self, managed_account):
        template = _template()
        request = route.CreateFromTemplateRequest(
            voice_gender="male", voice_id="not-a-suggested-voice", source="first_agent"
        )
        override = await route._voice_override(
            request, organization_id=1, template=template
        )
        stack = _stack(override)
        assert stack.tts.provider == "elevenlabs"
        assert (
            stack.tts.voice
            == next(v for v in template.suggested_voices if v.gender == "male").voice_id
        )

    async def test_a_byok_account_is_left_alone(self, monkeypatch):
        async def _config(organization_id):
            return SimpleNamespace(decibyl=None)

        monkeypatch.setattr(
            route, "get_organization_ai_model_configuration_v2", _config
        )
        request = route.CreateFromTemplateRequest(
            voice_gender="male", source="first_agent"
        )
        assert (
            await route._voice_override(
                request, organization_id=1, template=_template()
            )
            is None
        )
