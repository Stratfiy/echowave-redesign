"""The model choice as a non-technical buyer is asked it.

Two things are worth testing here and neither is the wording: that the choice
actually reaches the agent, and that a stack we cannot price says so rather
than saying zero.
"""

from api.schemas.ai_model_configuration import (
    OrganizationAIModelConfigurationV3,
    compile_ai_model_configuration_v3,
)
from api.services.configuration import managed_tiers
from api.services.configuration.agent_options import (
    approximate_minutes,
    brains,
    managed_stack_override,
    voices,
)
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY as OVERRIDE_KEY,
)


class TestOptions:
    def test_every_offered_tier_has_a_label(self):
        # A tier with no label would render as its storage key — "accurate" —
        # which is the vocabulary this whole thing exists to remove.
        assert {b.tier for b in brains()} == set(managed_tiers.LLM_TIERS)
        assert all(b.label and b.blurb for b in brains())

    def test_the_labels_are_the_product_not_the_key(self):
        assert [b.label for b in brains()] == ["Lite", "Normal", "Smart"]

    def test_exactly_one_voice_is_the_default(self):
        # Derived from position rather than hardcoded, so it stays right when
        # the managed tier moves from bulbul:v2 to v3 — the vendor lists its
        # own default first in both.
        defaults = [v for v in voices() if v.is_default]
        assert len(defaults) == 1
        assert defaults[0].voice_id == voices()[0].voice_id

    def test_voices_come_from_the_catalogue_with_a_gender(self):
        # Gender is Sarvam's own metadata; the picker filters on it and
        # guessing from a name would be both wrong and rude.
        found = voices()
        assert found
        assert all(v.voice_id and v.name for v in found)
        assert any(v.gender == "female" for v in found)
        assert any(v.gender == "male" for v in found)


class TestApproximateMinutes:
    def test_a_balance_becomes_minutes(self):
        assert approximate_minutes(250000, 520) == 480

    def test_an_unpriced_stack_is_unknown_not_free(self):
        # Zero would read as "this costs nothing", which is the one thing it
        # never means.
        assert approximate_minutes(250000, 0) is None
        assert approximate_minutes(250000, -1) is None

    def test_it_rounds_down(self):
        # Promising a minute the balance does not cover is how an overage
        # conversation starts.
        assert approximate_minutes(1000, 300) == 3


class TestOverride:
    def test_choosing_nothing_inherits_the_organization_default(self):
        # An API client posting the old three-field body must be unaffected.
        assert managed_stack_override(voice="", llm_tier="") == {}
        assert managed_stack_override(voice="  ", llm_tier="  ") == {}

    def test_the_choice_reaches_the_agent(self):
        override = managed_stack_override(voice="karun", llm_tier="lite")
        stack = OrganizationAIModelConfigurationV3.model_validate(
            override[OVERRIDE_KEY]
        )
        effective = compile_ai_model_configuration_v3(stack)

        assert effective.llm.model == "lite"
        assert effective.tts.voice == "karun"

    def test_every_slot_stays_managed(self):
        # The slots name a tier, not a vendor model. That is what lets the tier
        # be repointed later without every agent created today being pinned to
        # whatever it happened to resolve to this morning.
        override = managed_stack_override(voice="anushka", llm_tier="accurate")
        stack = OrganizationAIModelConfigurationV3.model_validate(
            override[OVERRIDE_KEY]
        )
        assert set(stack.stack.managed_slots()) == {"stt", "llm", "tts"}
        assert stack.stack.byok_slots() == []

    def test_a_voice_alone_still_gets_a_working_stack(self):
        override = managed_stack_override(voice="vidya", llm_tier="")
        stack = OrganizationAIModelConfigurationV3.model_validate(
            override[OVERRIDE_KEY]
        )
        effective = compile_ai_model_configuration_v3(stack)
        assert effective.tts.voice == "vidya"
        assert effective.llm.model == "default"
