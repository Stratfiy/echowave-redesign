"""Auto routing, after the production trial.

Four things the trial found, each pinned here:

* A message wrapped in a channel's thread and taught procedures was sorted by
  the wrapping, so every chat went to Deep (Opus).
* Auto knew one vendor. It now has OpenAI beside Claude, Claude first, OpenAI
  only on a fallback or when a rule names it -- and always the same answer for
  the same keys.
* The builder's default and the sampling rule follow the Smart tier's Claude.
* A decision is written down, with its reason.
"""

import asyncio
from types import SimpleNamespace

import pytest
from loguru import logger

from api import constants
from api.enums import CostComponent
from api.services.billing import default_rates
from api.services.configuration import chat_presets, managed_tiers, registry
from api.services.pipecat import reasoning_effort
from api.services.routing import brain, models, record
from api.services.workflow import channel_reply, text_chat_runner
from api.services.workflow.text_chat_session_service import (
    build_pending_text_chat_turn,
)

#: What a channel really sends ahead of a message: 1.9k to 9.4k characters.
THREAD = "Earlier in this channel:\n" + "\n".join(
    f"Ravi: line {i} of the thread, with some words in it" for i in range(80)
)
TAUGHT = "Procedures you have been taught:\n" + ("When asked X, do Y. " * 120)


# --- 1. the bare words are what is sorted --------------------------------------


@pytest.mark.parametrize(
    "request_text",
    ["What time do you open?", "hi", "Draft a reply to Ravi about the proposal"],
)
def test_a_short_request_in_a_long_thread_routes_like_the_bare_request(request_text):
    wrapped = channel_reply.compose_turn(f"{TAUGHT}\n\n{THREAD}", request_text)
    assert 1900 < len(wrapped) < 20000
    # The bug, kept as the reason for the fix: the wrapped turn is "deep".
    assert brain.by_rules(wrapped) == "deep"

    turn = build_pending_text_chat_turn(user_text=wrapped, routing_text=request_text)
    assert text_chat_runner.routing_text_for(turn) == request_text
    assert brain.by_rules(text_chat_runner.routing_text_for(turn)) == brain.by_rules(
        request_text
    )
    assert brain.by_rules(request_text) != "deep"


def test_a_turn_nothing_was_added_to_is_sorted_by_its_own_text():
    turn = build_pending_text_chat_turn(user_text="hello there")
    assert "routing_text" not in turn["user_message"]
    assert text_chat_runner.routing_text_for(turn) == "hello there"
    # Same text given twice is stored once.
    same = build_pending_text_chat_turn(user_text="hi", routing_text="hi")
    assert "routing_text" not in same["user_message"]


def test_a_genuinely_long_request_is_still_deep():
    long_ask = "Please go through this. " * 60
    turn = build_pending_text_chat_turn(user_text=long_ask, routing_text=long_ask)
    assert brain.by_rules(text_chat_runner.routing_text_for(turn)) == "deep"


# --- 2. Claude first, OpenAI beside it ------------------------------------------


def test_auto_kinds_map_to_the_same_tiers_as_the_presets():
    for kind, preset in brain.PRESET_FOR.items():
        assert (
            chat_presets.PRESETS_BY_SLUG[preset].llm_tier
            == managed_tiers.AUTO_KIND_TIERS[kind]
        )


@pytest.mark.parametrize("kind", ["quick", "steps", "deep"])
def test_every_openai_candidate_is_configured_and_priced_by_name(kind):
    model = managed_tiers.AUTO_OPENAI_MODELS[kind]
    provider = managed_tiers.AUTO_OPENAI_PROVIDER
    assert model in registry.OPENAI_MODELS
    assert chat_presets.named_model(f"model:{provider}/{model}") == (provider, model)
    assert any(
        r.provider == provider and r.component == CostComponent.LLM and r.model == model
        for r in default_rates.DEFAULT_RATES
    ), f"{model} would be billed at the provider-wide floor"


def _usable(monkeypatch, **answers):
    """Claude and OpenAI usable or not: {'anthropic': None | 'out_of_credit'}."""

    async def unusable_because(session, upstream):
        return answers.get(upstream.provider, "no_key")

    monkeypatch.setattr(models, "unusable_because", unusable_because)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,preset", list(brain.PRESET_FOR.items()))
