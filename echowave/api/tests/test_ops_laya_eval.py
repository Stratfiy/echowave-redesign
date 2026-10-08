"""Stream ops, handoff 14 / packet G: Laya shadow evaluation, safe timeout
and rollback.

What these defend: the rollback switch makes Auto route by rules and never
call Laya; with guardrails on, a slow Laya is cut at the hard deadline, a
raising one becomes an abstention, and repeated failures open a breaker that
stops calling it until it cools; a truncated message is flagged; shadow
counters carry counts only, never text; the labelled set loads and every
line is valid; the offline evaluation computes accuracy, per-kind false
positives and negatives, abstention, calibration and latency, and refuses
to promote a model that does not beat the rules.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from api import constants
from api.services import features
from api.services.ops import laya_eval
from api.services.routing import brain, decision
from api.tests.support.fake_redis import FakeRedis


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    features.clear_snapshot()
    laya_eval.reset_breaker()
    monkeypatch.setattr(constants, "LAYA_GUARDRAILS_ENABLED", False)
    monkeypatch.setattr(constants, "LAYA_ROLLBACK_ENABLED", False)
    monkeypatch.setattr(constants, "LAYA_URL", "http://laya.test")
    monkeypatch.setattr(constants, "LAYA_ROUTING", "on")
    monkeypatch.setattr(constants, "LAYA_HARD_DEADLINE_MS", 50)
    monkeypatch.setattr(constants, "LAYA_BREAKER_FAILURES", 3)
    monkeypatch.setattr(constants, "LAYA_BREAKER_COOLDOWN_SECONDS", 60)
    yield
    features.clear_snapshot()
    laya_eval.reset_breaker()


def _answer(label, confidence=0.9, **kw):
    async def chooser(question, labels, text):
        return decision.Decision(label=label, confidence=confidence, elapsed_ms=5, **kw)

    return chooser


@pytest.mark.asyncio
async def test_rollback_means_rules_and_laya_is_never_called(monkeypatch):
    calls = []

    async def spy(*args, **kwargs):
        calls.append(1)
        return decision.Decision("deep", 0.99, 1)

    monkeypatch.setattr(decision, "choose", spy)
    monkeypatch.setattr(constants, "LAYA_ROLLBACK_ENABLED", True)
    routed = await brain.route("hi")
    assert routed.kind == "quick" and routed.source == "rules"
    assert routed.abstained == "rolled_back"
    assert calls == []
    made = await laya_eval.guarded_choose("q", brain.KINDS, "hi", chooser=spy)
    assert made.abstained == "rolled_back" and calls == []


@pytest.mark.asyncio
async def test_guardrails_off_is_exactly_the_old_path(monkeypatch):
    made = await laya_eval.guarded_choose(
        "q", brain.KINDS, "hi", chooser=_answer("steps")
    )
    assert made.label == "steps"


@pytest.mark.asyncio
async def test_hard_deadline_and_errors_abstain(monkeypatch):
    monkeypatch.setattr(constants, "LAYA_GUARDRAILS_ENABLED", True)

    async def slow(*_args):
        await asyncio.sleep(1)

    async def boom(*_args):
        raise RuntimeError("down")

    late = await laya_eval.guarded_choose("q", brain.KINDS, "hi", chooser=slow)
    assert late.label is None and late.abstained == "deadline"
    assert late.elapsed_ms < 500
    broken = await laya_eval.guarded_choose("q", brain.KINDS, "hi", chooser=boom)
    assert broken.abstained == "error"


@pytest.mark.asyncio
async def test_breaker_opens_after_repeated_failures_and_resets_on_success(monkeypatch):
    monkeypatch.setattr(constants, "LAYA_GUARDRAILS_ENABLED", True)
    calls = []

    async def failing(*_args):
        calls.append(1)
        return decision.Decision(None, None, 5, "timeout")

    for _ in range(3):
        await laya_eval.guarded_choose("q", brain.KINDS, "hi", chooser=failing)
    assert laya_eval.breaker_state()["open"] is True
    skipped = await laya_eval.guarded_choose("q", brain.KINDS, "hi", chooser=failing)
    assert skipped.abstained == "circuit_open"
    assert len(calls) == 3
    # Cooled down: asked again, and a success closes it.
    laya_eval._BREAKER.open_until = 0
    ok = await laya_eval.guarded_choose(
        "q", brain.KINDS, "hi", chooser=_answer("quick")
    )
    assert ok.label == "quick"
    assert laya_eval.breaker_state()["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_on_mode_falls_back_to_rules_when_the_breaker_is_open(monkeypatch):
    monkeypatch.setattr(constants, "LAYA_GUARDRAILS_ENABLED", True)
    laya_eval._BREAKER.open_until = 10**12
    routed = await brain.route("Draft a reply to Ravi about the proposal")
    assert routed.source == "laya_fallback"
    assert routed.kind == "steps"
    assert routed.abstained == "circuit_open"


@pytest.mark.asyncio
async def test_truncation_is_reported(monkeypatch):
    import httpx

    real = httpx.AsyncClient

    def handler(request):
        return httpx.Response(
            200, json={"answers": {"decision": {"choice": "deep", "confidence": 0.9}}}
        )

    monkeypatch.setattr(
        decision.httpx,
        "AsyncClient",
        lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}),
    )
    short = await decision.choose("q", brain.KINDS, "hello")
    long = await decision.choose("q", brain.KINDS, "x" * (decision.MAX_TEXT_CHARS + 1))
    assert short.truncated is False
    assert long.truncated is True


def test_shadow_counters_never_carry_text():
    fields = laya_eval.shadow_fields(
        "quick", decision.Decision("deep", 0.8, 120, truncated=True)
    )
    assert fields == [
        "total",
        "latency:le_200",
        "truncated",
        "disagree",
        "disagree:quick->deep",
    ]
    abstained = laya_eval.shadow_fields(
        "steps", decision.Decision(None, None, 5, "timeout")
    )
    assert "abstained:timeout" in abstained


@pytest.mark.asyncio
async def test_shadow_report_sums_the_counters(monkeypatch):
    monkeypatch.setattr(constants, "LAYA_GUARDRAILS_ENABLED", True)
    redis = FakeRedis()
    await laya_eval.record_shadow(
        "quick", decision.Decision("quick", 0.9, 10), client=redis
    )
    await laya_eval.record_shadow(
        "quick", decision.Decision("deep", 0.9, 10), client=redis
    )
    await laya_eval.record_shadow(
        "steps", decision.Decision(None, None, 10, "low_confidence"), client=redis
    )
    report = await laya_eval.shadow_report(days=1, client=redis)
    assert report["total"] == 3
    assert report["agreement"] == 0.5
    assert report["abstention"] == round(1 / 3, 4)
    for key in redis.hashes:
        for name in redis.hashes[key]:
            assert b" " not in name  # counter names, never message text


def test_the_labelled_set_loads_and_is_balanced():
    samples = laya_eval.load_samples()
    assert len(samples) >= 50
    assert {s.expected for s in samples} == set(brain.KINDS)
    assert {"normal", "ambiguous", "adversarial"} <= {s.category for s in samples}
    assert {"en", "hi", "ta", "te"} <= {s.language for s in samples}
    assert len({s.id for s in samples}) == len(samples)


def test_a_malformed_line_is_an_error_not_a_skip(tmp_path):
    bad = tmp_path / "set.jsonl"
    bad.write_text(
        json.dumps({"id": "a", "text": "hi", "expected": "quick"}) + "\n{not json\n"
    )
    with pytest.raises(ValueError, match=":2:"):
        laya_eval.load_samples(bad)


@pytest.mark.asyncio
async def test_evaluation_report_and_verdict():
    samples = laya_eval.load_samples()

    # A "model" that answers the label exactly, except on adversarial cases
    # where it abstains: it should beat the rules, but the small set keeps
    # it from being promoted.
    expected = {s.text: s.expected for s in samples}
    category = {s.text: s.category for s in samples}

    async def oracle(question, labels, text):
        if category[text] == "adversarial":
            return decision.Decision(None, 0.4, 30, "low_confidence")
        return decision.Decision(expected[text], 0.9, 30)

    report = await laya_eval.evaluate(samples, chooser=oracle)
    assert report["laya_configured"] is True
    assert report["laya"]["answered_accuracy"] == 1.0
    assert report["combined"]["accuracy"] >= report["rules"]["accuracy"]
    assert report["laya"]["latency_ms"]["p95"] == 30.0
    assert report["laya"]["calibration"]["ece"] is not None
    per_kind = report["laya"]["per_kind"]
    assert set(per_kind) == set(brain.KINDS)
    assert all(row["false_positive"] == 0 for row in per_kind.values())
    assert report["verdict"]["promote"] is False
    assert any("300" in reason for reason in report["verdict"]["reasons"])
    page = laya_eval.render_markdown(report)
    assert "# Laya routing evaluation" in page and "| quick |" in page


@pytest.mark.asyncio
async def test_no_model_means_no_comparison():
    samples = laya_eval.load_samples()[:5]

    async def off(question, labels, text):
        return decision.Decision(None, None, 0, "off")

    report = await laya_eval.evaluate(samples, chooser=off)
    assert report["laya_configured"] is False
    assert report["verdict"]["promote"] is False
    assert "nothing was measured" in report["verdict"]["reasons"][0]


@pytest.mark.asyncio
async def test_a_worse_model_is_not_promoted(monkeypatch):
    samples = laya_eval.load_samples()

    async def always_deep(question, labels, text):
        return decision.Decision("deep", 0.99, 10)

    report = await laya_eval.evaluate(samples, chooser=always_deep)
    assert report["verdict"]["promote"] is False
    assert any("must beat them" in r for r in report["verdict"]["reasons"])
