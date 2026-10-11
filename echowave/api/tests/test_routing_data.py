"""Routing data for training the router, and the KTO export.

* Every routed call (Auto) leaves a ``routing_decision`` learning event:
  redacted input, the shape of the input, the candidates, the chosen model and
  who chose it, Laya's answer and confidence, latency, and later the tokens,
  cost and outcome. Same consent and org scoping as the rest of the loop.
* ``routing_explore`` (off by default) runs the next-best model on about 1% of
  calls that are safe to repeat, never shows it, and counts its cost against
  the weekly learning cap. It never runs on a call that faces a customer or
  has a side effect.
* KTO: one row per output with a desirable/undesirable label; thumbs alone are
  enough.
"""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api import constants
from api.db import db_client
from api.db.models import CallCostItemModel
from api.db.training_loop_models import LearningEventModel
from api.enums import AgentEventActor, AgentEventKind
from api.services import features, feedback
from api.services import training_loop as loop
from api.services.ops import laya_eval
from api.services.routing import brain, decision, explore
from api.services.training_loop import budget, consent, export, hooks
from api.services.training_loop import record as loop_record
from api.tests.test_training_loop import _as, _rows, _workspace

PHONE = "9876543210"


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(constants, "TRAINING_LOOP_ENABLED", True)


@pytest.fixture
def exploring(on, monkeypatch):
    monkeypatch.setattr(constants, "ROUTING_EXPLORE_ENABLED", True)


@pytest.fixture
async def a(db_session, async_session):
    return await _workspace(db_session, async_session, "ra")


@pytest.fixture
async def b(db_session, async_session):
    return await _workspace(db_session, async_session, "rb")


@pytest.fixture
def auto(monkeypatch):
    """Auto is in charge, and Laya (in shadow) says "deep" at 0.9."""
    monkeypatch.setattr(brain, "workspace_is_auto", AsyncMock(return_value=True))
    monkeypatch.setattr(constants, "LAYA_ROUTING", "shadow")
    monkeypatch.setattr(decision, "enabled", lambda: True)
    monkeypatch.setattr(
        laya_eval,
        "guarded_choose",
        AsyncMock(
            return_value=decision.Decision(label="deep", confidence=0.9, elapsed_ms=12)
        ),
    )


# --- routing decisions --------------------------------------------------------