async def test_claude_is_the_default_in_every_tier(monkeypatch, kind, preset):
    _usable(monkeypatch, anthropic=None, openai=None)
    chosen = await models._pick(None, kind, preset, None)
    assert (chosen.provider, chosen.reason, chosen.slug) == (
        "anthropic",
        models.CLAUDE_DEFAULT,
        preset,
    )
    assert chosen.model == models.claude_for(kind).model


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["out_of_credit", "no_key", "key_rejected"])
async def test_openai_is_the_fallback_when_claude_is_not_usable(monkeypatch, why):
    _usable(monkeypatch, anthropic=why, openai=None)
    chosen = await models._pick(None, "deep", "deep", None)
    assert (chosen.provider, chosen.model) == ("openai", "gpt-5")
    assert chosen.slug == "model:openai/gpt-5"
    assert (chosen.reason, chosen.detail) == (models.CLAUDE_UNAVAILABLE, why)
    assert chosen.is_fallback
    # What the runner then resolves it to is a direct platform-key OpenAI brain.
    configs = chat_presets.apply({}, chosen.slug)
    stack = next(iter(configs.values()))["stack"]
    assert stack["llm"]["provider"] == "openai"
    assert stack["llm"]["use_platform_key"] is True


@pytest.mark.asyncio
async def test_with_neither_vendor_usable_it_stays_on_claude_and_says_so(monkeypatch):
    _usable(monkeypatch, anthropic="out_of_credit", openai="no_key")
    chosen = await models._pick(None, "steps", "smart", None)
    assert (chosen.provider, chosen.slug) == ("anthropic", "smart")
    assert chosen.reason == models.NO_USABLE_VENDOR
    assert "out_of_credit" in chosen.detail and "no_key" in chosen.detail


@pytest.mark.asyncio
async def test_a_rule_naming_openai_picks_it_only_when_it_is_usable(monkeypatch):
    _usable(monkeypatch, anthropic=None, openai=None)
    chosen = await models._pick(None, "quick", "everyday", "OpenAI")
    assert (chosen.provider, chosen.model, chosen.reason) == (
        "openai",
        "gpt-4.1-mini",
        models.RULE_NAMED_VENDOR,
    )
    _usable(monkeypatch, anthropic=None, openai="no_key")
    chosen = await models._pick(None, "quick", "everyday", "openai")
    assert (chosen.provider, chosen.reason) == ("anthropic", models.CLAUDE_DEFAULT)


@pytest.mark.asyncio
async def test_the_same_keys_always_give_the_same_model(monkeypatch):
    _usable(monkeypatch, anthropic="out_of_credit", openai=None)
    seen = {await models._pick(None, "steps", "smart", None) for _ in range(20)}
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_failed_availability_check_costs_no_reply(monkeypatch):
    from api.db import db_client

    def broken():
        raise RuntimeError("database is down")

    monkeypatch.setattr(db_client, "async_session", broken)
    chosen = await models.pick("deep", preset="deep")
    assert (chosen.provider, chosen.slug, chosen.reason) == (
        "anthropic",
        "deep",
        models.UNCHECKED,
    )


@pytest.mark.asyncio
async def test_unusable_because_reads_credit_keys_and_rejections(monkeypatch):
    from api.services.agent_builder import client
    from api.services.configuration import managed_resolution

    upstream = managed_tiers.ManagedUpstream("anthropic", "claude-haiku-4-5")
    state = {"key": "k", "bad": False}

    async def platform_key(session, *, component, provider, model=None):
        return state["key"]

    async def known_bad(session, *, component, provider):
        return state["bad"]

    monkeypatch.setattr(managed_resolution, "_platform_key", platform_key)
    monkeypatch.setattr(managed_resolution, "_credential_is_known_bad", known_bad)
    monkeypatch.setattr(client, "is_exhausted", lambda provider: False)

    assert await models.unusable_because(None, upstream) is None
    state["bad"] = True
    assert await models.unusable_because(None, upstream) == "key_rejected"
    state["key"] = None
    assert await models.unusable_because(None, upstream) == "no_key"
    monkeypatch.setattr(client, "is_exhausted", lambda provider: True)
    assert await models.unusable_because(None, upstream) == "out_of_credit"


# --- 3. the last old defaults ---------------------------------------------------


def test_the_builder_and_the_sampling_rule_follow_the_smart_tier():
    assert constants.AGENT_BUILDER_MODELS["anthropic"] == constants.CLAUDE_SONNET_MODEL
    assert constants.CLAUDE_SONNET_MODEL == "claude-sonnet-5-5"
    assert (
        managed_tiers.resolve("llm", "accurate").model == constants.CLAUDE_SONNET_MODEL
    )
    # Sonnet 5.5 refuses sampling, and so does the Sonnet 5 it replaced.
    assert reasoning_effort.claude_rejects_sampling(constants.CLAUDE_SONNET_MODEL)
    assert reasoning_effort.claude_rejects_sampling("claude-sonnet-5")
    assert reasoning_effort.claude_takes_effort(constants.CLAUDE_SONNET_MODEL)


def test_the_managed_claude_tiers_are_the_constants():
    assert managed_tiers.resolve("llm", "default").model == constants.CLAUDE_HAIKU_MODEL
    assert managed_tiers.resolve("llm", "advanced").model == constants.CLAUDE_OPUS_MODEL


