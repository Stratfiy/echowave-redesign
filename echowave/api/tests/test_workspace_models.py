"""Settings -> Models: one workspace-wide choice per slot."""

import pytest

from api.schemas.ai_model_configuration import (
    BYOKAIModelConfiguration,
    BYOKPipelineAIModelConfiguration,
    OrganizationAIModelConfigurationV2,
    compile_ai_model_configuration_v2,
)
from api.services.configuration import workspace_models

PLATFORM = {
    "llm": ["anthropic", "openai", "sarvam"],
    "stt": ["sarvam"],
    "tts": ["sarvam"],
}


def _view(stored=None, keys=None):
    return workspace_models.view(
        stored, platform_providers=PLATFORM, keys_held=keys or {}
    )


def _slot(view, key):
    return next(s for s in view["slots"] if s["key"] == key)


def test_every_slot_is_on_the_screen():
    assert [s["key"] for s in _view()["slots"]] == ["llm", "stt", "tts", "embeddings"]


def test_a_new_workspace_is_on_auto_and_everyday_is_claude_haiku():
    brain = _slot(_view(), "llm")
    assert brain["current"] == "tier:auto"
    assert brain["current_label"] == "Auto"
    assert brain["ours"][0]["value"] == "tier:auto"
    everyday = next(o for o in brain["ours"] if o["value"] == "tier:default")
    assert "Claude Haiku" in everyday["serves"]


def test_own_keys_are_offered_only_for_vendors_we_do_not_provide():
    brain = _slot(_view(), "llm")
    own = {o["vendor"] for o in brain["own"]}
    assert "anthropic" not in own and "openai" not in own and "sarvam" not in own
    # Something we do not serve is offered, and is not dropped for want of a
    # key: holding one is shown, not required.
    assert "groq" in own
    hearing = _slot(_view(), "stt")
    hearing_own = {o["vendor"] for o in hearing["own"]}
    # Deepgram serves our Instant tier, so it is ours, not a key to bring.
    assert "deepgram" not in hearing_own
    assert "assemblyai" in hearing_own


def test_choosing_an_exact_model_runs_it_on_our_key():
    view = _view()
    stored = workspace_models.choose(
        None, slot="llm", value="model:anthropic/claude-sonnet-5-5", offered=view
    )
    llm = compile_ai_model_configuration_v2(stored).llm
    assert (llm.model, llm.use_platform_key) == ("claude-sonnet-5-5", True)
    assert _slot(_view(stored), "llm")["current_label"] == "Claude Sonnet 5.5"


def test_choosing_a_tier_clears_an_exact_model():
    view = _view()
    stored = workspace_models.choose(
        None, slot="llm", value="model:anthropic/claude-opus-5-5", offered=view
    )
    stored = workspace_models.choose(
        stored, slot="llm", value="tier:accurate", offered=view
    )
    assert stored.decibyl.slots == {}
    assert stored.decibyl.llm_tier == "accurate"


def test_an_own_key_choice_leaves_the_key_to_the_vault():
    view = _view(keys={"llm": ["groq"]})
    groq = next(o for o in _slot(view, "llm")["own"] if o["vendor"] == "groq")
    assert groq["has_key"] is True
    stored = workspace_models.choose(
        None, slot="llm", value=f"own:groq/{groq['models'][0]}", offered=view
    )
    llm = compile_ai_model_configuration_v2(stored).llm
    assert llm.provider == "groq" and llm.api_key == ""
    assert not getattr(llm, "use_platform_key", False)


@pytest.mark.parametrize(
    "value",
    ["model:acme/x", "own:anthropic/claude-opus-5-5", "own:groq/not-a-model", "nope"],
)
def test_anything_not_offered_is_refused(value):
    with pytest.raises(workspace_models.UnknownChoice):
        workspace_models.choose(None, slot="llm", value=value, offered=_view())


def test_an_old_all_own_keys_account_reads_as_its_stack():
    from api.services.configuration.registry import (
        DeepgramSTTConfiguration,
        ElevenlabsTTSConfiguration,
        OpenAILLMService,
    )

    stored = OrganizationAIModelConfigurationV2(
        mode="byok",
        byok=BYOKAIModelConfiguration(
            mode="pipeline",
            pipeline=BYOKPipelineAIModelConfiguration(
                llm=OpenAILLMService(model="gpt-4.1", api_key=""),
                stt=DeepgramSTTConfiguration(model="nova-3-general", api_key=""),
                tts=ElevenlabsTTSConfiguration(api_key=""),
            ),
        ),
    )
    brain = _slot(_view(stored), "llm")
    assert brain["current"] == "own:openai/gpt-4.1"


def test_an_own_keys_stack_is_shown_but_not_rebuilt_by_a_save():
    """Saving one slot used to rewrite every slot as vendor/model, dropping
    custom voices, languages and vendors that need more than a key."""
    from api.services.configuration.registry import OpenAILLMService

    stored = OrganizationAIModelConfigurationV2(
        mode="byok",
        byok=BYOKAIModelConfiguration(
            mode="pipeline",
            pipeline=BYOKPipelineAIModelConfiguration.model_construct(
                llm=OpenAILLMService(model="gpt-4.1", api_key=""), stt=None, tts=None
            ),
        ),
    )
    view = _view(stored)
    assert view["locked"]
    with pytest.raises(workspace_models.LockedStack):
        workspace_models.choose(stored, slot="llm", value="tier:accurate", offered=view)


def test_a_speech_to_speech_workspace_is_locked_too():
    from api.schemas.ai_model_configuration import DecibylManagedAIModelConfiguration

    stored = OrganizationAIModelConfigurationV2(
        mode="decibyl",
        decibyl=DecibylManagedAIModelConfiguration(realtime_tier="natural"),
    )
    assert _view(stored)["locked"]
    assert _view(None)["locked"] is None


@pytest.mark.asyncio
async def test_a_chat_preset_is_not_replaced_by_the_workspace_brain(monkeypatch):
    """Everyday picked for a message writes tier "default"; the workspace
    default must not swap in a dearer model over somebody's choice."""
    from api.schemas.ai_model_configuration import DecibylManagedAIModelConfiguration
    from api.services.configuration import ai_model_configuration as amc
    from api.services.configuration import chat_presets

    workspace = OrganizationAIModelConfigurationV2(
        mode="decibyl",
        decibyl=DecibylManagedAIModelConfiguration(
            slots={"llm": "anthropic/claude-opus-5-5"}
        ),
    )

    async def stored(_org):
        return workspace

    monkeypatch.setattr(amc, "get_organization_ai_model_configuration_v2", stored)

    async def nothing(*args, **kwargs):
        return None

    monkeypatch.setattr(amc.byok_resolution, "apply", nothing)
    monkeypatch.setattr(amc.managed_resolution, "apply", nothing)

    async def no_fallback(_org):
        return False

    monkeypatch.setattr(amc, "_managed_fallback_allowed", no_fallback)

    chose = chat_presets.apply({}, "everyday")
    effective = await amc.get_effective_ai_model_configuration_for_workflow(
        organization_id=1, workflow_configurations=chose
    )
    assert effective.llm.model == "default"

    # An agent on the default with no choice made does follow the workspace.
    from api.services.configuration.agent_options import managed_stack_override

    plain = managed_stack_override(voice="", llm_tier="default")
    effective = await amc.get_effective_ai_model_configuration_for_workflow(
        organization_id=1, workflow_configurations=plain
    )
    assert effective.llm.model == "claude-opus-5-5"