class TestRoutingDecisions:
    async def test_a_decibyl_turn_is_kept_with_what_the_router_saw(self, on, auto, a):
        routed = await brain.auto_route(
            a.org,
            f"Hi, call me on {PHONE}. What is due this week?",
            feature="decibyl",
            ref="decibyl_thread:main",
        )
        assert routed is not None
        (row,) = await _rows(a.org, event_type=loop.ROUTING_DECISION)
        assert (row.scope, row.workflow_id, row.source) == (
            loop.SCOPE_DECIBYL,
            None,
            loop.ROUTING,
        )
        assert PHONE not in row.input_text and "due this week" in row.input_text
        assert row.input_ref == "decibyl_thread:main"
        data = row.data
        assert data["feature"] == "decibyl"
        assert data["mode"] == "shadow"
        assert data["input"]["chars"] > 0 and data["input"]["questions"] == 1
        assert {c["preset"] for c in data["candidates"]} == {
            "everyday",
            "smart",
            "deep",
        }
        # Shadow: the rules chose, Laya's answer and confidence are beside it.
        assert data["chosen"] == {
            "kind": routed.kind,
            "preset": routed.preset,
            "source": "rules",
        }
        assert data["laya"]["kind"] == "deep" and data["laya"]["confidence"] == 0.9
        assert data["latency_ms"] >= 0
        assert data["outcomes"] == [] and data["cost_paise"] is None
        assert PHONE not in json.dumps(data)

    async def test_an_agents_chat_is_kept_against_the_agent(self, on, auto, a):
        await brain.auto_route(
            a.org,
            "Draft a reply to the supplier",
            feature="text_chat",
            workflow_id=a.workflow,
            ref="workflow_run:1",
        )
        (row,) = await _rows(a.org, event_type=loop.ROUTING_DECISION)
        assert (row.scope, row.workflow_id) == (loop.SCOPE_AGENT, a.workflow)

    async def test_laya_deciding_is_the_chosen_source(self, on, auto, a, monkeypatch):
        monkeypatch.setattr(constants, "LAYA_ROUTING", "on")
        await brain.auto_route(a.org, "hello", feature="decibyl", ref="r")
        (row,) = await _rows(a.org, event_type=loop.ROUTING_DECISION)
        assert row.data["chosen"]["source"] == "laya"
        assert row.data["chosen"]["kind"] == "deep"

    async def test_a_caller_that_names_no_feature_records_nothing(self, on, auto, a):
        await brain.auto_route(a.org, "hello")
        assert await _rows(a.org) == []

    async def test_flag_off_nothing_is_kept_and_the_route_is_unchanged(self, auto, a):
        routed = await brain.auto_route(a.org, "hello", feature="decibyl", ref="r")
        assert routed is not None
        assert await _rows(a.org) == []

    async def test_consent_off_nothing_is_kept(self, on, auto, a):
        await consent.set_use_feedback(
            organization_id=a.org, user=a.user, allowed=False
        )
        routed = await brain.auto_route(a.org, "hello", feature="decibyl", ref="r")
        assert routed is not None
        assert await _rows(a.org) == []

    async def test_a_broken_recorder_never_costs_the_route(
        self, on, auto, a, monkeypatch
    ):
        monkeypatch.setattr(
            db_client, "insert_learning_event", AsyncMock(side_effect=RuntimeError)
        )
        assert (
            await brain.auto_route(a.org, "hello", feature="decibyl", ref="r")
            is not None
        )

    async def test_each_workspace_keeps_its_own(self, on, auto, a, b):
        await brain.auto_route(a.org, "hello", feature="decibyl", ref="r")
        assert await _rows(b.org) == []


# --- outcomes -----------------------------------------------------------------


class TestRoutingOutcomes:
    async def _decision(self, ws, ref, workflow_id=None):
        await hooks.routing_decision(
            organization_id=ws.org,
            workflow_id=workflow_id,
            feature="text_chat",
            ref=ref,
            text="hello",
            attachments=0,
            routed=brain.Route(kind="quick", preset="everyday", source="rules"),
            candidates=brain.PRESET_FOR,
            mode="shadow",
            latency_ms=3,
        )

    async def test_an_outcome_links_with_the_runs_tokens_and_cost(
        self, on, a, async_session, db_session
    ):
        run = await db_session.create_workflow_run(
            name="r",
            workflow_id=a.workflow,
            mode="textchat",
            user_id=a.user.id,
            organization_id=a.org,
        )
        async_session.add(
            CallCostItemModel(
                workflow_run_id=run.id,
                component="llm_input",
                units=1,
                unit_rate_mpaise=0,
                cost_paise=600,
                provider_cost_paise=300,
            )
        )
        await async_session.flush()
        ref = f"workflow_run:{run.id}"
        await self._decision(a, ref, a.workflow)
        await hooks.routing_outcome(
            organization_id=a.org, ref=ref, outcome_type="eval_pass", outcome_ref="e:1"
        )
        (row,) = await _rows(a.org, event_type=loop.ROUTING_DECISION)
        assert row.data["cost_paise"] == 300
        assert [o["type"] for o in row.data["outcomes"]] == ["eval_pass"]
        assert row.data["outcomes"][0]["ref"] == "e:1"

    async def test_a_thumb_on_decibyls_thread_links_to_its_decision(
        self, on, auto, a, monkeypatch
    ):
        monkeypatch.setattr(constants, "REPLY_FEEDBACK_ENABLED", True)
        await db_client.record_agent_event(
            organization_id=a.org,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.HUMAN.value,
            summary="What is due this week?",
            payload={"body": "What is due this week?"},
        )
        await brain.auto_route(
            a.org,
            "What is due this week?",
            feature="decibyl",
            ref="decibyl_thread:main",
        )
        reply = await db_client.record_agent_event(
            organization_id=a.org,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary="Two invoices.",
            payload={"body": "Two invoices.", "model": "some-model"},
        )
        await feedback.submit(
            organization_id=a.org,
            user_id=a.user.id,
            subject_kind="reply",
            subject_id=getattr(reply, "id", reply),
            verdict="yes",
        )
        (decision_row,) = await _rows(a.org, event_type=loop.ROUTING_DECISION)
        assert [o["type"] for o in decision_row.data["outcomes"]] == [loop.THUMBS_UP]
        # The thumb itself now carries the question it answered.
        (thumb,) = await _rows(a.org, event_type=loop.THUMBS_UP)
        assert thumb.input_text == "What is due this week?"
        assert thumb.model_output == "Two invoices."

    async def test_no_decision_no_row_and_no_error(self, on, a):
        await hooks.routing_outcome(
            organization_id=a.org, ref="workflow_run:999", outcome_type="eval_pass"
        )
        assert await _rows(a.org) == []

    async def test_an_outcome_never_reaches_another_workspaces_decision(self, on, a, b):
        await self._decision(a, "decibyl_thread:main")
        await hooks.routing_outcome(
            organization_id=b.org,
            ref="decibyl_thread:main",
            outcome_type=loop.THUMBS_UP,
        )
        (row,) = await _rows(a.org, event_type=loop.ROUTING_DECISION)
        assert row.data["outcomes"] == []

    async def test_consent_off_links_nothing(self, on, a):
        await self._decision(a, "decibyl_thread:main")
        await consent.set_use_feedback(
            organization_id=a.org, user=a.user, allowed=False
        )
        await hooks.routing_outcome(
            organization_id=a.org,
            ref="decibyl_thread:main",
            outcome_type=loop.THUMBS_UP,
        )
        (row,) = await _rows(a.org, event_type=loop.ROUTING_DECISION)
        assert row.data["outcomes"] == []


