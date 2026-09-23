"""The report that has to exist before deciding how credits charge for models.

It answers one question: what does a reply, a routine, a call, a Decibyl turn
actually use, vendor by vendor, split into input, cached input, cache write
and output. Two sources feed it -- run receipts and direct calls -- and the
tests below hold the three things that would make it lie: pricing a direct
call wrongly, calling an unpriced model free, and mixing work kinds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from api.services.billing import model_usage, token_report

RATE = lambda mpaise: SimpleNamespace(rate_mpaise=mpaise)  # noqa: E731


def _report(run_rows=(), direct_rows=(), rates=None):
    return token_report.summarise(
        run_rows=list(run_rows), direct_rows=list(direct_rows), rates=rates or {}
    )


class TestRuns:
    def test_run_lines_keep_the_vendors_split_and_cost(self):
        r = _report(
            run_rows=[
                (1, "textchat", "openai", "gpt-4.1-mini", "llm_input", 1000, 3.0),
                (1, "textchat", "openai", "gpt-4.1-mini", "llm_cached", 4000, 1.0),
                (1, "textchat", "openai", "gpt-4.1-mini", "llm_output", 200, 2.5),
                (2, "plivo", "openai", "gpt-4.1-mini", "llm_input", 3000, 9.0),
                # not a token line: ignored here, it belongs to the call report
                (2, "plivo", "sarvam", "saaras", "stt", 60, 50.0),
            ]
        )
        line = r["by_model"][0]
        assert (line["source"], line["provider"], line["model"]) == (
            "run",
            "openai",
            "gpt-4.1-mini",
        )
        assert line["input_tokens"] == 4000 and line["cached_tokens"] == 4000
        assert line["output_tokens"] == 200 and line["calls"] == 2
        assert line["vendor_cost_paise"] == 15.5
        assert line["cached_share"] == 0.5

    def test_voice_and_text_are_separate_kinds_of_work(self):
        r = _report(
            run_rows=[
                (1, "textchat", "openai", "m", "llm_input", 100, 1.0),
                (2, "textchat", "openai", "m", "llm_input", 300, 3.0),
                (3, "plivo", "openai", "m", "llm_input", 5000, 50.0),
            ]
        )
        kinds = {w["work"]: w for w in r["by_work"] if w["source"] == "run"}
        assert kinds["text"]["tokens_per_run"] == {
            "count": 2,
            "median": 200.0,
            "p90": 300,
        }
        assert kinds["voice"]["tokens_per_run"]["count"] == 1


class TestDirectCalls:
    def test_priced_with_the_same_vendor_rule_as_runs(self):
        # Anthropic reports input net of its cache; the split must not
        # subtract the cache a second time.
        rates = {
            ("llm_input", "anthropic", "claude-x"): RATE(300_000),
            ("llm_cached", "anthropic", "claude-x"): RATE(30_000),
            ("llm_cache_write", "anthropic", "claude-x"): RATE(375_000),
            ("llm_output", "anthropic", "claude-x"): RATE(1_500_000),
        }
        r = _report(
            direct_rows=[("decibyl", "anthropic", "claude-x", 1000, 100, 9000, 0)],
            rates=rates,
        )
        line = r["by_model"][0]
        assert (line["input_tokens"], line["cached_tokens"], line["output_tokens"]) == (
            1000,
            9000,
            100,
        )
        # 1k * 300 paise + 9k * 30 paise + 0.1k * 1500 paise, per 1k, in paise
        assert line["vendor_cost_paise"] == pytest.approx(300 + 270 + 150)

    def test_the_provider_fallback_prices_a_model_without_its_own_row(self):
        rates = {
            ("llm_input", "openai", ""): RATE(10_000),
            ("llm_output", "openai", ""): RATE(40_000),
        }
        r = _report(
            direct_rows=[("builder", "openai", "gpt-new", 1000, 1000, 0, 0)],
            rates=rates,
        )
        assert r["by_model"][0]["vendor_cost_paise"] == pytest.approx(50)

    def test_an_unpriced_model_is_said_to_be_unpriced_not_free(self):
        r = _report(direct_rows=[("builder", "mystery", "m", 1000, 10, 0, 0)])
        line = r["by_model"][0]
        assert line["vendor_cost_paise"] == 0
        assert set(line["unpriced"]) == {"llm_input", "llm_output"}

    def test_unattributed_calls_are_counted(self):
        r = _report(
            direct_rows=[
                ("decibyl", "openai", "m", 10, 1, 0, 0),
                ("unattributed", "openai", "m", 10, 1, 0, 0),
            ]
        )
        assert r["totals"]["direct_calls"] == 2
        assert r["totals"]["unattributed_calls"] == 1

    def test_per_feature_spread(self):
        r = _report(
            direct_rows=[
                ("decibyl", "openai", "m", n, 0, 0, 0)
                for n in (100, 200, 300, 400, 1000)
            ]
        )
        work = next(w for w in r["by_work"] if w["work"] == "decibyl")
        assert work["tokens_per_call"] == {"count": 5, "median": 300.0, "p90": 1000}


def test_the_report_names_what_it_cannot_see():
    assert _report()["not_metered"]


def test_csv_has_a_header_and_one_row_per_model():
    r = _report(direct_rows=[("x", "openai", "m", 1, 1, 0, 0)])
    rows = token_report.as_csv_rows(r)
    assert rows[0][0] == "source" and len(rows) == 2


@pytest.mark.asyncio
async def test_build_reads_the_direct_table(db_session, async_session):
    await model_usage.record(
        provider="openai",
        model="gpt-4.1-mini",
        usage={"prompt_tokens": 500, "completion_tokens": 50},
    )
    now = datetime.now(UTC)
    report = await token_report.build(
        async_session, start=now - timedelta(hours=1), end=now + timedelta(hours=1)
    )
    line = next(l for l in report["by_model"] if l["source"] == "direct")
    assert (line["provider"], line["input_tokens"], line["output_tokens"]) == (
        "openai",
        500,
        50,
    )


def test_the_report_is_a_superadmin_route():
    from api.routes import billing_dashboard as route

    paths = {r.path for r in route.router.routes}
    assert "/admin/billing/tokens/by-model" in paths
    assert (
        len([r for r in route.router.routes if r.path == "/admin/billing/tokens"]) == 1
    )
    # The whole router is superadmin-only; a route added to it inherits that.
    assert any(
        "_require_staff_role" in getattr(d.dependency, "__qualname__", "")
        for d in route.router.dependencies
    )


@pytest.mark.asyncio
async def test_transcription_is_reported_in_minutes_not_as_token_calls(
    db_session, async_session
):
    """Until 23 Sept a transcribed upload was recorded nowhere. Now it is a
    row with audio seconds, reported on its own -- and never counted as a
    direct token call with no tokens in it."""
    with model_usage.scope(organization_id=None, feature="recording_transcription"):
        await model_usage.record_audio(provider="deepgram", model="nova-3", seconds=90)
        # A vendor that reports no length is still counted.
        await model_usage.record_audio(
            provider="deepgram", model="nova-3", seconds=None
        )
    with model_usage.scope(organization_id=None, feature="dialer_import"):
        await model_usage.record_audio(provider="sarvam", model="saarika", seconds=30)
    now = datetime.now(UTC)
    report = await token_report.build(
        async_session, start=now - timedelta(hours=1), end=now + timedelta(hours=1)
    )
    audio = {(a["feature"], a["provider"]): a for a in report["audio"]}
    upload = audio[("recording_transcription", "deepgram")]
    assert (
        upload["calls"],
        upload["audio_seconds"],
        upload["calls_without_length"],
    ) == (
        2,
        90.0,
        1,
    )
    assert audio[("dialer_import", "sarvam")]["audio_seconds"] == 30.0
    assert not any(l["provider"] in ("deepgram", "sarvam") for l in report["by_model"])


def test_a_transcription_service_is_named_by_its_vendor():
    class DeepgramTranscriptionService:
        pass

    assert model_usage.provider_of(DeepgramTranscriptionService()) == "deepgram"


def test_the_upload_route_records_what_it_transcribed():
    import inspect

    from api.routes import workflow_recording

    source = inspect.getsource(workflow_recording.transcribe_audio)
    assert "record_audio" in source and "recording_transcription" in source
