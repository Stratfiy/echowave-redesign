"""The managed offering: a customer with no API key, running on ours.

This is the seam that decides whether a "Decibyl" section becomes a real vendor
call or fails at dial time, so the tests are about the two things that break it
— a tier nobody mapped, and a platform key nobody stored.
"""

import pytest

from api.enums import CostComponent
from api.services.configuration import managed_tiers


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
        """Embeddings belong here even though they are not a billing component.

        A managed configuration emits an embeddings section for knowledge-base
        retrieval. While it was missing from this map the section kept
        ``provider=decibyl`` all the way to the embeddings factory, and the
        model-override screen showed a second "Invalid ServiceProviders.DECIBYL
        API key" that no customer could act on.
        """
        assert {component for component, _ in managed_tiers.upstream_providers()} == {
            "stt",
            "llm",
            "tts",
            "embeddings",
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