# --- counterfactual sampling ------------------------------------------------------------

SAFE = explore.Safety(customer_facing=False, idempotent=True, side_effects=False)


def _runner(cost=40, output="The other model said so."):
    async def run(preset, text):
        run.calls.append((preset, text))
        return explore.Sample(
            output=output,
            cost_paise=cost,
            prompt_tokens=10,
            completion_tokens=5,
            model=f"model-{preset}",
        )

    run.calls = []
    return run


async def _sample(
    ws, runner, *, feature="classify", safety=SAFE, roll=lambda: 0.0, **kw
):
    task = await explore.maybe_sample(
        organization_id=ws.org,
        workflow_id=kw.pop("workflow_id", ws.workflow),
        feature=feature,
        safety=safety,
        text=kw.pop("text", f"Is this spam? Call {PHONE}"),
        chosen_preset=kw.pop("chosen_preset", "everyday"),
        ref="r1",
        runner=runner,
        roll=roll,
        **kw,
    )
    if task is not None:
        await task
    return task


async def _spend(ws, async_session, db_session, paise):
    run = await db_session.create_workflow_run(
        name="r",
        workflow_id=ws.workflow,
        mode="textchat",
        user_id=ws.user.id,
        organization_id=ws.org,
    )
    run.created_at = datetime.now(UTC) - timedelta(days=1)
    async_session.add(
        CallCostItemModel(
            workflow_run_id=run.id,
            component="llm_input",
            units=1,
            unit_rate_mpaise=0,
            cost_paise=paise * 2,
            provider_cost_paise=paise,
        )
    )
    await async_session.flush()


