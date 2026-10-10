"""A model call that records nowhere is a bill nobody can explain.

Model calls reach a vendor through two doors, and each has its own record:

* a **run's own turns** (a call, a text reply, a routine, a trigger, a task)
  write their tokens onto the run -- ``usage_info`` and the receipt
  (``call_cost_items``);
* **everything else** -- the builder client, and one-shot ``run_inference``
  calls that go round the frame path -- writes ``model_usage``.

The gap this file closes: five one-shot ``run_inference`` callers (the channel
fold, the in-call variable extractor, the in-call summary, the handoff summary,
and the eval simulator and judge) and the acceptable-use screen spent tokens on
the platform key and left either nothing or a row with no organisation. The
first test is the guard: it fails on the pull request that adds a call site
with no record, which is the one place anyone will see it.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.metrics.metrics import LLMTokenUsage

from api.services.billing import model_usage
from api.services.compliance import acceptable_use
from api.services.workflow import text_chat_session_service as text_session_service

API_ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("services", "routes", "tasks")

#: Modules that call a model and are recorded some way other than
#: ``model_usage``. Each says how; an entry with no reason is not allowed.
RECORDED_ELSEWHERE = {
    "services/pipecat/run_pipeline.py": "the run's own LLM is metered onto the run; the warm-up is listed in token_report.NOT_METERED",
    "services/pipecat/service_factory.py": "the factory builds services for callers; each caller is listed here or records itself",
    "services/workflow/text_chat_runner.py": "a text turn's tokens are returned as execution.usage and merged onto the run",
    "services/workflow/disposition_run.py": "usage returned to the caller, which puts it on usage_info",
    "services/workflow/qa/analysis.py": "tokens added to the run's usage_info",
    "services/workflow/qa/node_summary.py": "tokens added to the caller's usage_total",
}

#: What a module must mention to record through ``model_usage``. Not
#: ``accumulate_token_usage``: the channel fold called that and then only
#: logged the total, which is exactly the gap this guard exists for.
RECORDERS = ("model_usage",)


_BUILDERS = {"create_llm_service", "create_llm_service_from_provider"}


def _calls_a_model(tree: ast.AST) -> bool:
    """``x.run_inference(...)`` (a one-shot call round the frame path), or a
    module that builds an LLM service for itself."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "run_inference":
            return True
        if isinstance(func, ast.Name) and func.id in _BUILDERS:
            return True
    return False


def _sites() -> dict[str, str]:
    found: dict[str, str] = {}
    for top in SCANNED:
        for path in sorted((API_ROOT / top).rglob("*.py")):
            source = path.read_text()
            if "run_inference" not in source and "create_llm_service" not in source:
                continue
            if _calls_a_model(ast.parse(source)):
                found[path.relative_to(API_ROOT).as_posix()] = source
    return found


def test_a_new_model_call_site_must_say_where_it_is_recorded():
    """Every module that makes a one-shot model call records it or is listed
    with the reason it is recorded elsewhere."""
    sites = _sites()
    assert sites, "the scan found no run_inference call at all; the guard is blind"
    unrecorded = [
        name
        for name, source in sites.items()
        if name not in RECORDED_ELSEWHERE
        and not any(word in source for word in RECORDERS)
    ]
    assert not unrecorded, (
        f"{unrecorded} call a model with run_inference and record the tokens "
        "nowhere. Call model_usage.record_inference(llm, organization_id=..., "
        "feature=...) straight after the call, or add the module to "
        "RECORDED_ELSEWHERE with the reason."
    )


def test_the_exemptions_still_exist_and_say_why():
    sites = _sites()
    for name, reason in RECORDED_ELSEWHERE.items():
        assert reason.strip(), f"{name} is exempt without a reason"
        assert name in sites, (
            f"{name} no longer calls run_inference; drop it from RECORDED_ELSEWHERE"
        )


@pytest.fixture
def rows():
    """The rows ``model_usage`` would write, without a database."""
    captured: list[dict] = []

    async def fake_write(row):
        captured.append(row)

    with patch.object(model_usage, "_write", fake_write):
        yield captured


def _llm(model="claude-test"):
    class AnthropicLLMService:
        model_name = model
        last_inference_usage = LLMTokenUsage(
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            cache_read_input_tokens=40,
            cache_creation_input_tokens=10,
        )

    return AnthropicLLMService()


