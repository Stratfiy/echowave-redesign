"""Charging a margin on provider usage bought with our keys.

Provider usage used to pass through at cost, with the platform fee as the only
margin. It is now marked up — 1.3x by default — and the property that makes
that safe to reason about is that **both numbers survive**: what the customer
paid and what the vendor charged us.

The alternative was baking 1.3 into the rate card, which needs no code and
destroys the margin figures: `provider_rates` would hold retail, and every
unit-economics number computed against it would read zero margin on a call that
earned 30%.

Two invariants everything else depends on:

* a **BYOK** component is charged nothing at all — the customer already paid the
  vendor, so there is no cost to mark up
* the **platform fee** is never marked up, because that would be a margin on a
  margin
"""

from __future__ import annotations

import pytest

from api.enums import CostComponent, RateUnit
from api.services.billing.cost_engine import RateSpec, compute_call_cost
from api.services.billing.usage import usage_items_from_usage_info

AT_COST = 10_000
MARKUP_1_3 = 13_000

RATES = {
    # 1000 mpaise per 1k tokens = 1 paise per 1k tokens.
    ("llm", "openai", "gpt-4o"): RateSpec(
        rate_mpaise=1_000_000, unit=RateUnit.THOUSAND_TOKENS
    ),
    ("tts", "sarvam", "bulbul"): RateSpec(
        rate_mpaise=500_000, unit=RateUnit.THOUSAND_CHARS
    ),
}


def _usage(component, provider, model, quantity):
    from api.services.billing.cost_engine import UsageItem

    return UsageItem(
        component=component, provider=provider, model=model, quantity=quantity
    )


def _cost(markup_bps, usage=None, platform_rate=0, seconds=60):
    return compute_call_cost(
        billable_seconds=seconds,
        platform_rate_mpaise=platform_rate,
        usage=usage or [_usage(CostComponent.LLM, "openai", "gpt-4o", 1000)],
        provider_rates=RATES,
        markup_bps=markup_bps,
    )


class TestTheMarkupApplies:
    def test_at_cost_is_the_identity(self):
        """10000 bps means charge exactly what the vendor charged."""
        cost = _cost(AT_COST)
        line = cost.line_items[0]
        assert line.cost_paise == line.provider_cost_paise

    def test_thirty_percent_is_charged_on_top(self):
        cost = _cost(MARKUP_1_3)
        line = cost.line_items[0]
        assert line.provider_cost_paise == 1000
        assert line.cost_paise == 1300

    def test_both_numbers_are_kept(self):
        """The whole point. Storing only the charged figure is how a margin
        report starts reading zero."""
        cost = _cost(MARKUP_1_3)
        assert cost.total_provider_cost_paise == 1000
        assert cost.total_charged_paise == 1300

    def test_margin_is_recoverable_from_a_receipt(self):
        cost = _cost(MARKUP_1_3)
        margin = cost.total_charged_paise - cost.total_provider_cost_paise
        assert margin == 300

    def test_every_provider_line_is_marked_up(self):
        cost = _cost(
            MARKUP_1_3,
            usage=[
                _usage(CostComponent.LLM, "openai", "gpt-4o", 1000),
                _usage(CostComponent.TTS, "sarvam", "bulbul", 2000),
            ],
        )
        provider_lines = [
            line
            for line in cost.line_items
            if line.component != CostComponent.PLATFORM.value
        ]
        assert len(provider_lines) == 2
        for line in provider_lines:
            assert line.cost_paise > line.provider_cost_paise

    def test_lines_are_marked_up_individually_so_a_receipt_adds_up(self):
        """Marking up the total instead would leave each printed line
        disagreeing with the sum beside it."""
        cost = _cost(
            MARKUP_1_3,
            usage=[
                _usage(CostComponent.LLM, "openai", "gpt-4o", 1000),
                _usage(CostComponent.TTS, "sarvam", "bulbul", 2000),
            ],
        )
        assert cost.total_charged_paise == sum(
            line.cost_paise for line in cost.line_items
        )