class TestCounterfactualSampling:
    async def test_it_is_off_by_default(self):
        assert constants.ROUTING_EXPLORE_ENABLED is False
        assert "routing_explore" in features.FLAGS
        assert explore.SAMPLE_RATE_PERCENT == 1.0

    async def test_the_ladder(self):
        assert explore.next_best("everyday") == "smart"
        assert explore.next_best("smart") == "deep"
        assert explore.next_best("deep") == "smart"
        assert explore.next_best("something_else") is None

    async def test_a_safe_sample_runs_the_next_best_and_keeps_it(
        self, exploring, a, async_session, db_session
    ):
        await _spend(a, async_session, db_session, 100_000)
        runner = _runner(cost=40)
        task = await _sample(a, runner)
        assert task is not None
        assert runner.calls[0][0] == "smart"
        (row,) = await _rows(a.org, event_type=loop.ROUTING_COUNTERFACTUAL)
        assert row.model_output == "The other model said so."
        assert PHONE not in row.input_text
        assert row.model == "model-smart"
        assert (row.prompt_tokens, row.completion_tokens) == (10, 5)
        assert row.data["cost_paise"] == 40
        assert row.data["shown_to_user"] is False
        assert (row.data["chosen_preset"], row.data["alternative_preset"]) == (
            "everyday",
            "smart",
        )
        assert (row.scope, row.workflow_id) == (loop.SCOPE_AGENT, a.workflow)

    async def test_only_about_one_percent_are_sampled(
        self, exploring, a, async_session, db_session
    ):
        await _spend(a, async_session, db_session, 100_000)
        runner = _runner()
        assert await _sample(a, runner, roll=lambda: 0.011) is None
        assert await _sample(a, runner, roll=lambda: 0.999) is None
        assert runner.calls == []
        assert await _sample(a, runner, roll=lambda: 0.0099) is not None

    @pytest.mark.parametrize(
        "safety",
        [
            explore.Safety(customer_facing=True, idempotent=True, side_effects=False),
            explore.Safety(customer_facing=False, idempotent=False, side_effects=False),
            explore.Safety(customer_facing=False, idempotent=True, side_effects=True),
        ],
    )
    async def test_it_never_runs_on_a_call_that_is_not_safe_to_repeat(
        self, exploring, a, async_session, db_session, safety
    ):
        await _spend(a, async_session, db_session, 100_000)
        runner = _runner()
        assert await _sample(a, runner, safety=safety) is None
        assert runner.calls == [] and await _rows(a.org) == []

    @pytest.mark.parametrize("feature", sorted(explore.NEVER_FEATURES) + ["unknown"])
    async def test_it_never_runs_on_voice_chat_or_anything_that_sends(
        self, exploring, a, async_session, db_session, feature
    ):
        await _spend(a, async_session, db_session, 100_000)
        runner = _runner()
        # Even when the caller swears it is safe.
        assert await _sample(a, runner, feature=feature) is None
        assert runner.calls == [] and await _rows(a.org) == []

    async def test_the_allowlist_and_the_never_list_cannot_overlap(self):
        assert not (explore.EXPLORABLE_FEATURES & explore.NEVER_FEATURES)
        assert explore.EXPLORABLE_FEATURES == {"classify", "extract"}

    async def test_a_caller_must_answer_all_three_safety_questions(self):
        with pytest.raises(TypeError):
            explore.Safety(customer_facing=False)  # type: ignore[call-arg]

    async def test_no_live_call_site_samples_a_customer_facing_path(self):
        """The routed paths today are chat replies and Decibyl turns. None of
        them, and no voice or sending code, may call the sampler."""
        root = pathlib.Path(__file__).resolve().parents[1]
        callers = sorted(
            str(path.relative_to(root))
            for path in root.rglob("*.py")
            if "tests" not in path.parts
            and path.name != "explore.py"
            and "maybe_sample(" in path.read_text()
        )
        assert callers == [], callers

    async def test_flag_off_or_loop_off_nothing_runs(
        self, a, monkeypatch, async_session, db_session
    ):
        await _spend(a, async_session, db_session, 100_000)
        runner = _runner()
        assert await _sample(a, runner) is None  # both off
        monkeypatch.setattr(constants, "ROUTING_EXPLORE_ENABLED", True)
        assert await _sample(a, runner) is None  # loop off
        monkeypatch.setattr(constants, "ROUTING_EXPLORE_ENABLED", False)
        monkeypatch.setattr(constants, "TRAINING_LOOP_ENABLED", True)
        assert await _sample(a, runner) is None  # explore off
        assert runner.calls == []

    async def test_consent_off_nothing_runs_and_nothing_is_paid_for(
        self, exploring, a, async_session, db_session
    ):
        await _spend(a, async_session, db_session, 100_000)
        await consent.set_use_feedback(
            organization_id=a.org, user=a.user, allowed=False
        )
        runner = _runner()
        assert await _sample(a, runner) is None
        assert runner.calls == [] and await _rows(a.org) == []

    async def test_the_cost_counts_against_the_weekly_cap(
        self, exploring, a, async_session, db_session
    ):
        await _spend(a, async_session, db_session, 10_000)  # cap: 2% = 200 paise
        assert (
            await budget.weekly_cap_for_scope(
                organization_id=a.org, workflow_id=a.workflow
            )
            == 200
        )
        runner = _runner(cost=150)
        assert await _sample(a, runner) is not None  # 0 spent: room
        assert await _sample(a, runner) is not None  # 150 < 200: room
        # 300 >= 200: the cap is used up, whatever the dice say.
        assert await _sample(a, runner) is None
        assert len(runner.calls) == 2
        assert (
            await db_client.routing_explore_spend_paise(
                organization_id=a.org,
                workflow_id=a.workflow,
                since=datetime.now(UTC) - timedelta(days=7),
            )
            == 300
        )

    async def test_an_agent_with_no_spend_has_no_budget_to_explore_with(
        self, exploring, a
    ):
        runner = _runner()
        assert await _sample(a, runner) is None and runner.calls == []

    async def test_the_cap_never_exceeds_the_rupee_ceiling(self):
        assert budget.WEEKLY_LOOP_CEILING_RUPEES == 500
        assert budget.weekly_cap_paise(10**9) == 50_000

    async def test_another_workspaces_samples_do_not_use_up_this_ones_cap(
        self, exploring, a, b, async_session, db_session
    ):
        await _spend(a, async_session, db_session, 10_000)
        await _spend(b, async_session, db_session, 10_000)
        assert await _sample(b, _runner(cost=500)) is not None
        assert await _sample(a, _runner(cost=10)) is not None

    async def test_a_failing_alternative_costs_nothing_and_raises_nothing(
        self, exploring, a, async_session, db_session
    ):
        await _spend(a, async_session, db_session, 100_000)

        async def broken(preset, text):
            raise RuntimeError("provider down")

        assert await _sample(a, broken) is not None
        assert await _rows(a.org) == []

    async def test_agentless_work_is_kept_in_scope_decibyl(
        self, exploring, a, async_session, db_session
    ):
        await _spend(a, async_session, db_session, 100_000)
        await _sample(a, _runner(), workflow_id=None)
        (row,) = await _rows(a.org, event_type=loop.ROUTING_COUNTERFACTUAL)
        assert (row.scope, row.workflow_id) == (loop.SCOPE_DECIBYL, None)


