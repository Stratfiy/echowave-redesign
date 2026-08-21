"""The India badge, and why it is computed rather than stored.

A doctor asking "where does my patient's voice go?" is asking one narrow
question. Answering it with a flag somebody set is how the answer goes stale:
moving a tier is a superadmin action taken without a release, so a stored badge
would keep claiming India the moment after somebody pointed the Lite brain at a
US vendor. For health data that is not a marketing slip, it is a DPDP problem.

So the tests here are mostly about the badge *going away* correctly. Anyone can
make a badge appear.
"""

from __future__ import annotations

import pytest

from api.services.configuration import managed_tiers
from api.services.configuration.residency import INDIA_PROCESSED, assess


@pytest.fixture(autouse=True)
def _clean_overrides():
    managed_tiers._OVERRIDES = {}
    yield
    managed_tiers._OVERRIDES = {}


class TestTheOneStackThatQualifies:
    def test_sarvam_speech_with_the_sarvam_brain_is_india_only(self):
        assert assess(architecture="pipeline", llm_tier="lite").india_only is True

    def test_nothing_is_reported_as_leaving(self):
        assert assess(architecture="pipeline", llm_tier="lite").leaves_india == ()


class TestWhatTakesTheBadgeAway:
    def test_a_foreign_brain_loses_it(self):
        """Normal and Smart are OpenAI, so only Lite qualifies."""
        result = assess(architecture="pipeline", llm_tier="default")

        assert result.india_only is False
        assert "the language model" in result.leaves_india

    def test_a_knowledge_base_loses_it_even_on_an_all_indian_call(self):
        """The case a declared badge would miss.

        Every model on the call is Indian and nothing about the call
        configuration changed — but embeddings run on OpenAI, so text goes
        abroad at ingest.
        """
        result = assess(
            architecture="pipeline", llm_tier="lite", has_knowledge_base=True
        )

        assert result.india_only is False
        assert "knowledge base indexing" in result.leaves_india

    def test_speech_to_speech_loses_it(self):
        """No Indic realtime model is good enough yet, so both tiers are
        foreign. If one ships, pointing the tier at it earns the badge with no
        code change here."""
        for tier in ("natural", "premium"):
            assert (
                assess(architecture="realtime", realtime_tier=tier).india_only is False
            )

    def test_every_foreign_component_is_named_not_just_the_first(self):
        """A clinic asking what leaves deserves the whole answer."""
        managed_tiers._OVERRIDES[("tts", "default")] = managed_tiers.ManagedUpstream(
            "elevenlabs", "eleven_flash_v2_5"
        )
        result = assess(architecture="pipeline", llm_tier="default")

        assert "the language model" in result.leaves_india
        assert "the voice" in result.leaves_india
        assert "and" in result.reason


class TestItFollowsTheTierRatherThanALabel:
    def test_moving_a_tier_abroad_removes_the_badge_immediately(self):
        """The property that makes this safe to show a doctor."""
        assert assess(architecture="pipeline", llm_tier="lite").india_only is True

        managed_tiers._OVERRIDES[("llm", "lite")] = managed_tiers.ManagedUpstream(
            "openai", "gpt-4.1-mini"
        )

        assert assess(architecture="pipeline", llm_tier="lite").india_only is False

    def test_moving_a_tier_home_earns_it_with_no_code_change(self):
        """When Sarvam ships a realtime model, this is all it takes."""
        managed_tiers._OVERRIDES[("realtime", "natural")] = (
            managed_tiers.ManagedUpstream("sarvam", "some-future-realtime")
        )

        assert (
            assess(architecture="realtime", realtime_tier="natural").india_only is True
        )


class TestTheAllowListIsTheSafeDirection:
    def test_an_unclassified_vendor_does_not_earn_the_badge(self):
        """A provider added to the platform must be *proved* Indian. Forgetting
        to classify one produces a badge that does not appear, never one that
        lies."""
        managed_tiers._OVERRIDES[("stt", "default")] = managed_tiers.ManagedUpstream(
            "some-new-indian-vendor", "model-x"
        )

        assert assess(architecture="pipeline", llm_tier="lite").india_only is False

    def test_the_list_is_short_and_explicit(self):
        assert INDIA_PROCESSED == frozenset({"sarvam"})
