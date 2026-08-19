"""Charging for Decibyl's own work rather than a vendor's.

Two mechanisms. A BYOK call produces no provider line to earn a markup on, so
its **platform rate** is uplifted — by how much depends on which key the
customer brought, because the voice is worth about thirty times what the
language model is. And a call that used the knowledge base or post-call QA
consumed a feature every competitor bills separately and we shipped free.

What these tests defend is that neither disturbed the invariants the rest of
billing rests on: an invoice still reconciles against its own line items,
provider cost still counts only money paid to third parties, and a managed call
with no add-ons produces exactly the receipt it did before.
"""

import random

import pytest

from api.enums import CostComponent, RateUnit
from api.services.billing.addons import (
    CALL_QA,
    KNOWLEDGE_BASE,
    addon_keys_from_usage_info,
    by_key,
    record_addon_used,
)
from api.services.billing.cost_engine import RateSpec, UsageItem, compute_call_cost
from api.services.billing.money import DEFAULT_PLATFORM_RATE_MPAISE
from api.services.billing.usage import byok_platform_tier

RATE = DEFAULT_PLATFORM_RATE_MPAISE  # ₹2.00 per minute

PROVIDER_RATES = {
    ("tts", "sarvam", ""): RateSpec(rate_mpaise=1500, unit=RateUnit.THOUSAND_CHARS),
}
USAGE = (UsageItem(component=CostComponent.TTS, provider="sarvam", quantity=2300),)


def _lines(cost, component):
    return [line for line in cost.line_items if line.component == component]


# --- the charges must be invisible until they are switched on ---------------


def test_managed_call_receipt_is_unchanged_when_no_fee_applies():
    """The regression that matters most: every existing account's invoice.

    Both charges default to off, so shipping them must not add a line, a paisa
    or a zero-valued row to a call that would not have been charged one.
    """
    cost = compute_call_cost(
        billable_seconds=45,
        platform_rate_mpaise=RATE,
        usage=USAGE,
        provider_rates=PROVIDER_RATES,
    )

    assert [line.component for line in cost.line_items] == ["tts", "platform"]
    assert cost.addon_fee_paise == 0


def test_a_zero_rate_emits_no_line_rather_than_a_zero_one():
    """Switching a charge off should leave no trace on the receipt.

    A ₹0.00 line is worse than no line: it invites the question of what it was
    for, on an invoice whose whole selling point is that it can be audited.
    """
    cost = compute_call_cost(
        billable_seconds=60,
        platform_rate_mpaise=RATE,
        addon_rates={CALL_QA: 0},
    )

    assert _lines(cost, CostComponent.ADDON.value) == []


# --- which BYOK tier a call falls in -----------------------------------------


@pytest.mark.parametrize(
    ("key_sources", "expected"),
    [
        # The voice is the expensive one and wins whenever it is theirs.
        ({"llm": "byok", "stt": "byok", "tts": "byok"}, "tts"),
        ({"llm": "managed", "stt": "managed", "tts": "byok"}, "tts"),
        ({"llm": "byok", "stt": "byok", "tts": "managed"}, "stt"),
        ({"llm": "managed", "stt": "byok", "tts": "managed"}, "stt"),
        # The language model alone never moves the tier.
        ({"llm": "byok", "stt": "managed", "tts": "managed"}, "managed"),
        ({"llm": "managed", "stt": "managed", "tts": "managed"}, "managed"),
    ],
)
def test_the_tier_is_cut_on_which_key_not_how_many(key_sources, expected):
    """Counting components prices a cheap key like an expensive one.

    On a typical Indic minute the margin given up is ~$0.014 for the voice,
    ~$0.002 for transcription and ~$0.0005 for the language model. A flat
    per-component uplift overcharges the last by an order of magnitude — far
    enough that the account's bill *rose* when they brought their own key.
    """
    assert byok_platform_tier({"key_sources": key_sources}) == expected


def test_the_language_model_is_never_a_tier_on_its_own():
    """Explicit, because it is the case the arithmetic turns on: charging for
    a BYOK language model costs the customer more in fee than it saves us in
    margin, which is an invoice nobody can defend."""
    assert byok_platform_tier({"key_sources": {"llm": "byok"}}) == "managed"


def test_a_run_with_no_recorded_key_sources_reads_as_managed():
    """Calls costed before key sources were tracked have no facts to bill on,
    so they keep paying what the account was already paying."""
    assert byok_platform_tier({}) == "managed"
    assert byok_platform_tier(None) == "managed"


# --- add-ons ----------------------------------------------------------------


