"""Query-time embedding usage during in-call knowledge-base retrieval.

Before this, embedding had no cost component at all — a real vendor call, on
our key by default, with no rate row and not even reported as ``uncosted``.
This covers the vertical slice from a recorded embedding call through to a
marked-up receipt line: the aggregator records it, ``usage.py`` turns it into
a costable item respecting BYOK, and the cost engine prices and marks it up
like every other component we buy and resell.

Deliberately out of scope: ingestion-time embedding (document upload). That
is a different event with no call to attach a line item to — see
``PRICING-DECISIONS.md``.
"""

import pytest

from api.enums import CostComponent, RateUnit
from api.services.billing.cost_engine import RateSpec, UsageItem, compute_call_cost
from api.services.billing.usage import usage_items_from_usage_info
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator


class TestTheAggregatorRecordsIt:
    def test_a_registered_call_appears_in_the_serialized_usage(self):
        agg = PipelineMetricsAggregator()
        agg.register_embedding_usage(provider="openai", model="text-embedding-3-small", tokens=42)

        usage = agg.get_all_usage_metrics_serialized()

        assert usage["embedding"] == {"openai|||text-embedding-3-small": 42}

    def test_repeated_calls_in_one_conversation_accumulate(self):
        """A conversation that searches the knowledge base three times pays
        for three embeddings."""
        agg = PipelineMetricsAggregator()
        agg.register_embedding_usage(provider="openai", model="text-embedding-3-small", tokens=10)
        agg.register_embedding_usage(provider="openai", model="text-embedding-3-small", tokens=15)

        usage = agg.get_all_usage_metrics_serialized()

        assert usage["embedding"] == {"openai|||text-embedding-3-small": 25}

    def test_a_zero_or_negative_token_count_is_not_recorded(self):
        """Mirrors how the rest of this codebase treats a non-positive
        quantity as nothing happened, not as a free line."""
        agg = PipelineMetricsAggregator()
        agg.register_embedding_usage(provider="openai", model="text-embedding-3-small", tokens=0)

        assert agg.get_all_usage_metrics_serialized()["embedding"] == {}

    def test_a_call_with_no_retrieval_reports_an_empty_dict_not_a_missing_key(self):
        """So a caller can always index usage_info["embedding"] without a
        KeyError, the same guarantee llm/tts/stt already give."""
        agg = PipelineMetricsAggregator()
        assert agg.get_all_usage_metrics_serialized()["embedding"] == {}

    def test_reset_clears_it(self):
        agg = PipelineMetricsAggregator()
        agg.register_embedding_usage(provider="openai", model="text-embedding-3-small", tokens=10)
        agg.reset_metrics()
        assert agg.get_all_usage_metrics_serialized()["embedding"] == {}


class TestUsageExtraction:
    def test_embedding_usage_becomes_a_costable_item(self):
        usage_info = {
            "embedding": {"openai|||text-embedding-3-small": 50},
        }

        items = usage_items_from_usage_info(usage_info)

        assert items == (
            UsageItem(
                component=CostComponent.EMBEDDING,
                provider="openai",
                quantity=50,
                model="text-embedding-3-small",
            ),
        )

    def test_a_zero_quantity_entry_is_dropped(self):
        """A line item costing nothing only adds noise to a receipt — the
        same rule llm/tts/stt already follow."""
        usage_info = {"embedding": {"openai|||text-embedding-3-small": 0}}
        assert usage_items_from_usage_info(usage_info) == ()

    def test_byok_embedding_produces_no_line(self):
        """The account already paid the vendor directly for its own key; a
        Decibyl receipt for the same usage would be a double charge."""
        usage_info = {
            "embedding": {"openai|||text-embedding-3-small": 50},
            "key_sources": {"embedding": "byok"},
        }

        assert usage_items_from_usage_info(usage_info) == ()

    def test_managed_embedding_alongside_byok_llm_only_bills_the_embedding(self):
        """Each component's key ownership is independent — a call can bring
        its own LLM key and still run embeddings on ours."""
        usage_info = {
            "llm": {"openai|||gpt-4o-mini": {"prompt_tokens": 100, "completion_tokens": 50}},
            "embedding": {"openai|||text-embedding-3-small": 50},
            "key_sources": {"llm": "byok", "embedding": "managed"},
        }

        items = usage_items_from_usage_info(usage_info)

        assert len(items) == 1
        assert items[0].component == CostComponent.EMBEDDING

    def test_no_embedding_key_produces_no_line(self):
        """A call that never retrieved from the knowledge base recorded no
        embedding usage_info key at all, and that must not synthesise a line
        from nothing."""
        assert usage_items_from_usage_info({"llm": {}}) == ()


class TestCostEngineMarksItUp:
    def test_an_embedding_line_is_priced_and_marked_up_like_any_other_component(self):
        cost = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=20_000,
            markup_bps=17_000,  # 1.7x
            usage=(
                UsageItem(
                    CostComponent.EMBEDDING, "openai", 1_000, model="text-embedding-3-small"
                ),
            ),
            provider_rates={
                ("embedding", "openai", "text-embedding-3-small"): RateSpec(
                    rate_mpaise=20_000, unit=RateUnit.THOUSAND_TOKENS
                ),
            },
        )

        line = next(line for line in cost.line_items if line.component == "embedding")
        # 1000 tokens at 20000 mpaise/1k tokens = 20 paise vendor cost,
        # at 1.7x = 34 paise.
        assert line.provider_cost_paise == 20
        assert line.cost_paise == 34
        assert line.provider == "openai"
        assert line.model == "text-embedding-3-small"

    def test_an_unpriced_embedding_model_is_uncosted_not_free(self):
        cost = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=20_000,
            usage=(UsageItem(CostComponent.EMBEDDING, "some-vendor", 500),),
            provider_rates={},
        )

        assert len(cost.uncosted) == 1
        assert cost.uncosted[0].component == CostComponent.EMBEDDING
        assert not any(line.component == "embedding" for line in cost.line_items)