@pytest.mark.asyncio
class TestOneShotInferenceIsRecorded:
    async def test_it_writes_a_row_for_the_organisation_and_the_feature(self, rows):
        await model_usage.record_inference(
            _llm(), organization_id=34, feature="channel_fold"
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["organization_id"] == 34
        assert row["feature"] == "channel_fold"
        assert row["provider"] == "anthropic"
        assert row["model"] == "claude-test"
        # Claude reports input net of its cache, so the cache is not inside it.
        assert row["prompt_tokens"] == 120
        assert row["completion_tokens"] == 30
        assert row["cache_read_input_tokens"] == 40
        assert row["cache_creation_input_tokens"] == 10

    async def test_a_service_that_reported_nothing_writes_nothing(self, rows):
        llm = _llm()
        llm.last_inference_usage = None
        await model_usage.record_inference(llm, organization_id=1, feature="x")
        assert rows == []

    async def test_a_failed_write_never_costs_the_reply(self):
        with patch.object(model_usage, "_write", AsyncMock(side_effect=OSError("db"))):
            await model_usage.record_inference(
                _llm(), organization_id=1, feature="channel_fold"
            )

    async def test_the_eval_simulator_and_judge_are_recorded_for_the_org(self, rows):
        from api.services.evals import runner

        llm = _llm()
        llm.run_inference = AsyncMock(return_value=" hello ")
        said = await runner._say(
            llm,
            "sys",
            [{"role": "user", "content": "hi"}],
            organization_id=34,
            feature="eval_caller",
        )
        assert said == "hello"
        assert [(r["organization_id"], r["feature"]) for r in rows] == [
            (34, "eval_caller")
        ]


@pytest.mark.asyncio
class TestTheAcceptableUseScreenBelongsToAnOrganisation:
    async def test_the_screen_is_recorded_against_the_org_it_screened(self, rows):
        model = SimpleNamespace(provider="anthropic", model="m", api_key="k")

        async def complete(**_):
            # What the real client does after the vendor answers.
            await model_usage.record(
                provider="anthropic", model="m", usage={"prompt_tokens": 9}
            )
            return SimpleNamespace(tool_calls=())

        with (
            patch.object(
                acceptable_use.settings, "resolve_model", AsyncMock(return_value=model)
            ),
            patch.object(acceptable_use, "complete", complete),
        ):
            await acceptable_use.screen(
                None, instructions="Book appointments.", organization_id=34
            )
        assert [(r["organization_id"], r["feature"]) for r in rows] == [
            (34, "acceptable_use")
        ]


@pytest.mark.asyncio
async def test_an_agent_turn_writes_its_tokens_onto_the_run(monkeypatch):
    """A run's own model calls are metered on the run, not in ``model_usage``:
    the turn's usage reaches ``usage_info`` and the receipt is written. This is
    the door a routine, a channel reply and a chat on an agent all use."""
    session = SimpleNamespace(
        session_data={"turns": [{"id": "t1", "status": "pending"}]},
        checkpoint=None,
        revision=1,
        workflow_run=SimpleNamespace(usage_info={}),
    )
    usage = {
        "llm": {
            "AnthropicLLMService#0|claude-test": {
                "prompt_tokens": 500,
                "completion_tokens": 40,
                "total_tokens": 540,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 0,
            }
        }
    }
    execution = SimpleNamespace(
        assistant_text="done",
        assistant_created_at="2026-10-10T00:00:00Z",
        events=[],
        usage=usage,
        checkpoint={},
        initial_context={},
        gathered_context={},
        state={},
        is_completed=False,
    )
    update_run = AsyncMock()
    receipt = AsyncMock()
    monkeypatch.setattr(
        text_session_service,
        "execute_text_chat_pending_turn",
        AsyncMock(return_value=execution),
    )
    monkeypatch.setattr(
        text_session_service.db_client, "update_workflow_run_text_session", AsyncMock()
    )
    monkeypatch.setattr(
        text_session_service.db_client, "update_workflow_run", update_run
    )
    monkeypatch.setattr(
        text_session_service,
        "_reload_text_chat_session",
        AsyncMock(return_value=session),
    )
    monkeypatch.setattr("api.services.billing.tasks.record_text_run_cost", receipt)

    await text_session_service.execute_pending_text_chat_turn(
        workflow_id=1, run_id=99, text_session=session
    )

    written = update_run.await_args.kwargs["usage_info"]["llm"]
    only = next(iter(written.values()))
    assert only["prompt_tokens"] == 500
    assert only["cache_read_input_tokens"] == 100
    receipt.assert_awaited_once_with(99)
