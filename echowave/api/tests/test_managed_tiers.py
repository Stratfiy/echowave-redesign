"""The managed offering: a customer with no API key, running on ours.

This is the seam that decides whether a "Decibyl" section becomes a real vendor
call or fails at dial time, so the tests are about the two things that break it
— a tier nobody mapped, and a platform key nobody stored.
"""

import pytest

from api.enums import CostComponent
from api.services.configuration import managed_tiers
from api.services.configuration.registry import REGISTRY, ServiceType


class TestTierMapping:
    def test_every_llm_tier_resolves(self):
        """A customer picked one of these from a dropdown. Every one of them has
        to name a real vendor or their agent cannot run."""
        for tier in managed_tiers.LLM_TIERS:
            up = managed_tiers.resolve(CostComponent.LLM, tier)
            assert up.provider and up.model

    def test_speech_is_indic_by_default(self):
        """The traffic is Indian. A generic multilingual voice reads as the
        wrong region to a Telugu listener, and no cost saving covers that."""
        assert managed_tiers.resolve(CostComponent.STT, "default").provider == "sarvam"
        assert managed_tiers.resolve(CostComponent.TTS, "default").provider == "sarvam"

    def test_a_retired_tier_falls_back_rather_than_failing(self):
        """A stored config naming a tier we since dropped must keep calling on
        the sensible option, not fail when the campaign dials."""
        assert managed_tiers.resolve(CostComponent.LLM, "a-tier-we-removed") == (
            managed_tiers.resolve(CostComponent.LLM, "default")
        )

    def test_no_tier_at_all_is_the_default(self):
        assert managed_tiers.resolve(CostComponent.LLM, None).model

    def test_a_component_with_no_mapping_raises(self):
        """Telephony is deliberately unmanaged — carrier accounts carry their own
        KYC. Asking for it is a programming error, not a runtime fallback."""
        with pytest.raises(KeyError):
            managed_tiers.resolve("telephony", "default")

    def test_a_mapping_can_be_moved_without_a_release(self, monkeypatch):
        """Switching a tier to a better model should be a restart, not a deploy.
        Customers named a tier, not a vendor, precisely so this is cheap."""
        monkeypatch.setenv("MANAGED_LLM_ACCURATE", "google:gemini-3.5-flash")
        up = managed_tiers.resolve(CostComponent.LLM, "accurate")
        assert (up.provider, up.model) == ("google", "gemini-3.5-flash")

    def test_a_malformed_override_is_ignored_rather_than_obeyed(self, monkeypatch):
        """ "google" with no model would resolve to a vendor with no model name.
        Falling back to the built-in mapping beats calling with nonsense."""
        monkeypatch.setenv("MANAGED_LLM_FAST", "google")
        assert managed_tiers.resolve(CostComponent.LLM, "fast").model


class TestWhatTheOfferingDependsOn:
    def test_it_reports_every_provider_a_tier_points_at(self):
        """The readiness check needs this: a tier pointing at a provider we hold
        no key for is a managed customer whose calls fail at dial time."""
        pairs = managed_tiers.upstream_providers()
        assert ("stt", "sarvam") in pairs
        assert ("tts", "sarvam") in pairs
        assert any(component == "llm" for component, _ in pairs)

    def test_it_covers_every_managed_component(self):
        """Embeddings and realtime belong here even though neither is a billing
        component.

        A managed configuration emits an embeddings section for knowledge-base
        retrieval. While it was missing from this map the section kept
        ``provider=decibyl`` all the way to the embeddings factory, and the
        model-override screen showed a second "Invalid ServiceProviders.DECIBYL
        API key" that no customer could act on.

        Realtime is here so speech-to-speech can be managed at all. Without a
        mapping it was BYOK-only, which is why the old UI listed "Speech to
        Speech" beside "BYOK" as though the two were alternatives rather than
        an architecture and a way of paying for it.
        """
        assert {component for component, _ in managed_tiers.upstream_providers()} == {
            "stt",
            "llm",
            "tts",
            "embeddings",
            "realtime",
        }

    def test_embeddings_resolve_to_a_real_provider_and_model(self):
        up = managed_tiers.resolve(managed_tiers.EMBEDDINGS_COMPONENT, "default")
        assert up.provider and up.model

    def test_the_stored_embeddings_model_name_still_resolves(self):
        """Managed configs compile with ``model="decibyl_embedding_v1"`` — a
        name from the old hosted service, not a tier. It has to land on the
        default rather than raise, or every managed knowledge base breaks."""
        assert managed_tiers.resolve(
            managed_tiers.EMBEDDINGS_COMPONENT, "decibyl_embedding_v1"
        ) == managed_tiers.resolve(managed_tiers.EMBEDDINGS_COMPONENT, "default")


class TestWhichCredentialAuthenticates:
    """One vendor key serves chat and embeddings, so embeddings must not get a
    credential slot of its own — an admin pasting the same Google key into two
    slots is one rotation away from a half-broken managed pipeline."""

    def test_embeddings_authenticate_on_the_llm_credential(self):
        from api.enums import CostComponent
        from api.services.configuration import managed_resolution

        assert (
            managed_resolution._credential_component(managed_tiers.EMBEDDINGS_COMPONENT)
            is CostComponent.LLM
        )

    def test_every_other_component_keeps_its_own(self):
        from api.enums import CostComponent
        from api.services.configuration import managed_resolution

        for component in (CostComponent.STT, CostComponent.LLM, CostComponent.TTS):
            assert managed_resolution._credential_component(component) is component


