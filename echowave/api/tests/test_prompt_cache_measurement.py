"""Measure prompt caching before optimising it (roadmap item 10, first half).

Caching already existed -- the composer keeps stable blocks first, the
pipeline turns Anthropic's caching on, the builder client marks the system
block -- and cached tokens were already recorded. What was missing was any
way to tell whether it works. These tests hold the measurement honest:

* every vendor payload shape the code receives reads into one shape, and
  gives back exactly the numbers billing always stored;
* a prompt's fingerprint is the same turn after turn and changes when its
  tools do, and per-call values change the prefix but not the version;
* a prefix that changes inside one conversation is counted, with which half;
* the report's numbers come out of seeded rows as hand-worked;
* the report is staff-only and hidden from the public API;
* with ``cache_v2`` off, nothing a model is sent changes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData
from pipecat.processors.aggregators.llm_context import LLMContext
from sqlalchemy import select

from api.db.models import (
    AgentEventModel,
    LlmCallUsageModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import AgentEventActor, AgentEventKind
from api.services import features
from api.services.agent_builder import client
from api.services.billing import (
    cache_capabilities,
    cache_metrics,
    cache_report,
    llm_usage,
    model_usage,
)
from api.services.billing.usage import llm_split_items
from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.workflow import decibyl
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.pipecat_engine_context_composer import (
    compose_system_prompt_for_node,
)
from pipecat.tests import MockLLMService

RATE = lambda mpaise: SimpleNamespace(rate_mpaise=mpaise)  # noqa: E731
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


# --- 1. one shape for every vendor ---------------------------------------------


class TestEveryVendorShapeNormalises:
    def test_anthropic_input_is_net_of_its_cache(self):
        u = llm_usage.normalise(
            {
                "input_tokens": 120,
                "output_tokens": 40,
                "cache_read_input_tokens": 900,
                "cache_creation_input_tokens": 60,
            }
        )
        assert (u.input_tokens, u.cache_read_tokens, u.cache_write_tokens) == (
            120,
            900,
            60,
        )
        assert u.total_input_tokens == 1080 and u.output_tokens == 40
        assert u.cache_outside_prompt

    def test_anthropic_with_no_cache_fields_is_still_anthropic(self):
        u = llm_usage.normalise({"input_tokens": 5, "output_tokens": 1})
        assert u.cache_outside_prompt and u.input_tokens == 5

    def test_bedrock_converse(self):
        u = llm_usage.normalise(
            {
                "inputTokens": 10,
                "outputTokens": 3,
                "cacheReadInputTokens": 500,
                "cacheWriteInputTokens": 20,
            }
        )
        assert u.as_dict() == {
            "input_tokens": 10,
            "output_tokens": 3,
            "cache_read_tokens": 500,
            "cache_write_tokens": 20,
            "reasoning_tokens": 0,
        }

    def test_openai_chat_contains_its_cache_and_reasoning(self):
        u = llm_usage.normalise(
            {
                "prompt_tokens": 1000,
                "completion_tokens": 40,
                "prompt_tokens_details": {"cached_tokens": 800},
                "completion_tokens_details": {"reasoning_tokens": 25},
            }
        )
        assert (u.input_tokens, u.cache_read_tokens) == (200, 800)
        assert (u.output_tokens, u.reasoning_tokens) == (40, 25)
        assert not u.cache_outside_prompt

    def test_openai_responses(self):
        u = llm_usage.normalise(
            {
                "input_tokens": 300,
                "output_tokens": 9,
                "input_tokens_details": {"cached_tokens": 256},
                "output_tokens_details": {"reasoning_tokens": 4},
            }
        )
        assert (u.input_tokens, u.cache_read_tokens, u.reasoning_tokens) == (
            44,
            256,
            4,
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "promptTokenCount": 1000,
                "candidatesTokenCount": 30,
                "thoughtsTokenCount": 10,
                "cachedContentTokenCount": 700,
            },
            {
                "prompt_token_count": 1000,
                "candidates_token_count": 30,
                "thoughts_token_count": 10,
                "cached_content_token_count": 700,
            },
        ],
        ids=["rest", "sdk"],
    )
    def test_gemini_thinking_is_output(self, payload):
        u = llm_usage.normalise(payload)
        assert (u.input_tokens, u.cache_read_tokens) == (300, 700)
        assert (u.output_tokens, u.reasoning_tokens) == (40, 10)

    def test_pipeline_follows_the_vendor_behind_it(self):
        payload = {
            "prompt_tokens": 100,
            "completion_tokens": 5,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 10,
        }
        claude = llm_usage.normalise(payload, shape="pipeline", provider="anthropic")
        openai = llm_usage.normalise(payload, shape="pipeline", provider="openai")
        assert (claude.input_tokens, claude.cache_write_tokens) == (100, 10)
        # A vendor that includes its cache in the prompt reports no write.
        assert (openai.input_tokens, openai.cache_write_tokens) == (20, 0)

    def test_pipecat_usage_object(self):
        usage = LLMTokenUsage(
            prompt_tokens=50,
            completion_tokens=7,
            total_tokens=57,
            cache_read_input_tokens=30,
            reasoning_tokens=3,
        )
        u = llm_usage.normalise(usage, shape="pipeline", provider="google")
        assert (u.input_tokens, u.cache_read_tokens, u.reasoning_tokens) == (20, 30, 3)

    def test_no_payload_is_none_not_zero(self):
        assert llm_usage.normalise(None) is None
        with pytest.raises(ValueError):
            llm_usage.normalise({}, shape="nonsense")

    @pytest.mark.parametrize(
        "provider,payload",
        [
            (
                "anthropic",
                {
                    "input_tokens": 120,
                    "output_tokens": 40,
                    "cache_read_input_tokens": 900,
                    "cache_creation_input_tokens": 60,
                },
            ),
            (
                "openai",
                {
                    "prompt_tokens": 1000,
                    "completion_tokens": 40,
                    "prompt_tokens_details": {"cached_tokens": 800},
                },
            ),
        ],
    )
    def test_billing_sees_the_same_numbers_it_always_did(self, provider, payload):
        """The stored fields, and so every bill, are unchanged."""
        fields = llm_usage.normalise(payload).as_usage_fields()
        again = llm_usage.normalise(fields, shape="pipeline", provider=provider)
        assert again.as_usage_fields() == fields
        split = {
            i.component: i.quantity
            for i in llm_split_items(fields, provider=provider, model="m")
        }
        assert sum(split.values()) == (
            llm_usage.normalise(payload).total_input_tokens + 40
        )


# --- 2. the capability matrix ---------------------------------------------------


class TestTheCapabilityMatrix:
    def test_every_routable_vendor_has_a_row(self):
        from api.services.configuration.registry import REGISTRY, ServiceType

        providers = {r["provider"] for r in cache_capabilities.matrix()}
        for service_type in (ServiceType.LLM, ServiceType.REALTIME):
            for provider in REGISTRY[service_type]:
                assert getattr(provider, "value", provider) in providers
        assert "anthropic_aws" in providers

    def test_an_unknown_vendor_says_unknown_rather_than_guessing(self):
        row = cache_capabilities.row("a-vendor-nobody-added")
        assert row["supported"] == cache_capabilities.UNKNOWN
        assert row["min_cacheable_tokens"] == cache_capabilities.UNKNOWN
        # Billing reads a missing discount as 1.0x; the matrix does not.
        assert row["cache_read_multiplier"] == cache_capabilities.UNKNOWN

    def test_prices_come_from_billing(self):
        from api.services.billing import default_rates

        row = cache_capabilities.row("anthropic")
        assert (
            row["cache_read_multiplier"]
            == default_rates.CACHED_INPUT_SHARE["anthropic"]
        )
        assert (
            row["cache_write_multiplier"]
            == default_rates.CACHE_WRITE_SHARE["anthropic"]
        )
        assert row["mechanism"] == cache_capabilities.EXPLICIT_CACHE_CONTROL

    def test_every_described_vendor_is_one_the_platform_can_route_to(self):
        providers = {r["provider"] for r in cache_capabilities.matrix()}
        assert set(cache_capabilities.VENDORS) <= providers


# --- 3. fingerprints ---------------------------------------------------------------

TOOLS = [
    {
        "name": "book",
        "description": "Book a slot.",
        "parameters": {"type": "object", "properties": {"day": {"type": "string"}}},
    },
    {"name": "cancel", "description": "Cancel.", "parameters": {"type": "object"}},
]


class TestFingerprints:
    def test_the_same_prompt_is_the_same_turn_after_turn(self):
        a = cache_metrics.prompt_hashes("You are a front desk.", TOOLS)
        # The same schemas built with their keys in another order.
        reordered = [
            {
                "parameters": dict(reversed(list(t["parameters"].items()))),
                "description": t["description"],
                "name": t["name"],
            }
            for t in TOOLS
        ]
        b = cache_metrics.prompt_hashes("You are a front desk.", reordered)
        assert a == b

    def test_a_tool_added_or_moved_is_a_new_prompt(self):
        base = cache_metrics.prompt_hashes("s", TOOLS)
        added = cache_metrics.prompt_hashes(
            "s", [*TOOLS, {"name": "x", "description": "", "parameters": {}}]
        )
        moved = cache_metrics.prompt_hashes("s", list(reversed(TOOLS)))
        for other in (added, moved):
            assert other.prompt_fingerprint != base.prompt_fingerprint
            assert other.tools_hash != base.tools_hash
            assert other.system_hash == base.system_hash

    def test_per_call_values_change_the_prefix_not_the_version(self):
        monday = cache_metrics.prompt_hashes(
            "Right now it is Monday.\n\nBe kind.",
            TOOLS,
            volatile=["Right now it is Monday."],
        )
        tuesday = cache_metrics.prompt_hashes(
            "Right now it is Tuesday.\n\nBe kind.",
            TOOLS,
            volatile=["Right now it is Tuesday."],
        )
        assert monday.prompt_fingerprint == tuesday.prompt_fingerprint
        assert monday.prefix_hash != tuesday.prefix_hash

    def test_pipecat_function_schemas_hash_like_dicts(self):
        from pipecat.adapters.schemas.function_schema import FunctionSchema

        schema = FunctionSchema(
            name="book",
            description="Book a slot.",
            properties={"day": {"type": "string"}},
            required=[],
        )
        a = cache_metrics.prompt_hashes("s", [schema])
        b = cache_metrics.prompt_hashes("s", [schema.to_default_dict()])
        assert a == b

    def test_decibyls_own_tools_serialise_identically_turn_to_turn(self):
        """Deterministic serialisation of the stable prefix: the same tools
        built twice, a minute apart, are byte-identical on the wire."""
        first = json.dumps(decibyl.office_tools(None))
        with patch("time.time", return_value=4_102_444_800.0):
            second = json.dumps(decibyl.office_tools(None))
        assert first == second
        assert decibyl.system_prompt(None) == decibyl.system_prompt(None)


# --- 4. prefix breakers ------------------------------------------------------------


def _call(minute, *, key="thread:a", feature="decibyl", system="s1", tools="t1", **kw):
    hashes = {
        "system_hash": system,
        "tools_hash": tools,
        "prefix_hash": f"{system}:{tools}",
        "prompt_fingerprint": kw.pop("fingerprint", f"fp-{system}-{tools}"),
    }
    row = {
        "created_at": T0 + timedelta(minutes=minute),
        "source": "direct",
        "feature": feature,
        "provider": kw.pop("provider", "anthropic"),
        "model": kw.pop("model", "m"),
        "conversation_key": key,
        "workflow_run_id": kw.pop("run", None),
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "is_retry": False,
        "byok": feature.endswith(":byok"),
        **hashes,
    }
    row.update(kw)
    return row


class TestPrefixBreakers:
    def test_each_change_is_counted_with_the_half_that_changed(self):
        calls = [
            _call(0),
            _call(1),  # same: no break
            _call(2, tools="t2"),  # tools
            _call(3, system="s2", tools="t2"),  # system
            _call(4, system="s3", tools="t3"),  # both
        ]
        breaks = cache_report.prefix_breaks(list(reversed(calls)))
        assert [b["kind"] for b in breaks] == ["tools", "system", "both"]

    def test_conversations_do_not_break_each_other(self):
        calls = [_call(0, key="thread:a"), _call(1, key="thread:b", system="other")]
        assert cache_report.prefix_breaks(calls) == []

    def test_a_summary_is_not_a_break(self):
        calls = [
            _call(0, key="run:1", feature="agent_call"),
            _call(1, key="run:1", feature="summary", system="summariser"),
            _call(2, key="run:1", feature="agent_call"),
        ]
        assert cache_report.prefix_breaks(calls) == []


# --- 5. the report's numbers -----------------------------------------------------

RATES = {
    ("llm_input", "anthropic", "m"): RATE(1000),
    ("llm_cached", "anthropic", "m"): RATE(100),
    ("llm_cache_write", "anthropic", "m"): RATE(1250),
    ("llm_output", "anthropic", "m"): RATE(5000),
}


class TestTheReportFromSeededRows:
    def _calls(self):
        return [
            # run:1 -- a call that completed: cold write, then two warm reads,
            # a retry, and a background summary.
            _call(
                0,
                key="run:1",
                feature="agent_call",
                run=1,
                input_tokens=100,
                cache_write_tokens=2000,
                output_tokens=20,
            ),
            _call(
                1,
                key="run:1",
                feature="agent_call",
                run=1,
                input_tokens=100,
                cache_read_tokens=2000,
                output_tokens=20,
            ),
            _call(
                2,
                key="run:1",
                feature="agent_call",
                run=1,
                input_tokens=100,
                cache_read_tokens=2000,
                output_tokens=20,
                is_retry=True,
            ),
            _call(
                3,
                key="run:1",
                feature="summary",
                run=1,
                system="sum",
                input_tokens=1000,
                output_tokens=100,
            ),
            # run:2 -- a call that failed, with its prompt changed mid-way.
            _call(
                0,
                key="run:2",
                feature="agent_call",
                run=2,
                input_tokens=2100,
                output_tokens=20,
            ),
            _call(
                1,
                key="run:2",
                feature="agent_call",
                run=2,
                system="s9",
                input_tokens=2100,
                output_tokens=20,
            ),
            # Too short for the vendor to cache: out of the hit rate.
            _call(
                5,
                key="thread:x",
                feature="decibyl:byok",
                input_tokens=50,
                output_tokens=5,
            ),
        ]

    def test_hit_rate_counts_only_what_could_have_been_cached(self):
        r = cache_report.summarise(self._calls(), rates=RATES, outcomes={})
        call = next(f for f in r["by_feature"] if f["feature"] == "agent_call")
        # Cacheable input: 2100 + 2100 + 2100 + 2100 + 2100 = 10500 (every
        # call is >= 1024); read 4000.
        assert call["cacheable_input_tokens"] == 10500
        assert call["hit_rate"] == round(4000 / 10500, 4)
        assert (call["warm_calls"], call["cold_calls"]) == (2, 3)
        decibyl_row = next(f for f in r["by_feature"] if f["feature"] == "decibyl")
        assert decibyl_row["cacheable_input_tokens"] == 0
        assert decibyl_row["hit_rate"] is None  # nothing could hit: not 0

    def test_costs_include_cache_writes_and_byok_is_not_priced(self):
        r = cache_report.summarise(self._calls(), rates=RATES, outcomes={})
        first = cache_report._price(self._calls()[0], RATES)[0]
        # 100 input at 1000, 2000 written at 1250, 20 output at 5000, per 1k,
        # in millipaise -> paise.
        assert first == pytest.approx(
            (100 * 1000 + 2000 * 1250 + 20 * 5000) / 1000 / 1000
        )
        assert (
            next(f for f in r["by_feature"] if f["feature"] == "decibyl")["cost_paise"]
            == 0
        )

    def test_cost_per_success_carries_retries_summaries_and_failures(self):
        calls = self._calls()
        r = cache_report.summarise(
            calls, rates=RATES, outcomes={"run:1": True, "run:2": False}
        )
        row = next(c for c in r["cost_per_outcome"] if c["feature"] == "agent_call")
        every = sum(
            cache_report._price(c, RATES)[0]
            for c in calls
            if c["conversation_key"] in ("run:1", "run:2")
        )
        assert (row["tasks"], row["tasks_with_outcome"], row["succeeded"]) == (2, 2, 1)
        assert row["cost_paise_per_success"] == round(every, 2)
        assert (row["retry_calls"], row["side_calls"]) == (1, 1)
        # A thread with no outcome is a task, but never in the per-success cost.
        chat = next(c for c in r["cost_per_outcome"] if c["feature"] == "decibyl")
        assert (
            chat["tasks_with_outcome"] == 0 and chat["cost_paise_per_success"] is None
        )

    def test_cold_and_warm_by_position(self):
        r = cache_report.summarise(self._calls(), rates=RATES, outcomes={})
        rows = {
            (c["feature"], c["position"]): c
            for c in r["cold_warm"]
            if c["feature"] == "agent_call"
        }
        assert rows[("agent_call", "first")]["calls"] == 2
        assert rows[("agent_call", "first")]["warm_calls"] == 0
        assert rows[("agent_call", "later")]["warm_calls"] == 2

    def test_prefix_breakers_and_top_fingerprints(self):
        r = cache_report.summarise(self._calls(), rates=RATES, outcomes={})
        feature = next(
            b for b in r["prefix_breaks"]["by_feature"] if b["feature"] == "agent_call"
        )
        assert (feature["conversations"], feature["conversations_with_breaks"]) == (
            2,
            1,
        )
        assert feature["system"] == 1 and feature["breaks"] == 1
        assert r["prefix_breaks"]["top_conversations"][0]["conversation_key"] == "run:2"
        spend = [f["cost_paise"] for f in r["top_fingerprints"]]
        assert spend == sorted(spend, reverse=True)
        assert r["totals"]["retry_calls"] == 1 and r["totals"]["side_calls"] == 1

    def test_one_version_with_many_first_prefixes_is_flagged(self):
        calls = [
            _call(0, key="run:1", system="s-mon", fingerprint="fp"),
            _call(0, key="run:2", system="s-tue", fingerprint="fp"),
        ]
        r = cache_report.summarise(calls, rates={}, outcomes={})
        (volatile,) = r["prefix_breaks"]["volatile_prefixes"]
        assert volatile["prompt_fingerprint"] == "fp"
        assert (
            volatile["distinct_first_prefixes"] == 2 and volatile["conversations"] == 2
        )

    def test_outcomes(self):
        done = {"is_completed": True, "state": "completed"}
        assert cache_report.outcome_of("agent_call", done) is True
        assert (
            cache_report.outcome_of("agent_call", {**done, "state": "running"}) is False
        )
        assert cache_report.outcome_of("routine", {"delivered": True}) is True
        assert cache_report.outcome_of("agent_chat", done) is None


async def _run(session, slug, *, mode="twilio", annotations=None, completed=True):
    user = UserModel(provider_id=f"user-cache-{slug}")
    org = OrganizationModel(provider_id=f"org-cache-{slug}", quota_decibyl_tokens=0)
    session.add_all([user, org])
    await session.flush()
    workflow = WorkflowModel(
        name=f"wf-{slug}",
        user_id=user.id,
        organization_id=org.id,
        workflow_definition={},
        template_context_variables={},
        call_disposition_codes={},
    )
    session.add(workflow)
    await session.flush()
    run = WorkflowRunModel(
        name="run",
        workflow_id=workflow.id,
        mode=mode,
        usage_info={},
        cost_info={},
        initial_context={},
        gathered_context={},
        annotations=annotations or {},
        is_completed=completed,
        state="completed" if completed else "running",
        created_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return org, workflow, run


@pytest.mark.asyncio
class TestTheTable:
    async def test_a_runs_calls_are_written_and_reported(
        self, db_session, async_session
    ):
        org, workflow, run = await _run(async_session, "a")
        routine_org, routine_wf, routine = await _run(
            async_session, "b", mode="textchat", annotations={"routine": {"id": 1}}
        )
        async_session.add(
            AgentEventModel(
                organization_id=routine_org.id,
                workflow_id=routine_wf.id,
                workflow_run_id=routine.id,
                at=datetime.now(UTC),
                kind=AgentEventKind.DELIVERABLE.value,
                actor=AgentEventActor.SYSTEM.value,
                summary="Done",
            )
        )
        await async_session.flush()

        agg = PipelineMetricsAggregator()
        agg.register_key_sources({"llm": "managed"})
        agg.register_prompt(cache_metrics.prompt_hashes("s", TOOLS))
        await agg._handle_llm_usage_metrics(
            LLMUsageMetricsData(
                processor="AnthropicLLMService#0",
                model="M",
                value=LLMTokenUsage(
                    prompt_tokens=100,
                    completion_tokens=10,
                    total_tokens=110,
                    cache_read_input_tokens=3000,
                ),
            )
        )
        agg.register_side_call(
            feature="summary",
            processor="AnthropicLLMService#0",
            model="M",
            usage=LLMTokenUsage(prompt_tokens=50, completion_tokens=5, total_tokens=55),
        )
        assert await cache_metrics.record_run(agg.take_llm_calls(), run.id) == 2
        assert agg.take_llm_calls() == []  # handed over once

        agg2 = PipelineMetricsAggregator()
        await agg2._handle_llm_usage_metrics(
            LLMUsageMetricsData(
                processor="OpenAILLMService#0",
                model="m",
                value=LLMTokenUsage(
                    prompt_tokens=2000,
                    completion_tokens=10,
                    total_tokens=2010,
                    cache_read_input_tokens=1024,
                ),
            )
        )
        await cache_metrics.record_run(agg2.take_llm_calls(), routine.id)

        rows = (await async_session.execute(select(LlmCallUsageModel))).scalars().all()
        by_feature = {r.feature: r for r in rows}
        call = by_feature["agent_call"]
        assert (call.organization_id, call.provider, call.model) == (
            org.id,
            "anthropic",
            "m",
        )
        assert (call.input_tokens, call.cache_read_tokens) == (100, 3000)
        assert call.conversation_key == f"run:{run.id}" and call.prefix_hash
        assert by_feature["summary"].prefix_hash is None
        assert by_feature["routine"].input_tokens == 976

        report = await cache_report.build(
            async_session, end=datetime.now(UTC) + timedelta(minutes=1)
        )
        outcomes = {r["feature"]: r for r in report["cost_per_outcome"]}
        assert outcomes["agent_call"]["succeeded"] == 1
        assert outcomes["routine"]["succeeded"] == 1
        assert report["window"]["days"] == 7
        assert report["capabilities"]

    async def test_a_failed_write_never_costs_the_call(self):
        with patch.object(
            cache_metrics, "_write", AsyncMock(side_effect=RuntimeError("db down"))
        ):
            await cache_metrics.record_direct(
                provider="openai",
                model="m",
                usage=llm_usage.NormalisedUsage(input_tokens=1),
                system="s",
                tools=[],
            )
            assert await cache_metrics.record_run([{"usage": None}], None) == 0


# --- 6. the builder client records the call and its prompt -----------------------


def _response(body: dict) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(body).encode())


@pytest.mark.asyncio
class TestTheBuilderClientRecordsThePrompt:
    async def test_a_call_carries_its_hashes_conversation_and_reasoning(self):
        body = {
            "choices": [{"message": {"content": "Done.", "role": "assistant"}}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 40,
                "prompt_tokens_details": {"cached_tokens": 800},
                "completion_tokens_details": {"reasoning_tokens": 12},
            },
        }
        with (
            patch.object(model_usage, "_write", AsyncMock()),
            patch.object(cache_metrics, "_write", AsyncMock()) as write,
            patch.object(
                httpx.AsyncClient, "post", AsyncMock(return_value=_response(body))
            ),
            model_usage.scope(organization_id=7, feature="decibyl"),
            cache_metrics.conversation("thread:t1"),
        ):
            await client.complete(
                provider=client.OPENAI,
                model="gpt-x",
                api_key="k",
                system="You are Decibyl.",
                conversation=client.Conversation(),
                tools=TOOLS,
            )
        (row,) = write.await_args.args[0]
        expected = cache_metrics.prompt_hashes("You are Decibyl.", TOOLS)
        assert row["prompt_fingerprint"] == expected.prompt_fingerprint
        assert row["conversation_key"] == "thread:t1"
        assert (row["organization_id"], row["feature"], row["source"]) == (
            7,
            "decibyl",
            "direct",
        )
        assert (row["input_tokens"], row["cache_read_tokens"]) == (200, 800)
        assert row["reasoning_tokens"] == 12 and row["is_retry"] is False

    async def test_a_decibyl_thread_is_the_conversation(self):
        from api.services.workflow import agent_timeline

        with agent_timeline.in_thread("abc"):
            assert cache_metrics.current_conversation() == "thread:abc"
        assert cache_metrics.current_conversation() is None

    async def test_a_retry_after_a_rate_limit_is_marked(self):
        limited = httpx.Response(429, headers={"retry-after": "0"}, content=b"{}")
        ok = _response(
            {
                "content": [{"type": "text", "text": "x"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )
        with (
            patch.object(model_usage, "_write", AsyncMock()),
            patch.object(cache_metrics, "_write", AsyncMock()) as write,
            patch.object(
                httpx.AsyncClient, "post", AsyncMock(side_effect=[limited, ok])
            ),
            patch.object(client.asyncio, "sleep", AsyncMock()),
        ):
            await client.complete(
                provider=client.ANTHROPIC,
                model="m",
                api_key="k",
                system="s",
                conversation=client.Conversation(),
                tools=[],
            )
        (row,) = write.await_args.args[0]
        assert row["is_retry"] is True


# --- 7. staff only ------------------------------------------------------------------


class TestStaffOnly:
    PATH = "/api/v1/admin/staff/operations/caching"

    def test_the_route_exists_and_needs_operations_read(self):
        import inspect

        from api.routes import staff_console

        route = next(
            r
            for r in staff_console.router.routes
            if r.path.endswith("/operations/caching")
        )
        assert "operations.read" in inspect.getsource(route.endpoint)

    def test_it_is_not_in_the_public_document(self):
        from api.app import app
        from api.services import openapi_surface

        assert self.PATH not in openapi_surface.public_spec(app)["paths"]
        assert self.PATH in openapi_surface.full_spec(app)["paths"]

    @pytest.mark.asyncio
    async def test_a_customer_is_refused(
        self, test_client_factory, db_session, monkeypatch
    ):
        # The console switched on, so the refusal is the staff gate's.
        real = features.is_on
        monkeypatch.setattr(
            features,
            "is_on",
            lambda name, organization_id=None: (
                name == "staff_console" or real(name, organization_id)
            ),
        )
        user, _ = await db_session.get_or_create_user_by_provider_id("cache-customer")
        async with test_client_factory(user) as http:
            response = await http.get(self.PATH)
        assert response.status_code in (401, 403)


# --- 8. cache_v2 off changes nothing a model is sent --------------------------------


def _compose(workflow, **kw):
    return compose_system_prompt_for_node(
        node=workflow.nodes[workflow.start_node_id],
        workflow=workflow,
        format_prompt=lambda p: p,
        has_recordings=False,
        today_line="Right now it is Friday.",
        skills="SKILLS",
        caller="WHO IS CALLING.",
        known_values={},
        **kw,
    )


class TestCacheV2IsOffByDefault:
    def test_the_flag_is_registered_and_off(self):
        assert "cache_v2" in features.FLAGS
        assert features.is_on("cache_v2") is False

    def test_off_the_clock_line_is_first_as_before(self, simple_workflow):
        assert _compose(simple_workflow) == _compose(
            simple_workflow, clock_after_instructions=False
        )
        assert _compose(simple_workflow).startswith("Right now it is Friday.")

    def test_on_the_clock_line_follows_the_fixed_instructions(self, simple_workflow):
        prompt = _compose(simple_workflow, clock_after_instructions=True)
        node_prompt = simple_workflow.nodes[simple_workflow.start_node_id].prompt
        assert prompt.startswith(node_prompt.strip()[:20])
        assert prompt.index("SKILLS") < prompt.index("Right now it is Friday.")
        assert prompt.index("Right now it is Friday.") < prompt.index("WHO IS CALLING.")
        # Nothing added, nothing lost: the same blocks, in another order.
        assert sorted(prompt.split("\n\n")) == sorted(
            _compose(simple_workflow).split("\n\n")
        )

    def test_off_the_builder_request_has_one_breakpoint(self):
        conversation = client.Conversation()
        conversation.add_user("hello")
        payload = client._anthropic_request(
            model="m", system="s", conversation=conversation, tools=[]
        )
        assert payload["messages"] == [{"role": "user", "content": "hello"}]
        assert "cache_control" not in json.dumps(payload["messages"])

    def test_on_the_builder_marks_the_tail_without_touching_the_conversation(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            features, "is_on", lambda name, organization_id=None: name == "cache_v2"
        )
        conversation = client.Conversation()
        conversation.messages.append(
            {"role": "user", "content": [{"type": "text", "text": "look"}]}
        )
        payload = client._anthropic_request(
            model="m", system="s", conversation=conversation, tools=[]
        )
        assert payload["messages"][-1]["content"][-1]["cache_control"] == {
            "type": "ephemeral"
        }
        # The conversation's own list is not marked for the next request.
        assert "cache_control" not in conversation.messages[0]["content"][0]


@pytest.mark.asyncio
class TestTheEngine:
    def _engine(self, workflow):
        return PipecatEngine(
            llm=MockLLMService(mock_steps=[]),
            context=LLMContext(),
            workflow=workflow,
            call_context_vars={},
            workflow_run_id=1,
            task=SimpleNamespace(),
            is_voice=True,
        )

    async def _prompt(self, engine, workflow, monkeypatch, on: bool):
        seen = {}

        async def capture(system_prompt, functions):
            seen["prompt"] = system_prompt

        engine._organization_id = 1
        engine._workflow_id = 1
        engine._today_line = "Right now it is Friday."
        engine._scoped_document_uuids = []
        engine._remembered_block = ""
        engine._skills_block = ""
        engine._routines_block = ""
        engine._update_llm_context = capture
        monkeypatch.setattr(features, "on_anywhere", lambda name: on)
        monkeypatch.setattr(
            features,
            "is_on",
            lambda name, organization_id=None: on and name == "cache_v2",
        )
        await engine._setup_llm_context(workflow.nodes[workflow.start_node_id])
        return seen["prompt"]

    async def test_off_is_the_prompt_it_always_was_and_is_measured(
        self, db_session, simple_workflow, monkeypatch
    ):
        engine = self._engine(simple_workflow)
        listener = SimpleNamespace(register_prompt=lambda h: seen.append(h))
        seen: list = []
        engine.set_cache_listener(listener)
        with patch(
            "api.services.workflow.pipecat_engine.compose_system_prompt_for_node",
            wraps=compose_system_prompt_for_node,
        ) as compose:
            off = await self._prompt(engine, simple_workflow, monkeypatch, on=False)
        assert "clock_after_instructions" not in compose.call_args.kwargs
        assert off.startswith("Right now it is Friday.")
        (hashes,) = seen
        assert hashes.system_hash == cache_metrics.prompt_hashes(off, []).system_hash

        on = await self._prompt(
            self._engine(simple_workflow), simple_workflow, monkeypatch, on=True
        )
        assert not on.startswith("Right now it is Friday.")
        assert "Right now it is Friday." in on
