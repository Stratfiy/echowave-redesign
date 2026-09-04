"""The catalogue and the rate card have to agree on what a slot is called.

Two slots are filed under one name in ``platform_models`` and another in
``provider_rates``, and a mismatch is invisible: nothing raises, the row simply
reads as unpriced, ``sellable`` drops it, and the customer's picker is empty for
that slot however many models an operator ticked.

``estimator.rate_card_provider`` already documents what this cost once on the
estimate path -- a quote of Rs2.76 against an invoice of Rs25.79. These tests
hold the catalogue to the same seam so it cannot drift back.

Deliberately free of the database and of pipecat: the thing under test is a
name translation, and a test that needs a migrated schema to check a string is
a test nobody runs.
"""

from __future__ import annotations

from api.enums import CostComponent
from api.services.billing.default_rates import REALTIME_RATES
from api.services.billing.estimator import rate_card_provider
from api.services.configuration import managed_tiers
from api.services.configuration.model_catalogue import _rate_card_slot


def test_realtime_is_priced_as_llm_under_its_service_class_name():
    assert _rate_card_slot("realtime", "openai_realtime") == (
        "llm",
        "decibylopenairealtime",
    )
    assert _rate_card_slot("realtime", "google_realtime") == (
        "llm",
        "decibylgeminilive",
    )


def test_embeddings_is_priced_under_the_singular_component():
    """``CostComponent.EMBEDDING``, not the catalogue's ``embeddings``."""
    assert _rate_card_slot("embeddings", "openai") == ("embedding", "openai")


def test_ordinary_slots_pass_through_untouched():
    for component in ("stt", "llm", "tts"):
        assert _rate_card_slot(component, "sarvam") == (component, "sarvam")


def test_it_uses_the_same_seam_the_estimate_does():
    """One translation, not two that can drift apart."""
    for provider in ("openai_realtime", "google_realtime", "azure_realtime"):
        component, translated = _rate_card_slot("realtime", provider)
        assert component == CostComponent.LLM.value
        assert translated == rate_card_provider(provider)


def test_every_realtime_tier_lands_on_a_rate_that_exists():
    """The test that would have caught it.

    A tier resolves to a vendor name; the catalogue asks the rate card about
    that name. If the two vocabularies disagree the tier is unsellable, which
    is what ``realtime`` was: zero models offered, both rows priced by nothing.
    """
    priced = {rate.provider for rate in REALTIME_RATES}

    for tier in managed_tiers.REALTIME_TIERS:
        upstream = managed_tiers.resolve(managed_tiers.REALTIME_COMPONENT, tier)
        _, translated = _rate_card_slot("realtime", upstream.provider)
        assert translated in priced, (
            f"realtime tier {tier!r} resolves to {upstream.provider!r}, which the "
            f"catalogue looks up as {translated!r} -- not one of {sorted(priced)}"
        )
