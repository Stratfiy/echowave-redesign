"""The model choice as a non-technical buyer is asked it.

Two things are worth testing here and neither is the wording: that the choice
actually reaches the agent, and that a stack we cannot price says so rather
than saying zero.
"""

from api.db.models import OrganizationModel
from api.schemas.ai_model_configuration import (
    OrganizationAIModelConfigurationV3,
    compile_ai_model_configuration_v3,
)
from api.services.configuration import agent_options, managed_tiers
from api.services.configuration.agent_options import (
    approximate_minutes,
    brains,
    managed_stack_override,
    voices,
)
from api.services.configuration.ai_model_configuration import (
    WORKFLOW_MODEL_CONFIGURATION_V2_OVERRIDE_KEY as OVERRIDE_KEY,
    get_effective_ai_model_configuration_for_workflow,
)


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


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

    async def test_a_wizard_created_v3_agent_reaches_call_readiness(
        self, monkeypatch
    ):
        """The create wizard stores v3 under the historical v2 override key.

        Call readiness must dispatch on the payload version, not the key name.
        This is the production regression that returned an HTTP 500 before a
        workflow run or carrier call could be created.
        """

        async def leave_managed_slots_unresolved(_effective):
            return None

        monkeypatch.setattr(
            "api.services.configuration.ai_model_configuration."
            "managed_resolution.apply",
            leave_managed_slots_unresolved,
        )
        override = managed_stack_override(voice="karun", llm_tier="lite")

        effective = await get_effective_ai_model_configuration_for_workflow(
            organization_id=None,
            workflow_configurations=override,
        )

        assert effective.llm.model == "lite"
        assert effective.tts.voice == "karun"
        assert effective.managed_service_version == 3


class TestVoiceSamples:
    """A sample is an optional asset — nothing may depend on it existing."""

    def test_the_path_is_derived_and_normalised(self):
        from api.services.configuration.voice_samples import sample_path

        assert sample_path("Anushka", "hi") == "voice-samples/anushka-hi.wav"
        assert sample_path("  KARUN  ", "en") == "voice-samples/karun-en.wav"

    def test_samples_live_apart_from_call_audio(self):
        # Product assets, not customer data — a retention sweep over call
        # recordings must never reach them.
        from api.services.configuration.voice_samples import SAMPLE_PREFIX, sample_path

        assert sample_path("anushka", "en").startswith(f"{SAMPLE_PREFIX}/")

    async def test_an_unknown_language_has_no_sample(self):
        from api.services.configuration.voice_samples import sample_url

        assert await sample_url("anushka", "ta") is None

    async def test_storage_being_down_returns_no_sample_rather_than_raising(self):
        # Storage failing must cost the picker its play buttons, not its
        # existence — this runs with nothing listening on the storage port.
        from api.services.configuration.voice_samples import sample_url

        assert await sample_url("anushka", "en") is None

    def test_every_sample_language_has_a_line_to_say(self):
        from api.services.configuration.voice_samples import (
            SAMPLE_LANGUAGES,
            SAMPLE_LINES,
        )

        for language in SAMPLE_LANGUAGES:
            assert SAMPLE_LINES.get(language, "").strip()