# --- 4. every decision is recorded ----------------------------------------------


def _capture():
    lines: list[str] = []
    handle = logger.add(
        lambda m: lines.append(str(m)), level="INFO", format="{message}"
    )
    return lines, handle


@pytest.mark.asyncio
async def test_a_decision_is_logged_and_counted_with_its_reason(monkeypatch):
    counted: list[str] = []

    async def count(field, *, client=None):
        counted.append(field)

    monkeypatch.setattr(record, "_count", count)
    lines, handle = _capture()
    try:
        record.decision(
            organization_id=7,
            feature="text_chat",
            kind="deep",
            provider="openai",
            model="gpt-5",
            source="fallback",
            reason=models.CLAUDE_UNAVAILABLE,
            detail="out_of_credit",
        )
        await asyncio.sleep(0)
        await asyncio.gather(*record._PENDING)
    finally:
        logger.remove(handle)
    line = next(item for item in lines if "auto_route" in item)
    for part in (
        "org=7",
        "feature=text_chat",
        "kind=deep",
        "model=openai/gpt-5",
        "source=fallback",
        "reason=claude_unavailable",
        "detail=out_of_credit",
    ):
        assert part in line
    assert counted == ["deep|openai/gpt-5|fallback|claude_unavailable"]


@pytest.mark.asyncio
async def test_the_counter_is_a_daily_hash_that_expires():
    calls = []

    class Pipe:
        def hincrby(self, key, field, n):
            calls.append(("hincrby", key, field, n))

        def expire(self, key, ttl):
            calls.append(("expire", key, ttl))

        async def execute(self):
            calls.append(("execute",))

    await record._count(
        "quick|anthropic/m|rules|claude_default",
        client=SimpleNamespace(pipeline=lambda: Pipe()),
    )
    assert calls[0][0] == "hincrby" and calls[0][1].startswith(
        record.COUNTER_KEY_PREFIX
    )
    assert calls[0][2:] == ("quick|anthropic/m|rules|claude_default", 1)
    assert calls[1][2] == record.COUNTER_TTL_SECONDS and calls[2] == ("execute",)


@pytest.mark.asyncio
async def test_auto_route_records_the_decision_and_carries_the_model(monkeypatch):
    monkeypatch.setattr(constants, "LAYA_URL", "")

    async def on(_org):
        return True

    seen = []
    monkeypatch.setattr(brain, "workspace_is_auto", on)
    monkeypatch.setattr(record, "decision", lambda **kw: seen.append(kw))
    _usable(monkeypatch, anthropic="out_of_credit", openai=None)

    async def pick(kind, *, preset, prefer_vendor=None):
        return await models._pick(None, kind, preset, prefer_vendor)

    monkeypatch.setattr(models, "pick", pick)

    routed = await brain.auto_route(42, "Draft a reply to Ravi", feature="text_chat")
    assert (routed.kind, routed.source) == ("steps", "rules")
    assert (routed.provider, routed.model) == ("openai", "gpt-4.1")
    assert routed.preset == "model:openai/gpt-4.1"
    assert routed.model_reason == models.CLAUDE_UNAVAILABLE
    assert routed.as_dict()["provider"] == "openai"
    assert seen == [
        {
            "organization_id": 42,
            "feature": "text_chat",
            "kind": "steps",
            "provider": "openai",
            "model": "gpt-4.1",
            "source": "rules",
            "reason": models.CLAUDE_UNAVAILABLE,
            "detail": "out_of_credit",
        }
    ]


@pytest.mark.asyncio
async def test_a_laya_abstention_is_recorded_as_a_fallback(monkeypatch):
    monkeypatch.setattr(constants, "LAYA_URL", "")
    monkeypatch.setattr(constants, "LAYA_ROUTING", "on")

    async def on(_org):
        return True

    async def unsure(*args, **kwargs):
        from api.services.routing import decision

        return decision.Decision(None, None, 5, "low_confidence")

    from api.services.ops import laya_eval
    from api.services.routing import decision

    monkeypatch.setattr(brain, "workspace_is_auto", on)
    monkeypatch.setattr(decision, "enabled", lambda: True)
    monkeypatch.setattr(laya_eval, "rolled_back", lambda: False)
    monkeypatch.setattr(laya_eval, "guarded_choose", unsure)
    seen = []
    monkeypatch.setattr(record, "decision", lambda **kw: seen.append(kw))
    _usable(monkeypatch, anthropic=None, openai=None)

    async def pick(kind, *, preset, prefer_vendor=None):
        return await models._pick(None, kind, preset, prefer_vendor)

    monkeypatch.setattr(models, "pick", pick)
    routed = await brain.auto_route(1, "hi")
    assert routed.source == "laya_fallback"
    assert seen[0]["source"] == "fallback"
