"""What the managed tier picker offers, and what it must keep resolving.

The picker listed five tiers where three of them — ``fast``, ``lite`` and
``zen`` — resolved to the same model. That is worse than listing three: a
customer who picks ``lite`` over ``fast`` believes they have made a decision
and has not, and the first time they ask why "lite" costs the same as "fast"
the honest answer is that it is the same model.

Two properties, and the second is the one that costs money if it breaks.

**What is offered is genuinely distinct.** Every tier in the picker resolves to
a different model, so the ladder means something.

**What was offered still resolves.** ``lite`` and ``zen`` are retired from the
picker but stay mapped. Dropping them from the resolver would fall those agents
back to ``default`` — a different model at more than twice the blended price —
silently, mid-campaign, on configurations nobody edited.
"""

import pytest

from api.services.configuration import managed_tiers
from api.services.configuration.registry import DECIBYL_LLM_MODELS


class TestWhatIsOffered:
    def test_the_picker_offers_exactly_the_offered_tiers(self):
        """One list, not two that drift. The picker renders from the registry
        constant and the resolver reads its own; they have to agree."""
        assert tuple(DECIBYL_LLM_MODELS) == managed_tiers.OFFERED_LLM_TIERS

    def test_every_offered_tier_resolves_to_a_different_model(self):
        """The property the whole change exists for."""
        resolved = [
            managed_tiers.resolve("llm", tier)
            for tier in managed_tiers.OFFERED_LLM_TIERS
        ]
        pairs = [(up.provider, up.model) for up in resolved]
        assert len(set(pairs)) == len(pairs), pairs

    def test_the_ladder_reads_cheapest_first(self):
        """Order is part of the offer. A picker whose first option is the
        dearest reads as a default nobody chose."""
        assert managed_tiers.OFFERED_LLM_TIERS[0] == "fast"
        assert managed_tiers.OFFERED_LLM_TIERS[-1] == "accurate"

    def test_nothing_retired_is_still_on_offer(self):
        for tier in managed_tiers.RETIRED_LLM_TIERS:
            assert tier not in DECIBYL_LLM_MODELS


class TestWhatMustKeepWorking:
    @pytest.mark.parametrize("tier", managed_tiers.RETIRED_LLM_TIERS)
    def test_a_retired_tier_still_resolves(self, tier):
        upstream = managed_tiers.resolve("llm", tier)
        assert upstream.provider and upstream.model

    @pytest.mark.parametrize("tier", managed_tiers.RETIRED_LLM_TIERS)
    def test_a_retired_tier_resolves_to_what_it_always_did(self, tier):
        """Not to `default`. Falling back would move a live agent from a
        $0.19/1M model to a $0.48/1M one without anyone editing anything."""
        upstream = managed_tiers.resolve("llm", tier)
        assert (upstream.provider, upstream.model) == (
            "google",
            "gemini-2.5-flash-lite",
        )

    def test_every_name_the_resolver_knows_is_still_listed(self):
        """`LLM_TIERS` is the union. If a name leaves it, a stored config
        naming that tier stops resolving to itself and starts resolving to
        `default` through the unknown-tier fallback."""
        assert set(managed_tiers.OFFERED_LLM_TIERS) | set(
            managed_tiers.RETIRED_LLM_TIERS
        ) == set(managed_tiers.LLM_TIERS)

    def test_an_unknown_tier_still_falls_back_rather_than_raising(self):
        """A tier we have genuinely never had must not fail a call at dial
        time. This is the behaviour retired tiers are deliberately kept out
        of."""
        upstream = managed_tiers.resolve("llm", "not-a-tier")
        assert (upstream.provider, upstream.model) == (
            managed_tiers.resolve("llm", "default").provider,
            managed_tiers.resolve("llm", "default").model,
        )


class TestTheOverrideStillApplies:
    def test_a_retired_tier_can_still_be_repointed(self, monkeypatch):
        """The env override is what makes a tier movable without a release,
        and it has to keep working for the retired names too — they are the
        ones most likely to need moving when their model is withdrawn."""
        monkeypatch.setenv("MANAGED_LLM_ZEN", "openai:gpt-4o-mini")
        upstream = managed_tiers.resolve("llm", "zen")
        assert (upstream.provider, upstream.model) == ("openai", "gpt-4o-mini")

    def test_an_offered_tier_can_be_repointed(self, monkeypatch):
        monkeypatch.setenv("MANAGED_LLM_FAST", "sarvam:sarvam-m")
        upstream = managed_tiers.resolve("llm", "fast")
        assert (upstream.provider, upstream.model) == ("sarvam", "sarvam-m")