class TestEveryTierNamesSomethingThatExists:
    """A tier is a promise that a real provider and a real model are waiting.

    Nothing enforced that promise. The tier table is a plain dict of strings,
    and every consumer downstream — the config registry, the embedding factory,
    the rate card — looks its half up by name and shrugs when it misses. Three
    tiers were pointing at names nothing served, and all three failed the same
    way: late, at dial time, with an error naming a vendor the customer never
    chose.

    * ``embeddings`` said ``google``. There is no Google embeddings service
      here, and ``build_embedding_service`` falls through to OpenAI for any
      provider it does not recognise — so a managed knowledge base built an
      OpenAI client and authenticated it with a Google key.
    * ``stt`` said ``saarika:v2``, a Sarvam generation the vendor's own
      configuration class no longer offers.
    * ``realtime`` said ``gpt-4o-realtime-preview``, which appeared in no model
      list and no price book entry in this repository.

    These tests are the enforcement. They read the tier table against the
    things that consume it, so the next stale string fails here rather than on
    somebody's call.
    """

    SERVICE_TYPES = {
        "stt": ServiceType.STT,
        "llm": ServiceType.LLM,
        "tts": ServiceType.TTS,
        managed_tiers.EMBEDDINGS_COMPONENT: ServiceType.EMBEDDINGS,
        managed_tiers.REALTIME_COMPONENT: ServiceType.REALTIME,
    }

    def test_every_tier_provider_has_a_configuration_class(self):
        """The one that matters: a provider with no class in the registry for
        its service type cannot be built, and nothing on the resolution path
        checks."""
        missing = [
            (component, tier, upstream.provider)
            for (component, tier), upstream in managed_tiers._defaults().items()
            if upstream.provider not in REGISTRY[self.SERVICE_TYPES[component]]
        ]
        assert missing == []

    def test_the_embeddings_tier_is_a_provider_the_factory_actually_branches_on(self):
        """``build_embedding_service`` names azure and decibyl, then falls
        through to an OpenAI client for everything else. That fallback is why
        the Google mistake was silent — so the tier is held to the set the
        factory can genuinely serve, not merely to a registered class."""
        upstream = managed_tiers.resolve(managed_tiers.EMBEDDINGS_COMPONENT, "default")
        assert upstream.provider in {"azure", "decibyl", "openai", "openrouter"}

    def test_every_cascade_tier_has_a_rate_on_file(self):
        """An unpriced tier is worse than an expensive one: the call runs, the
        cost lands nowhere, and margin reports 100%.

        Scoped to the cascade components, whose billing provider name is the
        same string the configuration uses. Realtime is excluded on purpose —
        it is metered under the processor class name
        (``decibylopenairealtime``), not the configuration provider, and
        ``test_default_rates`` covers that mapping.
        """
        from api.enums import CostComponent
        from api.services.billing.default_rates import DEFAULT_RATES

        components = {
            "stt": CostComponent.STT,
            "llm": CostComponent.LLM,
            "tts": CostComponent.TTS,
        }
        unpriced = []
        for (component, tier), upstream in managed_tiers._defaults().items():
            cost_component = components.get(component)
            if cost_component is None:
                continue
            priced = any(
                rate.provider == upstream.provider
                and rate.component == cost_component
                and rate.model in ("", upstream.model)
                for rate in DEFAULT_RATES
            )
            if not priced:
                unpriced.append((component, tier, upstream.provider, upstream.model))
        assert unpriced == []

    #: Tier models that are deliberately absent from a provider's published
    #: list, with the reason. The list on a configuration class is what the
    #: BYOK dropdown offers a customer; a managed tier is what *we* choose to
    #: run on our own key, and the two need not be identical. But the gap has
    #: to be a decision somebody made, not a string that rotted — so each one
    #: is written down here and everything else fails.
    DELIBERATELY_OFF_LIST = {
        # The "accurate" tier. Older than the gpt-4.1/gpt-5 line the dropdown
        # offers, still served by OpenAI, and priced explicitly at $2.50/$10 in
        # default_rates — kept on purpose, not by neglect.
        ("llm", "openai", "gpt-4o"),
    }

    def test_every_tier_model_is_one_the_provider_publishes(self):
        """Catches the failure the provider check cannot see: a real vendor,
        a model name that vendor retired.

        ``saarika:v2`` passed every other test here. Sarvam is registered, the
        provider-wide Sarvam rate priced it, and nothing compared the string
        against Sarvam's own class — which defaults to ``saarika:v2.5`` and
        describes only v2.5 and saaras:v3. A managed customer would have
        transcribed every call on a name the vendor no longer answers to.
        """
        stale = []
        for (component, tier), upstream in managed_tiers._defaults().items():
            config_cls = REGISTRY[self.SERVICE_TYPES[component]].get(upstream.provider)
            if config_cls is None:
                continue  # covered, and reported, by the provider test above
            field = config_cls.model_fields.get("model")
            published = (getattr(field, "json_schema_extra", None) or {}).get(
                "examples"
            )
            if not published:
                continue  # the class publishes no list; nothing to check against
            if upstream.model in published:
                continue
            if (
                component,
                upstream.provider,
                upstream.model,
            ) in self.DELIBERATELY_OFF_LIST:
                continue
            stale.append((component, tier, upstream.provider, upstream.model))
        assert stale == []
