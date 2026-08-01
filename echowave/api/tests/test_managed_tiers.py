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
        assert {component for component, _ in managed_tiers.upstream_providers()} == {
            "stt",
            "llm",
            "tts",
        }
