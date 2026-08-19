"""The two charges that bill Decibyl's own work rather than a vendor's.

Both exist because the platform fee alone under-collects on calls that cost us
the same to run as any other. A BYOK call produces no provider line to earn a
markup on; a call that used the knowledge base or post-call QA consumed a
feature every competitor charges separately for.

What these tests defend is that adding them did not disturb the invariants the
rest of billing rests on: an invoice still reconciles against its own line
items, provider cost still counts only money paid to third parties, and a
managed call with no add-ons produces exactly the receipt it did before.
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
from api.services.billing.usage import byok_model_share

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
    assert cost.orchestration_fee_paise == 0
    assert cost.addon_fee_paise == 0


def test_a_zero_rate_emits_no_line_rather_than_a_zero_one():
    """Switching a charge off should leave no trace on the receipt.

    A ₹0.00 line is worse than no line: it invites the question of what it was
    for, on an invoice whose whole selling point is that it can be audited.
    """
    cost = compute_call_cost(
        billable_seconds=60,
        platform_rate_mpaise=RATE,
        orchestration_rate_mpaise=0,
        addon_rates={CALL_QA: 0},
    )

    assert _lines(cost, CostComponent.ORCHESTRATION.value) == []
    assert _lines(cost, CostComponent.ADDON.value) == []


# --- the orchestration fee --------------------------------------------------


def test_orchestration_fee_is_charged_per_billable_minute():
    cost = compute_call_cost(
        billable_seconds=60,
        platform_rate_mpaise=RATE,
        orchestration_rate_mpaise=300_000,  # ₹3.00/min
    )

    (line,) = _lines(cost, CostComponent.ORCHESTRATION.value)
    assert line.cost_paise == 300
    assert line.provider is None


def test_orchestration_fee_is_our_revenue_not_a_pass_through():
    """It must never inflate reported provider cost.

    If it did, the unit-economics screen would show our own fee as money paid
    to a vendor and every margin figure on it would be understated.
    """
    cost = compute_call_cost(
        billable_seconds=60,
        platform_rate_mpaise=RATE,
        usage=USAGE,
        provider_rates=PROVIDER_RATES,
        orchestration_rate_mpaise=300_000,
    )

    (line,) = _lines(cost, CostComponent.ORCHESTRATION.value)
    assert line.provider_cost_paise == 0
    assert cost.total_provider_cost_paise == sum(
        item.provider_cost_paise for item in cost.line_items
    )


@pytest.mark.parametrize(
    ("key_sources", "expected"),
    [
        ({"llm": "byok", "stt": "byok", "tts": "byok"}, (3, 3)),
        ({"llm": "byok", "stt": "managed", "tts": "managed"}, (1, 3)),
        ({"llm": "managed", "stt": "managed", "tts": "managed"}, (0, 3)),
        ({"llm": "managed"}, (0, 1)),
        ({}, (0, 0)),
    ],
)
def test_byok_share_is_a_fraction_not_a_boolean(key_sources, expected):
    """Part-BYOK is the common case and must not be billed as if it were full.

    An account that brings only its TTS key still pays a marked-up rate on the
    STT and LLM we bought for it. Charging that account the same orchestration
    fee as one that brought every key would bill it twice for one call.
    """
    assert byok_model_share({"key_sources": key_sources}) == expected


def test_a_run_with_no_recorded_key_sources_is_charged_nothing_extra():
    """Calls costed before key sources were tracked have no facts to bill on.

    Inventing a fee for them would be charging for a property of the call that
    nobody recorded.
    """
    assert byok_model_share({}) == (0, 0)
    assert byok_model_share(None) == (0, 0)


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
        platform_rate_mpaise=RATE,
        orchestration_rate_mpaise=240_000,  # ₹2.40/min
        addon_rates={CALL_QA: 240_000},
    )

    assert cost.billed_seconds == 45
    for component in (CostComponent.ORCHESTRATION.value, CostComponent.ADDON.value):
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
            platform_rate_mpaise=RATE,
            markup_bps=14_000,
            usage=USAGE,
            provider_rates=PROVIDER_RATES,
            orchestration_rate_mpaise=rng.choice([0, 100_000, 300_000]),
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