def test_each_addon_gets_its_own_named_line():
    """A receipt has to say which feature was charged, not just that one was."""
    cost = compute_call_cost(
        billable_seconds=60,
        platform_rate_mpaise=RATE,
        addon_rates={CALL_QA: 20_000, KNOWLEDGE_BASE: 5_000},
    )

    lines = _lines(cost, CostComponent.ADDON.value)
    assert {line.provider for line in lines} == {CALL_QA, KNOWLEDGE_BASE}
    assert all(line.provider_cost_paise == 0 for line in lines)
    assert cost.addon_fee_paise == sum(line.cost_paise for line in lines)


def test_addon_lines_are_ordered_deterministically():
    """Two calls that used the same features must produce the same receipt.

    Add-ons arrive from a set on the run, whose iteration order is not stable
    between processes.
    """
    rates = {CALL_QA: 20_000, KNOWLEDGE_BASE: 5_000}
    first = compute_call_cost(
        billable_seconds=60, platform_rate_mpaise=RATE, addon_rates=rates
    )
    second = compute_call_cost(
        billable_seconds=60,
        platform_rate_mpaise=RATE,
        addon_rates=dict(reversed(list(rates.items()))),
    )

    assert [line.provider for line in _lines(first, CostComponent.ADDON.value)] == [
        line.provider for line in _lines(second, CostComponent.ADDON.value)
    ]


# --- both charges ride the pulse -------------------------------------------


def test_fees_are_charged_on_pulse_rounded_time_like_the_platform_fee():
    """The 15-second pulse is the product's loudest claim.

    A fee billed on whole minutes while the platform fee is billed on pulses
    would quietly undo it on exactly the short calls it was built for.
    """
    cost = compute_call_cost(
        billable_seconds=40,
        platform_rate_mpaise=240_000,  # ₹2.40/min, as an uplifted BYOK rate
        addon_rates={CALL_QA: 240_000},
    )

    assert cost.billed_seconds == 45
    for component in (CostComponent.PLATFORM.value, CostComponent.ADDON.value):
        (line,) = _lines(cost, component)
        assert line.units == 45
        # 45s at ₹2.40/min is 180 paise; a whole minute would be 240.
        assert line.cost_paise == 180


# --- reading what the runtime recorded --------------------------------------


def test_addons_are_reported_in_catalogue_order_and_deduplicated():
    assert addon_keys_from_usage_info({"addons": [CALL_QA, KNOWLEDGE_BASE]}) == (
        KNOWLEDGE_BASE,
        CALL_QA,
    )
    assert addon_keys_from_usage_info({"addons": [CALL_QA, CALL_QA]}) == (CALL_QA,)


@pytest.mark.parametrize(
    "usage_info",
    [None, {}, {"addons": "knowledge_base"}, {"addons": ["nope"]}, {"addons": 7}],
)
def test_malformed_addon_usage_does_not_break_costing(usage_info):
    """``usage_info`` is free-form JSON written by the pipeline.

    A bad value there must not stop a call being costed — an uncosted call is
    a call the account used for free.
    """
    assert addon_keys_from_usage_info(usage_info) == ()


def test_an_unknown_addon_key_is_not_billed():
    """Usage recorded by a newer runtime than the costing code is not an error."""
    assert by_key("something_we_removed") is None


def test_recording_an_addon_is_idempotent():
    usage_info: dict = {}
    record_addon_used(usage_info, KNOWLEDGE_BASE)
    record_addon_used(usage_info, KNOWLEDGE_BASE)

    assert usage_info == {"addons": [KNOWLEDGE_BASE]}


def test_recording_an_addon_survives_a_malformed_existing_value():
    usage_info: dict = {"addons": "garbage"}
    record_addon_used(usage_info, CALL_QA)

    assert usage_info["addons"] == [CALL_QA]


# --- the invariant the whole package rests on -------------------------------


def test_invoice_reconciles_against_its_own_line_items_with_fees_applied():
    """``total == sum(lines)`` has to survive the two new line types.

    Asserted over random calls rather than a chosen one because the failure
    mode is a rounding drift that only appears at particular durations.
    """
    rng = random.Random(7)

    for _ in range(2_000):
        cost = compute_call_cost(
            billable_seconds=rng.randint(1, 900),
            platform_rate_mpaise=rng.choice([RATE, RATE + 20_000, RATE + 150_000]),
            markup_bps=14_000,
            usage=USAGE,
            provider_rates=PROVIDER_RATES,
            addon_rates=rng.choice(
                [{}, {CALL_QA: 20_000}, {CALL_QA: 20_000, KNOWLEDGE_BASE: 5_000}]
            ),
        )

        assert cost.total_charged_paise == sum(
            line.cost_paise for line in cost.line_items
        )
        # Fees never leak into provider cost, at any duration.
        assert cost.total_provider_cost_paise == sum(
            line.provider_cost_paise for line in cost.line_items
        )
