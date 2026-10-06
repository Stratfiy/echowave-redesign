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


def test_a_new_workspace_is_on_everyday_which_is_claude():
    brain = _slot(_view(), "llm")
    assert brain["current"] == "tier:default"
    assert brain["current_label"] == "Everyday"
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