class TestWhatIsNotMarkedUp:
    def test_the_platform_fee_is_never_marked_up(self):
        """It is already our margin. Marking it up charges a margin on a
        margin."""
        cost = _cost(MARKUP_1_3, platform_rate=1_000_000, seconds=60)
        platform = [
            line
            for line in cost.line_items
            if line.component == CostComponent.PLATFORM.value
        ][0]
        assert platform.provider_cost_paise == 0
        assert platform.cost_paise == cost.platform_fee_paise

    def test_the_platform_fee_is_unchanged_by_the_markup(self):
        at_cost = _cost(AT_COST, platform_rate=1_000_000)
        marked_up = _cost(MARKUP_1_3, platform_rate=1_000_000)
        assert at_cost.platform_fee_paise == marked_up.platform_fee_paise

    def test_a_byok_component_is_never_charged_at_all(self):
        """Upstream of the markup: a customer on their own key already paid the
        vendor, so no usage item is produced and there is nothing to mark up."""
        items = usage_items_from_usage_info(
            {
                # Keyed "<processor>|||<model>" — the shape the pipeline writes.
                "llm": {
                    "openai|||gpt-4o": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 500,
                    }
                },
                "key_sources": {"llm": "byok"},
            }
        )
        assert [i for i in items if i.component == CostComponent.LLM] == []

    def test_a_managed_component_does_produce_a_line_to_mark_up(self):
        items = usage_items_from_usage_info(
            {
                "llm": {
                    "openai|||gpt-4o": {
                        "prompt_tokens": 1000,
                        "completion_tokens": 500,
                    }
                },
                "key_sources": {"llm": "managed"},
            }
        )
        assert [i for i in items if i.component == CostComponent.LLM]


class TestArithmetic:
    def test_the_markup_uses_no_floating_point(self):
        """1.3 as a float is 1.2999999999999998, which reconciles differently
        depending on the order lines are summed."""
        cost = _cost(MARKUP_1_3)
        for line in cost.line_items:
            assert isinstance(line.cost_paise, int)
            assert isinstance(line.provider_cost_paise, int)

    @pytest.mark.parametrize(
        "quantity,expected_cost,expected_charged",
        [
            (1, 1, 1),  # 0.001 paise -> 1 after per-line rounding, x1.3 -> 1
            (1000, 1000, 1300),
            (1500, 1500, 1950),
        ],
    )
    def test_rounding_is_half_up_per_line(
        self, quantity, expected_cost, expected_charged
    ):
        cost = _cost(
            MARKUP_1_3,
            usage=[_usage(CostComponent.LLM, "openai", "gpt-4o", quantity)],
        )
        line = cost.line_items[0]
        assert line.provider_cost_paise == expected_cost
        assert line.cost_paise == expected_charged

    def test_a_markup_below_cost_is_arithmetically_possible(self):
        """Not a recommendation — a guard that the multiplier is a number and
        not a special case, so a promotional rate needs no new code path."""
        cost = _cost(5_000)  # 0.5x
        line = cost.line_items[0]
        assert line.cost_paise == 500
        assert line.provider_cost_paise == 1000


class TestUncostedUsageIsUnaffected:
    def test_usage_with_no_rate_is_still_reported_uncosted(self):
        """The markup must not turn a missing rate into a silent zero charge."""
        cost = _cost(
            MARKUP_1_3,
            usage=[_usage(CostComponent.STT, "nobody", "nothing", 60)],
        )
        assert len(cost.uncosted) == 1
        assert cost.total_provider_cost_paise == 0