# --- KTO ------------------------------------------------------------------------------


async def _thumb(ws, kind, n, *, prompt="What time do you open?", out="At nine."):
    assert await loop_record.record(
        organization_id=ws.org,
        event_type=kind,
        source=loop.REPLY,
        subject_key=f"reply:{n}:{ws.user.id}:{kind}",
        workflow_id=ws.workflow,
        user_id=ws.user.id,
        input_ref=f"agent_event:{n}",
        input_text=prompt,
        model_output=out,
    )


async def _kto(ws):
    result = await export.build(organization_id=ws.org, shape=export.KTO)
    return [json.loads(x) for x in result.lines]


class TestKto:
    async def test_thumbs_alone_are_enough(self, on, a):
        await _thumb(a, loop.THUMBS_UP, 1, prompt="Q1", out="Good answer")
        await _thumb(a, loop.THUMBS_DOWN, 2, prompt="Q2", out="Bad answer")
        lines = await _kto(a)
        assert {(x["prompt"], x["completion"], x["label"]) for x in lines} == {
            ("Q1", "Good answer", True),
            ("Q2", "Bad answer", False),
        }
        assert all(x["meta"]["organization_id"] == a.org for x in lines)
        assert all("scope" in x["meta"] for x in lines)

    async def test_it_needs_no_pairs(self, on, a):
        await _thumb(a, loop.THUMBS_UP, 1)
        assert (
            await export.build(organization_id=a.org, shape=export.PREFERENCE)
        ).lines == []
        assert len(await _kto(a)) == 1

    async def test_cards_and_evals_give_labelled_outputs(self, on, a):
        common = dict(
            organization_id=a.org, workflow_id=a.workflow, source=loop.ACTION_CARD
        )
        await loop_record.record(
            event_type=loop.APPROVED,
            subject_key="c:1",
            input_ref="e:1",
            input_text="P1",
            model_output="same",
            owner_final="same",
            **common,
        )
        await loop_record.record(
            event_type=loop.EDITED_THEN_APPROVED,
            subject_key="c:2",
            input_ref="e:2",
            input_text="P2",
            model_output="models words",
            owner_final="owners words",
            **common,
        )
        await loop_record.record(
            event_type=loop.REJECTED,
            subject_key="c:3",
            input_ref="e:3",
            input_text="P3",
            model_output="turned down",
            **common,
        )
        await loop_record.record(
            event_type=loop.EVAL_FAIL,
            subject_key="ev:1",
            input_ref="ev:1",
            input_text="P4",
            model_output="failed it",
            **{**common, "source": loop.EVAL},
        )
        got = {(x["prompt"], x["completion"], x["label"]) for x in await _kto(a)}
        assert got == {
            ("P1", "same", True),
            ("P2", "owners words", True),
            ("P2", "models words", False),
            ("P3", "turned down", False),
            ("P4", "failed it", False),
        }

    async def test_approved_then_undone_is_not_a_desirable_output(self, on, a):
        common = dict(
            organization_id=a.org,
            workflow_id=a.workflow,
            source=loop.ACTION_CARD,
            input_ref="e:1",
            input_text="P",
            model_output="out",
        )
        await loop_record.record(
            event_type=loop.APPROVED, subject_key="c:1", owner_final="out", **common
        )
        await loop_record.record(event_type=loop.UNDONE, subject_key="c:1u", **common)
        assert [(x["completion"], x["label"]) for x in await _kto(a)] == [
            ("out", False)
        ]

    async def test_a_thumb_without_its_prompt_makes_no_line(self, on, a):
        assert await loop_record.record(
            organization_id=a.org,
            event_type=loop.THUMBS_UP,
            source=loop.REPLY,
            subject_key="t",
            workflow_id=a.workflow,
            model_output="Answer with no question",
        )
        assert await _kto(a) == []

    async def test_routing_rows_are_not_training_text(self, on, a):
        await hooks.routing_decision(
            organization_id=a.org,
            workflow_id=a.workflow,
            feature="text_chat",
            ref="workflow_run:1",
            text="hello",
            attachments=0,
            routed=brain.Route(kind="quick", preset="everyday", source="rules"),
            candidates=brain.PRESET_FOR,
            mode="shadow",
            latency_ms=1,
        )
        for shape in export.SHAPES:
            assert (await export.build(organization_id=a.org, shape=shape)).lines == []

    async def test_archived_and_other_workspaces_rows_are_out(self, on, a, b):
        await _thumb(a, loop.THUMBS_UP, 1, prompt="A-Q", out="A-out")
        await _thumb(b, loop.THUMBS_UP, 1, prompt="B-Q", out="B-out")
        assert [x["prompt"] for x in await _kto(a)] == ["A-Q"]
        await consent.set_use_feedback(
            organization_id=a.org, user=a.user, allowed=False, delete_past=True
        )
        assert await _kto(a) == []
        assert [x["prompt"] for x in await _kto(b)] == ["B-Q"]

    async def test_over_http(self, on, a):
        await _thumb(a, loop.THUMBS_DOWN, 1, prompt="Q", out="meh")
        async with _as(a.as_user) as client:
            response = await client.get("/api/v1/training-loop/export?shape=kto")
        assert response.status_code == 200
        (line,) = [json.loads(x) for x in response.text.splitlines()]
        assert line["label"] is False and line["completion"] == "meh"

    async def test_the_row_table_is_untouched_by_reading(self, on, a, async_session):
        await _thumb(a, loop.THUMBS_UP, 1)
        before = len(await _rows(a.org))
        await _kto(a)
        assert len(await _rows(a.org)) == before
        total = (
            (await async_session.execute(select(LearningEventModel.id))).scalars().all()
        )
        assert total