class TestTheSimplePickersCards:
    """Three cards, priced, each carrying the residency badge.

    Every bundle has **variants** even when there is one, so the screen renders
    a single shape rather than branching on architecture — and a fourth bundle
    later needs no new case.
    """

    async def test_every_enabled_bundle_is_offered(self, db_session, async_session):
        org = await _org(async_session, "bundle-list")
        cards = await agent_options.bundle_options(
            async_session, organization_id=org.id
        )

        assert [c["slug"] for c in cards] == ["everyday", "natural", "premium"]

    async def test_the_pipeline_bundle_offers_the_three_brains(
        self, db_session, async_session
    ):
        """Everyday is three variants of one card, not three cards."""
        org = await _org(async_session, "bundle-variants")
        cards = await agent_options.bundle_options(
            async_session, organization_id=org.id
        )
        everyday = next(c for c in cards if c["slug"] == "everyday")

        assert [v["tier"] for v in everyday["variants"]] == [
            "lite",
            "default",
            "accurate",
        ]
        assert [v["label"] for v in everyday["variants"]] == ["Lite", "Normal", "Smart"]

    async def test_a_speech_to_speech_bundle_has_exactly_one_variant(
        self, db_session, async_session
    ):
        org = await _org(async_session, "bundle-single")
        cards = await agent_options.bundle_options(
            async_session, organization_id=org.id
        )
        natural = next(c for c in cards if c["slug"] == "natural")

        assert len(natural["variants"]) == 1
        assert natural["picks_voice"] is False

    async def test_only_the_pipeline_bundle_picks_a_voice(
        self, db_session, async_session
    ):
        """A speech-to-speech model brings its own voice — there is nothing to
        choose, and offering a picker that does nothing is worse than none."""
        org = await _org(async_session, "bundle-voice")
        cards = await agent_options.bundle_options(
            async_session, organization_id=org.id
        )

        assert next(c for c in cards if c["slug"] == "everyday")["picks_voice"] is True
        assert next(c for c in cards if c["slug"] == "premium")["picks_voice"] is False

    async def test_the_india_badge_rides_on_the_variant_not_the_card(
        self, db_session, async_session
    ):
        """Within Everyday, Lite qualifies and the other two do not — so the
        badge cannot live on the card."""
        org = await _org(async_session, "bundle-badge")
        cards = await agent_options.bundle_options(
            async_session, organization_id=org.id
        )
        by_tier = {
            v["tier"]: v
            for v in next(c for c in cards if c["slug"] == "everyday")["variants"]
        }

        assert by_tier["lite"]["india_only"] is True
        assert by_tier["default"]["india_only"] is False
        assert by_tier["accurate"]["india_only"] is False

    async def test_a_disabled_bundle_is_not_offered(self, db_session, async_session):
        from api.services.configuration import bundles as bundle_service

        org = await _org(async_session, "bundle-hidden")
        await bundle_service.list_bundles(async_session)
        await bundle_service.upsert_bundle(
            async_session, slug="premium", actor_user_id=None, is_enabled=False
        )

        cards = await agent_options.bundle_options(
            async_session, organization_id=org.id
        )
        assert "premium" not in [c["slug"] for c in cards]

    async def test_the_price_matches_the_estimator_it_came_from(
        self, db_session, async_session
    ):
        """No second calculation. Every pricing bug this week came from a
        parallel sum drifting from the first one, so the card's number has to
        be the estimator's number rather than a re-derivation of it.
        """
        org = await _org(async_session, "bundle-price")
        cards = await agent_options.bundle_options(
            async_session, organization_id=org.id
        )
        lite = next(
            v
            for v in next(c for c in cards if c["slug"] == "everyday")["variants"]
            if v["tier"] == "lite"
        )

        direct = await agent_options.price_per_minute(
            async_session, organization_id=org.id, brain="lite"
        )
        assert lite["paise_per_minute"] == direct

    async def test_a_dearer_brain_costs_more(self, db_session, async_session):
        """The point of offering the choice at all.

        Needs real rate rows: with an empty price book every provider line is
        uncosted and all three tiers come back as the platform fee alone —
        which is correct behaviour and would make this assertion vacuous.
        """
        from datetime import UTC, datetime

        from api.db.models import ProviderRateModel
        from api.enums import CostComponent, RateUnit

        def rate(provider, component, unit, mpaise, model=""):
            return ProviderRateModel(
                provider=provider,
                model=model,
                component=component.value,
                unit=unit.value,
                rate_mpaise=mpaise,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )

        org = await _org(async_session, "bundle-order")
        async_session.add_all(
            [
                rate("sarvam", CostComponent.STT, RateUnit.MINUTE, 500),
                rate("sarvam", CostComponent.TTS, RateUnit.THOUSAND_CHARS, 1500),
                # Millipaise per 1,000 tokens, at the magnitude the real card
                # uses. My first pass was a hundred times too small: the line
                # came out sub-paise, rounded to zero, and all three tiers
                # priced identically — which looked like a bug in the picker
                # and was a bug in the fixture.
                rate(
                    "sarvam",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    760,
                    model="sarvam-105b",
                ),
                rate(
                    "openai",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    7_300,
                    model="gpt-4.1-mini",
                ),
                rate(
                    "openai",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    36_500,
                    model="gpt-4.1",
                ),
            ]
        )
        await async_session.flush()

        cards = await agent_options.bundle_options(
            async_session, organization_id=org.id
        )
        by_tier = {
            v["tier"]: v["paise_per_minute"]
            for v in next(c for c in cards if c["slug"] == "everyday")["variants"]
        }

        assert by_tier["lite"] < by_tier["default"] < by_tier["accurate"]