class TestByokRevenueIsThePlatformFeeAlone:
    """The commercial invariant behind bring-your-own-key.

    A customer on their own keys has already paid OpenAI, Sarvam and the rest
    directly. Charging them again here would be billing twice for one unit of
    usage — so on such a call the platform fee is the whole of our margin, and
    that has to hold no matter how much speech or how many tokens the call
    consumed.

    Telephony is charged separately from the model keys and is not a hole in
    the rule: bringing your own language model says nothing about who owns the
    phone line, so the two questions are asked independently. On a
    platform-managed carrier we fronted the carrier's bill and charge it on,
    excluded from the markup so it carries no margin either. On the customer's
    own carrier it is not charged at all — see
    ``services/telephony/carriage.py``.
    """

    #: A busy call — long conversation, plenty of synthesis — on the account's
    #: own keys throughout. The figures are large on purpose: if any of it
    #: leaked into a charge, the assertions below could not miss it.
    ALL_BYOK = {
        "llm": {
            "OpenAILLMService|||gpt-4o": {
                "prompt_tokens": 90_000,
                "completion_tokens": 12_000,
            }
        },
        "tts": {"SarvamTTSService|||bulbul": 40_000},
        "stt": {"DeepgramSTTService|||nova-3": 600},
        "telephony": {"twilio": 600},
        "call_duration_seconds": 600,
        # Own model keys, but a Decibyl phone number — the mix this class is
        # about, and the one where the platform fee has to be the whole margin.
        "key_sources": {
            "llm": "byok",
            "stt": "byok",
            "tts": "byok",
            "telephony": "managed",
        },
    }

    def test_no_model_usage_becomes_a_billable_item(self):
        items = usage_items_from_usage_info(self.ALL_BYOK)
        components = {item.component for item in items}

        assert CostComponent.LLM not in components
        assert CostComponent.TTS not in components
        assert CostComponent.STT not in components
        # Telephony survives: this call ran on a Decibyl number, and whose
        # keys ran the models does not change who paid the carrier.
        assert CostComponent.TELEPHONY in components

    def test_the_platform_fee_is_the_entire_margin(self):
        """The invariant stated as money. Revenue minus what we paid out equals
        the platform fee exactly — not approximately, and not plus a little
        speech synthesis that slipped through."""
        telephony_rate = {
            ("telephony", "twilio", ""): RateSpec(
                rate_mpaise=1_250_000, unit=RateUnit.MINUTE
            )
        }
        cost = compute_call_cost(
            billable_seconds=600,
            platform_rate_mpaise=200_000,
            usage=usage_items_from_usage_info(self.ALL_BYOK),
            provider_rates=telephony_rate,
            markup_bps=MARKUP_1_3,
        )

        margin = cost.total_charged_paise - cost.total_provider_cost_paise
        assert margin == cost.platform_fee_paise

    def test_telephony_is_charged_but_earns_nothing(self):
        """Charged because we pay the carrier; no margin because telephony is
        outside MARKED_UP_COMPONENTS — the rate card holds the sell price
        directly, so marking it up again would mean the number an operator
        types is not the number a customer pays."""
        telephony_rate = {
            ("telephony", "twilio", ""): RateSpec(
                rate_mpaise=1_250_000, unit=RateUnit.MINUTE
            )
        }
        cost = compute_call_cost(
            billable_seconds=600,
            platform_rate_mpaise=200_000,
            usage=usage_items_from_usage_info(self.ALL_BYOK),
            provider_rates=telephony_rate,
            markup_bps=MARKUP_1_3,
        )
        line = next(
            l for l in cost.line_items if l.component == CostComponent.TELEPHONY.value
        )

        assert line.cost_paise > 0
        assert line.cost_paise == line.provider_cost_paise

    def test_raising_the_markup_cannot_change_a_byok_bill(self):
        """The reason this is worth a test of its own: moving the managed
        multiplier is an environment variable, and it must be impossible for it
        to reach an account that brought its own keys."""
        telephony_rate = {
            ("telephony", "twilio", ""): RateSpec(
                rate_mpaise=1_250_000, unit=RateUnit.MINUTE
            )
        }

        def at(markup):
            return compute_call_cost(
                billable_seconds=600,
                platform_rate_mpaise=200_000,
                usage=usage_items_from_usage_info(self.ALL_BYOK),
                provider_rates=telephony_rate,
                markup_bps=markup,
            ).total_charged_paise

        assert at(13_000) == at(14_000) == at(20_000)

    def test_a_mixed_call_only_charges_the_managed_half(self):
        """The realistic case: an account brings a language-model key and takes
        our voice. Exactly one of the two should reach the receipt."""
        mixed = {
            **self.ALL_BYOK,
            "key_sources": {"llm": "byok", "stt": "byok", "tts": "managed"},
        }
        components = {i.component for i in usage_items_from_usage_info(mixed)}

        assert CostComponent.TTS in components
        assert CostComponent.LLM not in components
        assert CostComponent.STT not in components

    def test_an_unrecorded_component_is_charged_rather_than_given_away(self):
        """Calls predating this tracking carry no key_sources. Treating an
        absent entry as BYOK would silently stop billing for usage nobody
        confirmed was BYOK — the expensive direction to be wrong in."""
        legacy = {k: v for k, v in self.ALL_BYOK.items() if k != "key_sources"}
        components = {i.component for i in usage_items_from_usage_info(legacy)}

        assert CostComponent.LLM in components
        assert CostComponent.TTS in components
        assert CostComponent.STT in components
