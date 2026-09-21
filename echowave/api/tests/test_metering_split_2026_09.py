"""S-1: the language-model line splits into input, cached input and output.

Decided 21 Sept 2026 (Billing Gap Audit slice 1). Nothing here changes a
receipt until ``METERING_SPLIT_2026_09_ENABLED`` is on; with it off, every
run is costed exactly as before against the blended ``llm`` rows.

* the pipeline already records prompt, completion and cache-read tokens per
  model; on, they become three lines at the vendor's three prices;
* whether the prompt count already contains the cached part is a fact about
  the vendor, so the arithmetic follows the vendor and the same token is
  never priced twice nor given away;
* the ``data`` component meters what a tool bought on a platform key, per
  request, at cost, and a lookup on the customer's own connector is not a
  line;
* the split price book is the blended book un-blended -- the same models at
  the same list prices -- and is seeded only when the split is on.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from api import constants
from api.enums import CostComponent, RateUnit
from api.services.billing import default_rates, markup, usage
from api.services.billing.cost_engine import (
    MARKED_UP_COMPONENTS,
    RateSpec,
    UsageItem,
    compute_call_cost,
)
from api.services.billing.money import cost_paise


@pytest.fixture
def split_on(monkeypatch):
    monkeypatch.setattr(constants, "METERING_SPLIT_2026_09_ENABLED", True)


@pytest.fixture
def split_off(monkeypatch):
    monkeypatch.setattr(constants, "METERING_SPLIT_2026_09_ENABLED", False)


OPENAI_TURNS = {
    "llm": {
        "OpenAILLMService#0|||gpt-4o-mini": {
            "prompt_tokens": 10_000,
            "completion_tokens": 900,
            "cache_read_input_tokens": 6_000,
            "cache_creation_input_tokens": 0,
        }
    }
}

ANTHROPIC_TURNS = {
    "llm": {
        "AnthropicLLMService#0|||claude-sonnet-5": {
            "prompt_tokens": 4_000,
            "completion_tokens": 900,
            "cache_read_input_tokens": 6_000,
            "cache_creation_input_tokens": 1_000,
        }
    }
}


def _by_component(items):
    return {item.component: item for item in items}


class TestFlagOffIsUnchanged:
    def test_one_blended_line_of_prompt_plus_completion(self, split_off):
        items = usage.usage_items_from_usage_info(OPENAI_TURNS)
        assert [i.component for i in items] == [CostComponent.LLM]
        assert items[0].quantity == 10_900

    def test_data_is_not_metered(self, split_off):
        items = usage.usage_items_from_usage_info({"data": {"serper|||search": 3}})
        assert items == ()


class TestTheSplit:
    def test_openai_prompt_already_contains_the_cached_part(self, split_on):
        got = _by_component(usage.usage_items_from_usage_info(OPENAI_TURNS))
        assert got[CostComponent.LLM_INPUT].quantity == 4_000
        assert got[CostComponent.LLM_CACHED].quantity == 6_000
        assert got[CostComponent.LLM_OUTPUT].quantity == 900
        assert CostComponent.LLM not in got

    def test_anthropic_prompt_excludes_the_cache_and_writes_count_as_input(
        self, split_on
    ):
        got = _by_component(usage.usage_items_from_usage_info(ANTHROPIC_TURNS))
        assert got[CostComponent.LLM_INPUT].quantity == 5_000
        assert got[CostComponent.LLM_CACHED].quantity == 6_000
        assert got[CostComponent.LLM_OUTPUT].quantity == 900

    def test_every_line_names_the_model_and_vendor(self, split_on):
        for item in usage.usage_items_from_usage_info(OPENAI_TURNS):
            assert item.provider == "openai"
            assert item.model == "gpt-4o-mini"

    def test_a_vendor_that_reports_no_cache_produces_two_lines(self, split_on):
        info = {
            "llm": {
                "SarvamLLMService#0|||sarvam-105b": {
                    "prompt_tokens": 3_000,
                    "completion_tokens": 300,
                }
            }
        }
        got = _by_component(usage.usage_items_from_usage_info(info))
        assert set(got) == {CostComponent.LLM_INPUT, CostComponent.LLM_OUTPUT}
        assert got[CostComponent.LLM_INPUT].quantity == 3_000

    def test_the_same_tokens_are_never_counted_twice(self, split_on):
        got = usage.usage_items_from_usage_info(OPENAI_TURNS)
        assert sum(i.quantity for i in got) == 10_900

    def test_a_byok_model_still_produces_no_line(self, split_on):
        info = {**OPENAI_TURNS, "key_sources": {"llm": "byok"}}
        assert usage.usage_items_from_usage_info(info) == ()


class TestTheDataComponent:
    def test_a_platform_key_lookup_is_one_line_per_request(self, split_on):
        items = usage.usage_items_from_usage_info(
            {"data": {"serper|||search": 3, "crawl4ai|||fetch": 12}}
        )
        got = {(i.provider, i.model): i for i in items}
        assert got[("serper", "search")].component == CostComponent.DATA
        assert got[("serper", "search")].quantity == 3
        assert got[("crawl4ai", "fetch")].quantity == 12

    def test_the_customers_own_connector_is_not_a_line(self, split_on):
        items = usage.usage_items_from_usage_info(
            {"data": {"apollo|||enrich": 5}, "key_sources": {"data": "byok"}}
        )
        assert items == ()

    def test_priced_per_request_at_cost(self):
        assert cost_paise(quantity=3, rate_mpaise=150_000, unit=RateUnit.EACH) == 450
        assert markup.default_markup_bps(CostComponent.DATA) == 10_000


class TestTheReceipt:
    RATES: ClassVar[dict] = {
        ("llm_input", "openai", "gpt-4o-mini"): RateSpec(
            rate_mpaise=1_440, unit=RateUnit.THOUSAND_TOKENS
        ),
        ("llm_cached", "openai", "gpt-4o-mini"): RateSpec(
            rate_mpaise=720, unit=RateUnit.THOUSAND_TOKENS
        ),
        ("llm_output", "openai", "gpt-4o-mini"): RateSpec(
            rate_mpaise=5_760, unit=RateUnit.THOUSAND_TOKENS
        ),
        ("data", "serper", ""): RateSpec(rate_mpaise=150_000, unit=RateUnit.EACH),
    }

    def test_three_model_lines_and_a_data_line_each_marked_up_by_their_own_rule(
        self, split_on
    ):
        items = (
            *usage.usage_items_from_usage_info(OPENAI_TURNS),
            UsageItem(CostComponent.DATA, "serper", 3, model="search"),
        )
        cost = compute_call_cost(
            billable_seconds=0,
            platform_rate_mpaise=0,
            usage=items,
            provider_rates=self.RATES,
            markup_overrides={
                key: markup.default_markup_bps(key[0]) for key in self.RATES
            },
        )
        by = {line.component: line for line in cost.line_items}
        # 4,000 input tokens at 1,440 mpaise per 1k = 5.76 paise, x2.0
        assert by["llm_input"].provider_cost_paise == 6
        assert by["llm_input"].cost_paise == 12
        assert by["llm_cached"].provider_cost_paise == 4
        assert by["llm_output"].provider_cost_paise == 5
        # 3 requests at 1.50 each, at cost
        assert by["data"].provider_cost_paise == 450
        assert by["data"].cost_paise == 450
        assert cost.uncosted == ()

    def test_the_split_lines_are_marked_up_like_the_model(self):
        for component in CostComponent.llm_split_components():
            assert component.value in MARKED_UP_COMPONENTS
            assert markup.default_markup_bps(component) == markup.default_markup_bps(
                CostComponent.LLM
            )

    def test_the_split_lines_are_provider_components(self):
        for component in (*CostComponent.llm_split_components(), CostComponent.DATA):
            assert component in CostComponent.provider_components()


class TestTheSplitPriceBook:
    def test_every_blended_model_has_its_three_rows(self):
        blended = {(r.provider, r.model) for r in default_rates.LLM_RATES}
        split = {(r.provider, r.model) for r in default_rates.LLM_SPLIT_RATES}
        assert blended == split
        for key in blended:
            rows = [
                r for r in default_rates.LLM_SPLIT_RATES if (r.provider, r.model) == key
            ]
            assert {r.component for r in rows} == set(
                CostComponent.llm_split_components()
            )

    def test_the_blend_is_the_split_at_the_documented_share(self):
        """Edit a price on one side and forget the other, and this fails."""
        split = {
            (r.provider, r.model, r.component): r.usd_per_unit
            for r in default_rates.LLM_SPLIT_RATES
        }
        for blended in default_rates.LLM_RATES:
            key = (blended.provider, blended.model)
            expected = split[
                (*key, CostComponent.LLM_INPUT)
            ] * default_rates.LLM_INPUT_SHARE + split[
                (*key, CostComponent.LLM_OUTPUT)
            ] * (1 - default_rates.LLM_INPUT_SHARE)
            assert blended.usd_per_unit == pytest.approx(expected, rel=1e-9), key

    def test_cached_input_is_never_dearer_than_input(self):
        split = {
            (r.provider, r.model, r.component): r.usd_per_unit
            for r in default_rates.LLM_SPLIT_RATES
        }
        for (provider, model, component), price in split.items():
            if component is CostComponent.LLM_CACHED:
                assert price <= split[(provider, model, CostComponent.LLM_INPUT)]

    def test_anthropic_and_openai_cache_discounts_are_the_published_ones(self):
        assert default_rates.cached_input_share("anthropic") == 0.1
        assert default_rates.cached_input_share("openai", "gpt-4o-mini") == 0.5
        assert default_rates.cached_input_share("openai", "gpt-5") == 0.1
        assert default_rates.cached_input_share("sarvam") == 1.0

    def test_split_rows_are_quoted_per_thousand_tokens(self):
        for row in default_rates.LLM_SPLIT_RATES:
            assert row.unit is RateUnit.THOUSAND_TOKENS
            assert row.usd_per_unit > 0

    def test_seeded_only_when_the_split_is_on(self, split_off, monkeypatch):
        assert default_rates.rates() == default_rates.DEFAULT_RATES
        monkeypatch.setattr(constants, "METERING_SPLIT_2026_09_ENABLED", True)
        assert set(default_rates.rates()) == set(default_rates.DEFAULT_RATES) | set(
            default_rates.LLM_SPLIT_RATES
        )

    def test_no_duplicate_keys_once_seeded(self, split_on):
        keys = [(r.provider, r.model, r.component) for r in default_rates.rates()]
        assert len(keys) == len(set(keys))
