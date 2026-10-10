"""The training loop, step 1: what happened to each suggestion, kept per
workspace, redacted, consented, exportable.

DB integration tests on the transactional test session: real workspaces,
agents, cards and receipts. The things this has to get right, and so the
things tested:

* off by default, and off records nothing at all;
* "Use my feedback to improve my agents": on for the workspace by default;
  off, rows hold no words, and the words already kept are cleared;
* phone numbers, emails, Aadhaar and PAN are gone before anything is stored;
* every existing point reports in: edit card publish and discard, action card
  confirm / edit / decline / undo, thumbs, failed evals, escalations;
* one workspace's data never appears in another's counts or export;
* the weekly budget is the smaller of 2% and the ceiling.
"""

from __future__ import annotations

import copy
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api import constants
from api.db import db_client
from api.db.models import (
    AdminActionLogModel,
    AuditEntryModel,
    CallCostItemModel,
    EvalCaseModel,
    EvalResultModel,
    OrganizationModel,
)
from api.db.training_loop_models import LearningEventModel
from api.enums import AgentEventActor, AgentEventKind
from api.services import feedback
from api.services import training_loop as loop
from api.services.escalation import record as escalation_record
from api.services.training_loop import (
    budget,
    consent,
    export,
    hooks,
    redact,
    summary,
)
from api.services.training_loop import record as loop_record
from api.services.workflow import actions, agent_timeline, publish_gate, self_edit

START = "Greet the caller and ask how you can help."
GRAPH = {
    "nodes": [
        {
            "id": "1",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Start",
                "prompt": START,
                "greeting_type": "text",
                "greeting": "Namaste, City Dental.",
            },
        },
        {
            "id": "2",
            "type": "endCall",
            "position": {"x": 0, "y": 200},
            "data": {"name": "End", "prompt": "Bye"},
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source": "1",
            "target": "2",
            "data": {"label": "End", "condition": "The caller is done."},
        }
    ],
}
PROPOSED = "Greet the caller warmly, ask their name, then how you can help."


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(constants, "TRAINING_LOOP_ENABLED", True)


async def _workspace(db_session, async_session, tag: str, role: str = "owner"):
    org = OrganizationModel(provider_id=f"training-loop-org-{tag}")
    async_session.add(org)
    await async_session.flush()
    user, _ = await db_session.get_or_create_user_by_provider_id(
        f"training-loop-user-{tag}"
    )
    user.selected_organization_id = org.id
    user.email = f"{tag}@example.com"
    await db_session.add_user_to_organization(user.id, org.id, role=role)
    workflow = await db_session.create_workflow(
        name=f"Front desk {tag}",
        workflow_definition=copy.deepcopy(GRAPH),
        user_id=user.id,
        organization_id=org.id,
    )
    return SimpleNamespace(
        org=org.id,
        user=user,
        workflow=workflow.id,
        as_user=SimpleNamespace(
            id=user.id, selected_organization_id=org.id, email=user.email
        ),
    )


@pytest.fixture
async def a(db_session, async_session):
    return await _workspace(db_session, async_session, "a")


@pytest.fixture
async def b(db_session, async_session):
    return await _workspace(db_session, async_session, "b")


async def _rows(org: int, **where) -> list[LearningEventModel]:
    async with db_client.async_session() as session:
        query = select(LearningEventModel).where(
            LearningEventModel.organization_id == org
        )
        for column, value in where.items():
            query = query.where(getattr(LearningEventModel, column) == value)
        return list(
            (await session.execute(query.order_by(LearningEventModel.id)))
            .scalars()
            .all()
        )


async def _keep(ws, event_type, **fields):
    """One event written the way the hooks write it."""
    values = dict(
        organization_id=ws.org,
        workflow_id=ws.workflow,
        source=loop.EDIT_CARD,
        subject_key=f"k-{event_type}-{datetime.now(UTC).timestamp()}",
    )
    values.update(fields)
    assert await loop_record.record(event_type=event_type, **values)


# --- redaction ---------------------------------------------------------------


class TestRedaction:
    @pytest.mark.parametrize(
        "text,marker",
        [
            ("Call me on +91 98765 43210", redact.PHONE),
            ("or 9876543210 please", redact.PHONE),
            ("or 098765-43210", redact.PHONE),
            ("919876543210", redact.PHONE),
            ("landline 011-23456789", redact.PHONE),
            ("US +1 (415) 555-2671", redact.PHONE),
            ("mail ravi.k+shop@store.co.in now", redact.EMAIL),
            ("PAN ABCDE1234F", redact.PAN),
            ("pan abcde1234f typed small", redact.PAN),
            ("Aadhaar 2345 6789 0123", redact.AADHAAR),
            ("Aadhaar 2345-6789-0123", redact.AADHAAR),
            ("Aadhaar 234567890123", redact.AADHAAR),
        ],
    )
    def test_each_identifier_becomes_its_marker(self, text, marker):
        out = redact.redact(text)
        assert marker in out
        for digits in ("98765", "43210", "6789", "0123", "ABCDE", "abcde", "ravi"):
            assert digits not in out

    def test_ordinary_numbers_and_words_survive(self):
        text = "Pay Rs 1,00,000 on 2026-10-12 at 14:30, order 4521, 5 items."
        assert redact.redact(text) == text

    def test_nested_values_are_redacted(self):
        out = redact.redact_value({"a": ["x@y.com", {"b": "9876543210"}], "n": 7})
        assert out == {"a": [redact.EMAIL, {"b": redact.PHONE}], "n": 7}

    def test_text_is_capped(self):
        assert len(redact.redact("x" * 50_000)) == redact.MAX_TEXT_CHARS

    async def test_nothing_raw_reaches_the_table(self, on, db_session, a):
        await _keep(
            a,
            loop.APPROVED,
            input_text="Ring 9876543210 or mail a@b.com",
            model_output="PAN ABCDE1234F, Aadhaar 2345 6789 0123",
            owner_final="Call +91 98765 43210",
            detail="a@b.com",
        )
        (row,) = await _rows(a.org)
        stored = " ".join(
            [row.input_text, row.model_output, row.owner_final, row.detail]
        )
        for raw in ("9876543210", "a@b.com", "ABCDE1234F", "2345", "98765 43210"):
            assert raw not in stored
        assert row.consent_state == loop.GRANTED


# --- the flag and the consent --------------------------------------------------


class TestOffByDefault:
    async def test_the_flag_is_off_unless_switched_on(self):
        assert constants.TRAINING_LOOP_ENABLED is False
        assert loop.enabled() is False

    async def test_off_records_nothing(self, db_session, a):
        assert not await loop_record.record(
            organization_id=a.org,
            event_type=loop.APPROVED,
            source=loop.EDIT_CARD,
            subject_key="x",
            workflow_id=a.workflow,
            input_text="hello",
        )
        await hooks.edit_card_settled(
            organization_id=a.org,
            event_id=1,
            payload={"workflow_id": a.workflow, "step": "S", "old": "a", "new": "b"},
            action="publish",
            user_id=a.user.id,
        )
        assert await _rows(a.org) == []

    async def test_an_unknown_event_type_is_refused_and_logged(self, on, a):
        assert not await loop_record.record(
            organization_id=a.org,
            event_type="made_up",
            source=loop.EDIT_CARD,
            subject_key="x",
        )
        assert await _rows(a.org) == []

    async def test_the_same_event_twice_is_one_row(self, on, a):
        for _ in range(2):
            await loop_record.record(
                organization_id=a.org,
                event_type=loop.APPROVED,
                source=loop.EDIT_CARD,
                subject_key="same",
                workflow_id=a.workflow,
                input_text="p",
                owner_final="q",
            )
        assert len(await _rows(a.org)) == 1


class TestConsent:
    async def test_on_by_default_for_the_workspace(self, on, a):
        assert await consent.use_feedback(a.org) is True

    async def test_off_keeps_the_facts_and_no_words(self, on, a):
        await consent.set_use_feedback(
            organization_id=a.org, user=a.user, allowed=False
        )
        await loop_record.record(
            organization_id=a.org,
            event_type=loop.REJECTED,
            source=loop.EDIT_CARD,
            subject_key="r1",
            workflow_id=a.workflow,
            input_text="the prompt 9876543210",
            model_output="the answer",
            owner_final="the owner's",
            detail="reason",
            model="some-model",
            prompt_tokens=10,
            completion_tokens=5,
        )
        (row,) = await _rows(a.org)
        assert row.consent_state == loop.DECLINED
        assert (row.input_text, row.model_output, row.owner_final, row.detail) == (
            None,
            None,
            None,
            None,
        )
        # What happened is still true, and countable.
        assert (row.event_type, row.workflow_id, row.model) == (
            loop.REJECTED,
            a.workflow,
            "some-model",
        )
        assert (row.prompt_tokens, row.completion_tokens) == (10, 5)

    async def test_switching_off_clears_the_words_already_kept(self, on, a):
        await _keep(a, loop.APPROVED, input_text="p", owner_final="q")
        assert (await _rows(a.org))[0].input_text == "p"
        result = await consent.set_use_feedback(
            organization_id=a.org, user=a.user, allowed=False
        )
        assert result == {"use_feedback": False, "cleared": 1}
        (row,) = await _rows(a.org)
        assert (row.input_text, row.owner_final) == (None, None)
        assert row.consent_state == loop.DECLINED
        # ... and nothing of it is exportable.
        assert (await export.build(organization_id=a.org, shape=export.SFT)).lines == []

    async def test_switching_back_on_keeps_words_from_then_on(self, on, a):
        await consent.set_use_feedback(
            organization_id=a.org, user=a.user, allowed=False
        )
        await consent.set_use_feedback(organization_id=a.org, user=a.user, allowed=True)
        await _keep(a, loop.APPROVED, input_text="p", owner_final="q")
        assert (await _rows(a.org))[0].consent_state == loop.GRANTED

    async def test_one_workspace_switching_off_leaves_another_alone(self, on, a, b):
        await _keep(b, loop.APPROVED, input_text="p", owner_final="q")
        await consent.set_use_feedback(
            organization_id=a.org, user=a.user, allowed=False
        )
        assert await consent.use_feedback(b.org) is True
        assert (await _rows(b.org))[0].input_text == "p"

    async def test_the_change_is_in_the_audit_log(self, on, a, async_session):
        await consent.set_use_feedback(
            organization_id=a.org, user=a.user, allowed=False
        )
        rows = (
            (
                await async_session.execute(
                    select(AuditEntryModel).where(
                        AuditEntryModel.organization_id == a.org,
                        AuditEntryModel.action == "training_loop_consent",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].subject == "Use my feedback to improve my agents"


# --- the existing points ----------------------------------------------------------


def _screen():
    return patch.object(
        publish_gate.acceptable_use, "screen", AsyncMock(return_value=[])
    )


async def _card(a) -> int:
    result = await self_edit.propose(
        organization_id=a.org,
        workflow_id=a.workflow,
        workflow_run_id=None,
        arguments={"step": "Start", "new_prompt": PROPOSED, "why": "Be warmer"},
    )
    assert result["status"] == "proposed", result
    cards = list(
        await db_client.agent_events(
            organization_id=a.org,
            workflow_id=a.workflow,
            kinds=[AgentEventKind.EDIT_PROPOSED.value],
        )
    )
    return cards[-1].id


class TestEditCards:
    async def test_shown_then_published_is_approved(self, on, a):
        card = await _card(a)
        (shown,) = await _rows(a.org, event_type=loop.SUGGESTION_SHOWN)
        assert shown.source == loop.EDIT_CARD
        assert shown.workflow_id == a.workflow
        assert shown.input_ref == f"agent_event:{card}"
        assert START in shown.input_text and "Be warmer" in shown.input_text
        assert shown.model_output == PROPOSED

        with _screen():
            await self_edit.settle(
                organization_id=a.org,
                event_id=card,
                action="publish",
                user_id=a.user.id,
            )
        (approved,) = await _rows(a.org, event_type=loop.APPROVED)
        assert approved.owner_final == PROPOSED
        assert approved.user_id == a.user.id
        assert approved.subject_key == shown.subject_key

    async def test_discard_is_rejected(self, on, a):
        card = await _card(a)
        await self_edit.settle(
            organization_id=a.org, event_id=card, action="discard", user_id=a.user.id
        )
        (rejected,) = await _rows(a.org, event_type=loop.REJECTED)
        assert rejected.model_output == PROPOSED
        assert rejected.owner_final is None
        assert await _rows(a.org, event_type=loop.APPROVED) == []

    async def test_settling_twice_records_once(self, on, a):
        card = await _card(a)
        await self_edit.settle(
            organization_id=a.org, event_id=card, action="discard", user_id=a.user.id
        )
        with pytest.raises(self_edit.EditError):
            await self_edit.settle(
                organization_id=a.org,
                event_id=card,
                action="discard",
                user_id=a.user.id,
            )
        assert len(await _rows(a.org, event_type=loop.REJECTED)) == 1

    async def test_a_broken_recorder_does_not_break_the_card(self, on, a):
        card = await _card(a)
        with (
            patch.object(
                loop_record, "run_usage", AsyncMock(side_effect=RuntimeError("boom"))
            ),
            patch.object(
                db_client, "insert_learning_event", AsyncMock(side_effect=RuntimeError)
            ),
        ):
            payload = await self_edit.settle(
                organization_id=a.org,
                event_id=card,
                action="discard",
                user_id=a.user.id,
            )
        assert payload["decided"]["action"] == "discard"

    async def test_flag_off_the_card_still_works_and_nothing_is_kept(self, a):
        card = await _card(a)
        await self_edit.settle(
            organization_id=a.org, event_id=card, action="discard", user_id=a.user.id
        )
        assert await _rows(a.org) == []


def _run_tool_card(args: dict, **extra) -> dict:
    return {
        "action": actions.RUN_TOOL,
        "args": {"tool_name": "Send mail", "toolkit": "gmail", "arguments": args},
        "label": "Send mail to the supplier",
        "why": "They asked for the quote",
        "reversible": False,
        "state": actions.PROPOSED,
        **extra,
    }


async def _action_card(a, payload: dict) -> int:
    event_id = await agent_timeline.record(
        organization_id=a.org,
        kind=AgentEventKind.ACTION_PROPOSED.value,
        summary=payload["label"],
        workflow_id=a.workflow,
        payload=payload,
        actor=AgentEventActor.AGENT.value,
    )
    assert event_id is not None
    await hooks.action_card_shown(
        organization_id=a.org,
        event_id=event_id,
        workflow_id=a.workflow,
        workflow_run_id=None,
        payload=payload,
    )
    return event_id


@pytest.fixture
def armed(monkeypatch):
    """Confirm queues a job; the queue is not what is under test."""
    import api.tasks.arq as arq

    monkeypatch.setattr(arq, "enqueue_job", AsyncMock())


class TestActionCards:
    async def test_confirm_is_approved(self, on, a, armed):
        event_id = await _action_card(a, _run_tool_card({"body": "Quote please"}))
        await actions.settle(
            organization_id=a.org, event_id=event_id, verb="confirm", user_id=a.user.id
        )
        (row,) = await _rows(a.org, event_type=loop.APPROVED)
        assert row.source == loop.ACTION_CARD
        assert "Quote please" in row.owner_final
        assert row.subject_key == f"action_card:{event_id}"
        assert len(await _rows(a.org, event_type=loop.SUGGESTION_SHOWN)) == 1

    async def test_decline_is_rejected(self, on, a):
        event_id = await _action_card(a, _run_tool_card({"body": "Quote please"}))
        await actions.settle(
            organization_id=a.org, event_id=event_id, verb="decline", user_id=a.user.id
        )
        (row,) = await _rows(a.org, event_type=loop.REJECTED)
        assert "Quote please" in row.model_output

    async def test_undo_inside_the_window_is_undone(self, on, a, armed):
        event_id = await _action_card(a, _run_tool_card({"body": "Quote please"}))
        for verb in ("confirm", "undo"):
            await actions.settle(
                organization_id=a.org,
                event_id=event_id,
                verb=verb,
                user_id=a.user.id,
            )
        assert len(await _rows(a.org, event_type=loop.APPROVED)) == 1
        assert len(await _rows(a.org, event_type=loop.UNDONE)) == 1

    async def test_an_edited_card_keeps_the_models_version_and_the_owners(
        self, on, a, armed, monkeypatch
    ):
        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
        event_id = await _action_card(a, _run_tool_card({"body": "Quote please"}))
        revised = await actions.revise(
            organization_id=a.org,
            event_id=event_id,
            arguments={"body": "Quote for 40 units please"},
            user_id=a.user.id,
        )
        (correction,) = await _rows(a.org, event_type=loop.OWNER_CORRECTION)
        assert "Quote please" in correction.model_output
        assert "40 units" in correction.owner_final

        await actions.settle(
            organization_id=a.org,
            event_id=event_id,
            verb="confirm",
            user_id=a.user.id,
            version=revised["version"],
        )
        (row,) = await _rows(a.org, event_type=loop.EDITED_THEN_APPROVED)
        assert "Quote please" in row.model_output
        assert "40 units" not in row.model_output
        assert "40 units" in row.owner_final
        assert await _rows(a.org, event_type=loop.APPROVED) == []

    async def test_a_second_revision_still_points_at_the_models_original(
        self, on, a, monkeypatch
    ):
        monkeypatch.setattr(constants, "TASK_LEDGER_ENABLED", True)
        event_id = await _action_card(a, _run_tool_card({"body": "first"}))
        for body in ("second", "third"):
            await actions.revise(
                organization_id=a.org,
                event_id=event_id,
                arguments={"body": body},
                user_id=a.user.id,
            )
        corrections = await _rows(a.org, event_type=loop.OWNER_CORRECTION)
        assert len(corrections) == 2
        assert all("first" in c.model_output for c in corrections)
        assert "third" in corrections[-1].owner_final

    @pytest.mark.parametrize("key", ["private_to", "only_user_id", "owner_user_id"])
    async def test_a_persons_own_card_is_never_recorded(self, on, a, key):
        payload = _run_tool_card({"body": "secret"}, **{key: a.user.id})
        event_id = await _action_card(a, payload)
        await actions.settle(
            organization_id=a.org, event_id=event_id, verb="decline", user_id=a.user.id
        )
        assert await _rows(a.org) == []

    async def test_a_kind_of_card_that_is_not_listed_is_not_recorded(self, on, a):
        payload = _run_tool_card({"body": "x"}, action=actions.FORGET_FACT)
        await _action_card(a, payload)
        assert await _rows(a.org) == []

    def test_every_kind_of_card_has_been_decided_on(self):
        """A new action card is recorded, or deliberately not, and somebody
        said which. This fails until they do."""
        constants_ = {
            value
            for name, value in vars(actions).items()
            if name.isupper()
            and isinstance(value, str)
            and value
            in {
                *actions.ACTIONS,
                *actions.INTERNAL_ACTIONS,
            }
        }
        decided = hooks.RECORDED_ACTIONS | hooks.EXCLUDED_ACTIONS
        assert constants_ <= decided, sorted(constants_ - decided)
        assert not hooks.RECORDED_ACTIONS & hooks.EXCLUDED_ACTIONS
        # And every name in the lists is a real kind of card.
        real = set(actions.ACTIONS) | set(actions.INTERNAL_ACTIONS)
        assert decided <= real, sorted(decided - real)


class TestThumbs:
    async def _reply(self, a, *, workflow_id):
        return await db_client.record_agent_event(
            organization_id=a.org,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.AGENT.value,
            summary="Your slot is at 5 pm. Call 9876543210 to change it.",
            workflow_id=workflow_id,
            payload={
                "body": "Your slot is at 5 pm. Call 9876543210 to change it.",
                "model": "some-model",
            },
        )

    async def test_yes_and_not_quite(self, on, a, monkeypatch):
        monkeypatch.setattr(constants, "REPLY_FEEDBACK_ENABLED", True)
        reply = await self._reply(a, workflow_id=a.workflow)
        reply_id = getattr(reply, "id", reply)
        await feedback.submit(
            organization_id=a.org,
            user_id=a.user.id,
            subject_kind="reply",
            subject_id=reply_id,
            verdict="not_quite",
            reasons=["too_long", "wrong_language"],
        )
        (row,) = await _rows(a.org, event_type=loop.THUMBS_DOWN)
        assert row.workflow_id == a.workflow
        assert row.detail == "too_long,wrong_language"
        assert row.model == "some-model"
        assert "9876543210" not in row.model_output
        assert "5 pm" in row.model_output

        await feedback.submit(
            organization_id=a.org,
            user_id=a.user.id,
            subject_kind="reply",
            subject_id=reply_id,
            verdict="yes",
        )
        assert len(await _rows(a.org, event_type=loop.THUMBS_UP)) == 1

    async def test_decibyls_own_thread_is_not_an_agent_and_is_not_kept(
        self, on, a, monkeypatch
    ):
        monkeypatch.setattr(constants, "REPLY_FEEDBACK_ENABLED", True)
        reply = await self._reply(a, workflow_id=None)
        await feedback.submit(
            organization_id=a.org,
            user_id=a.user.id,
            subject_kind="reply",
            subject_id=getattr(reply, "id", reply),
            verdict="yes",
        )
        assert await _rows(a.org) == []


class TestEvalsAndEscalations:
    async def _failed_eval(self, a, async_session, status="failed"):
        case = EvalCaseModel(
            organization_id=a.org,
            workflow_id=a.workflow,
            name="Refund",
            persona="An angry customer, phone 9876543210",
            goal="Get a refund",
            must_say=["refund policy"],
            must_not_say=["promise cash"],
            created_by=a.user.id,
        )
        async_session.add(case)
        await async_session.flush()
        result = EvalResultModel(
            case_id=case.id,
            organization_id=a.org,
            workflow_id=a.workflow,
            status=status,
            verdict="It promised cash.",
            transcript=[
                {"role": "agent", "text": "Hello, City Dental."},
                {"role": "caller", "text": "I want my money."},
                {"role": "agent", "text": "Sure, cash tomorrow."},
            ],
        )
        async_session.add(result)
        await async_session.flush()
        return case, result

    async def test_a_failed_case_is_an_event(self, on, a, async_session):
        case, result = await self._failed_eval(a, async_session)
        await hooks.eval_finished(result.id)
        (row,) = await _rows(a.org, event_type=loop.EVAL_FAIL)
        assert row.source == loop.EVAL
        assert row.workflow_id == a.workflow
        assert "Get a refund" in row.input_text
        assert "9876543210" not in row.input_text
        assert "Sure, cash tomorrow." in row.model_output
        assert "I want my money" not in row.model_output
        assert row.detail == "It promised cash."
        assert row.group_key == f"eval_case:{case.id}"

    async def test_a_pass_is_not_an_event(self, on, a, async_session):
        _, result = await self._failed_eval(a, async_session, status="passed")
        await hooks.eval_finished(result.id)
        assert await _rows(a.org) == []

    async def test_finishing_a_case_goes_through_the_hook(self, on, a, async_session):
        from api.services.evals import runner

        _, result = await self._failed_eval(a, async_session, status="running")
        await runner._finish(
            result.id,
            status="failed",
            verdict="Said the wrong thing.",
            transcript=[{"role": "agent", "text": "Hi"}],
            run_id=None,
        )
        assert len(await _rows(a.org, event_type=loop.EVAL_FAIL)) == 1

    async def test_an_escalation_is_an_event_once(self, on, a):
        for _ in range(2):
            await escalation_record.open_escalation(
                organization_id=a.org,
                workflow_id=a.workflow,
                workflow_run_id=None,
                sequence=1,
                reason="caller_asked",
                detail="Wants a person, number 9876543210",
            )
        (row,) = await _rows(a.org, event_type=loop.ESCALATION)
        assert row.detail == "caller_asked"
        assert "9876543210" not in row.input_text
        assert row.workflow_id == a.workflow


# --- the export -----------------------------------------------------------------------


async def _decided(
    ws,
    ref,
    *,
    prompt,
    shown,
    final=None,
    how="approved",
    group="g1",
    edited=False,
):
    """A card's whole life, the way the hooks leave it."""
    common = dict(
        workflow_id=ws.workflow,
        source=loop.ACTION_CARD,
        input_ref=f"agent_event:{ref}",
        group_key=group,
        input_text=prompt,
    )
    await loop_record.record(
        organization_id=ws.org,
        event_type=loop.SUGGESTION_SHOWN,
        subject_key=f"c:{ref}",
        model_output=shown,
        **common,
    )
    kind = {
        "approved": loop.EDITED_THEN_APPROVED if edited else loop.APPROVED,
        "rejected": loop.REJECTED,
        "undone": loop.UNDONE,
    }[how]
    await loop_record.record(
        organization_id=ws.org,
        event_type=kind,
        subject_key=f"c:{ref}",
        model_output=shown,
        owner_final=final if how == "approved" else None,
        **common,
    )


class TestExport:
    async def test_sft_is_the_owners_final_version(self, on, a):
        await _decided(a, 1, prompt="P1", shown="model one", final="model one")
        await _decided(
            a, 2, prompt="P2", shown="model two", final="owner two", edited=True
        )
        await _decided(a, 3, prompt="P3", shown="turned down", how="rejected")
        result = await export.build(organization_id=a.org, shape=export.SFT)
        lines = [json.loads(line) for line in result.lines]
        assert {(x["prompt"], x["completion"]) for x in lines} == {
            ("P1", "model one"),
            ("P2", "owner two"),
        }
        assert all(x["meta"]["organization_id"] == a.org for x in lines)
        assert result.truncated is False

    async def test_preference_pairs_an_edit_with_the_models_original(self, on, a):
        await _decided(
            a, 1, prompt="P", shown="model said", final="owner said", edited=True
        )
        result = await export.build(organization_id=a.org, shape=export.PREFERENCE)
        (pair,) = [json.loads(line) for line in result.lines]
        assert pair["prompt"] == "P"
        assert pair["chosen"] == "owner said"
        assert pair["rejected"] == "model said"

    async def test_preference_pairs_an_approval_with_an_earlier_rejection(self, on, a):
        await _decided(a, 1, prompt="P", shown="first try", how="rejected")
        await _decided(a, 2, prompt="P", shown="second try", final="second try")
        result = await export.build(organization_id=a.org, shape=export.PREFERENCE)
        (pair,) = [json.loads(line) for line in result.lines]
        assert (pair["chosen"], pair["rejected"]) == ("second try", "first try")

    async def test_an_approval_with_nothing_to_set_against_makes_no_pair(self, on, a):
        await _decided(a, 1, prompt="P", shown="only try", final="only try")
        assert (
            await export.build(organization_id=a.org, shape=export.PREFERENCE)
        ).lines == []

    async def test_a_rejection_is_used_for_one_pair_only(self, on, a):
        await _decided(a, 1, prompt="P", shown="bad", how="rejected")
        await _decided(a, 2, prompt="P", shown="good one", final="good one")
        await _decided(a, 3, prompt="P", shown="good two", final="good two")
        pairs = (
            await export.build(organization_id=a.org, shape=export.PREFERENCE)
        ).lines
        assert len(pairs) == 1

    async def test_a_different_agents_rejection_is_not_a_pair(self, on, a):
        await _decided(a, 1, prompt="P", shown="bad", how="rejected", group="other")
        await _decided(a, 2, prompt="P", shown="good", final="good")
        assert (
            await export.build(organization_id=a.org, shape=export.PREFERENCE)
        ).lines == []

    async def test_approved_then_undone_is_not_an_example(self, on, a):
        await _decided(a, 1, prompt="P", shown="x", final="x")
        await loop_record.record(
            organization_id=a.org,
            event_type=loop.UNDONE,
            source=loop.ACTION_CARD,
            subject_key="c:1",
            workflow_id=a.workflow,
            input_ref="agent_event:1",
            input_text="P",
            model_output="x",
        )
        assert (await export.build(organization_id=a.org, shape=export.SFT)).lines == []

    async def test_an_agent_filter(self, on, a, db_session):
        other = await db_session.create_workflow(
            name="Second",
            workflow_definition=copy.deepcopy(GRAPH),
            user_id=a.user.id,
            organization_id=a.org,
        )
        await _decided(a, 1, prompt="P1", shown="x", final="x")
        await loop_record.record(
            organization_id=a.org,
            event_type=loop.APPROVED,
            source=loop.ACTION_CARD,
            subject_key="second",
            workflow_id=other.id,
            input_ref="agent_event:9",
            input_text="P9",
            owner_final="y",
        )
        only = await export.build(
            organization_id=a.org, shape=export.SFT, workflow_id=other.id
        )
        assert [json.loads(line)["prompt"] for line in only.lines] == ["P9"]

    async def test_it_says_when_it_stopped_short(self, on, a):
        for n in range(3):
            await _decided(a, n, prompt=f"P{n}", shown="x", final="x")
        result = await export.build(organization_id=a.org, shape=export.SFT, limit=2)
        assert result.truncated is True

    async def test_an_unknown_shape_is_refused(self, a):
        with pytest.raises(ValueError):
            await export.build(organization_id=a.org, shape="csv")


class TestOneWorkspaceNeverSeesAnothers:
    async def test_exports_hold_only_their_own_rows(self, on, a, b):
        await _decided(a, 1, prompt="A-prompt", shown="A-out", final="A-final")
        await _decided(b, 1, prompt="B-prompt", shown="B-out", final="B-final")
        await _decided(a, 2, prompt="A-p2", shown="A-bad", how="rejected", group="g")
        await _decided(b, 2, prompt="B-p2", shown="B-bad", how="rejected", group="g")
        await _decided(a, 3, prompt="A-p3", shown="A-good", final="A-good", group="g")
        for shape in export.SHAPES:
            mine = await export.build(organization_id=a.org, shape=shape)
            theirs = await export.build(organization_id=b.org, shape=shape)
            assert mine.lines and theirs.lines
            assert not any("B-" in line for line in mine.lines), shape
            assert not any("A-" in line for line in theirs.lines), shape
            assert all(
                json.loads(line)["meta"]["organization_id"] == a.org
                for line in mine.lines
            )

    async def test_a_rejection_in_one_workspace_is_never_a_pairs_other_half(
        self, on, a, b
    ):
        await _decided(b, 1, prompt="P", shown="B-bad", how="rejected", group="g")
        await _decided(a, 2, prompt="P", shown="A-good", final="A-good", group="g")
        assert (
            await export.build(organization_id=a.org, shape=export.PREFERENCE)
        ).lines == []

    async def test_counts_are_per_workspace(self, on, a, b):
        await _decided(a, 1, prompt="P", shown="x", final="x")
        await _decided(b, 1, prompt="P", shown="x", final="x")
        await _decided(b, 2, prompt="P", shown="x", how="rejected")
        mine = await summary.agent_summary(
            organization_id=a.org, workflow_id=a.workflow
        )
        assert (mine["approved_this_week"], mine["rejected_this_week"]) == (1, 0)
        # An agent read through the wrong workspace has nothing.
        wrong = await summary.agent_summary(
            organization_id=a.org, workflow_id=b.workflow
        )
        assert (wrong["approved_this_week"], wrong["rejected_this_week"]) == (0, 0)

    async def test_switching_off_clears_only_that_workspace(self, on, a, b):
        await _decided(a, 1, prompt="P", shown="x", final="x")
        await _decided(b, 1, prompt="P", shown="x", final="x")
        await consent.set_use_feedback(
            organization_id=a.org, user=a.user, allowed=False
        )
        assert (await export.build(organization_id=a.org, shape=export.SFT)).lines == []
        assert (
            len((await export.build(organization_id=b.org, shape=export.SFT)).lines)
            == 1
        )

    async def test_the_data_layer_has_no_cross_workspace_read(self):
        """Every training-loop read takes an organization_id by name."""
        import inspect

        from api.db.training_loop_client import TrainingLoopClient

        for name, fn in inspect.getmembers(TrainingLoopClient, inspect.isfunction):
            if name.startswith("_") or name in ("async_session",):
                continue
            if "learning_event" in name or "model_spend" in name:
                assert "organization_id" in inspect.signature(fn).parameters or (
                    name == "insert_learning_event"
                ), name


# --- the counts and the week ------------------------------------------------------------------


class TestSummary:
    def test_the_week_starts_on_monday_in_india(self):
        wednesday = datetime(2026, 10, 14, 10, 0, tzinfo=UTC)
        assert summary.week_start(wednesday) == datetime(
            2026, 10, 11, 18, 30, tzinfo=UTC
        )
        sunday_night_utc = datetime(2026, 10, 11, 20, 0, tzinfo=UTC)  # Monday in IST
        assert summary.week_start(sunday_night_utc) == datetime(
            2026, 10, 11, 18, 30, tzinfo=UTC
        )

    async def test_this_week_only_and_edited_counts_as_approved(self, on, a):
        await _decided(a, 1, prompt="P", shown="x", final="x")
        await _decided(a, 2, prompt="P", shown="x", final="y", edited=True)
        await _decided(a, 3, prompt="P", shown="x", how="rejected")
        last_week = datetime.now(UTC) - timedelta(days=9)
        await db_client.insert_learning_event(
            {
                "organization_id": a.org,
                "workflow_id": a.workflow,
                "event_type": loop.APPROVED,
                "source": loop.EDIT_CARD,
                "subject_key": "old",
                "consent_state": loop.GRANTED,
                "created_at": last_week,
            }
        )
        got = await summary.agent_summary(organization_id=a.org, workflow_id=a.workflow)
        assert got == {
            "approved_this_week": 2,
            "rejected_this_week": 1,
            "use_feedback": True,
        }


# --- the budget --------------------------------------------------------------------------------


class TestBudget:
    def test_the_smaller_of_two_percent_and_the_ceiling(self):
        ceiling = budget.WEEKLY_LOOP_CEILING_PAISE
        assert budget.weekly_cap_paise(10_000) == 200  # 2% of Rs 100
        assert budget.weekly_cap_paise(ceiling * 50) == ceiling  # 2% == ceiling
        assert budget.weekly_cap_paise(ceiling * 50 + 100) == ceiling  # 2% > ceiling
        assert budget.weekly_cap_paise(ceiling * 49) == ceiling * 49 * 2 // 100

    def test_nothing_spent_is_nothing_to_spend(self):
        assert budget.weekly_cap_paise(0) == 0
        assert budget.weekly_cap_paise(None) == 0
        assert budget.weekly_cap_paise(-500) == 0

    def test_it_rounds_down(self):
        assert budget.weekly_cap_paise(149) == 2
        assert budget.weekly_cap_paise(49) == 0

    def test_the_ceiling_is_a_constant_in_rupees(self):
        assert budget.WEEKLY_LOOP_CEILING_PAISE == (
            budget.WEEKLY_LOOP_CEILING_RUPEES * budget.PAISE_PER_RUPEE
        )
        assert budget.SPEND_SHARE_PERCENT == 2
        assert budget.SPEND_WINDOW_DAYS == 28

    async def _spend(self, ws, async_session, db_session, lines, *, days_ago=1):
        run = await db_session.create_workflow_run(
            name="r",
            workflow_id=ws.workflow,
            mode="textchat",
            user_id=ws.user.id,
            organization_id=ws.org,
        )
        run.created_at = datetime.now(UTC) - timedelta(days=days_ago)
        for component, paise in lines:
            async_session.add(
                CallCostItemModel(
                    workflow_run_id=run.id,
                    component=component,
                    units=1,
                    unit_rate_mpaise=0,
                    cost_paise=paise * 2,
                    provider_cost_paise=paise,
                )
            )
        await async_session.flush()

    async def test_it_is_computed_from_the_agents_own_model_receipts(
        self, a, b, async_session, db_session
    ):
        await self._spend(
            a,
            async_session,
            db_session,
            [("llm_input", 6000), ("llm_output", 3000), ("llm", 1000), ("stt", 99999)],
        )
        # Older than four weeks: not counted.
        await self._spend(
            a, async_session, db_session, [("llm_input", 50_000)], days_ago=40
        )
        # Another workspace's agent: not counted.
        await self._spend(b, async_session, db_session, [("llm_input", 70_000)])
        assert (
            await budget.weekly_cap_for_agent(
                organization_id=a.org, workflow_id=a.workflow
            )
            == 200
        )  # 2% of 10,000 paise: the vendor price, model lines only
        assert (
            await budget.weekly_cap_for_agent(
                organization_id=b.org, workflow_id=b.workflow
            )
            == 1400
        )
        # Asked through the wrong workspace, an agent has no spend.
        assert (
            await budget.weekly_cap_for_agent(
                organization_id=a.org, workflow_id=b.workflow
            )
            == 0
        )

    async def test_no_loop_runs_yet(self):
        """This step only computes the number: nothing in the package starts a
        job or changes an agent."""
        import pathlib

        package = pathlib.Path(loop.__file__).parent
        source = "".join(p.read_text() for p in package.glob("*.py"))
        for forbidden in ("enqueue_job", "publish_definition", "save_workflow_draft"):
            assert forbidden not in source, forbidden


# --- the routes ----------------------------------------------------------------------------------


@asynccontextmanager
async def _as(user, *, staff=None):
    from api.app import app
    from api.services.auth.depends import get_superuser, get_user

    app.dependency_overrides[get_user] = lambda: user
    if staff is not None:
        app.dependency_overrides[get_superuser] = lambda: staff
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_user, None)
        app.dependency_overrides.pop(get_superuser, None)


class TestRoutes:
    async def test_the_routes_are_not_there_while_the_flag_is_off(self, a):
        async with _as(a.as_user) as client:
            for method, path in (
                ("GET", "/api/v1/training-loop/settings"),
                ("GET", f"/api/v1/training-loop/agents/{a.workflow}/summary"),
                ("GET", "/api/v1/training-loop/export"),
            ):
                response = await client.request(method, path)
                assert response.status_code == 404, path

    async def test_settings_are_read_by_a_member_and_changed_by_an_admin(
        self, on, db_session, async_session
    ):
        ws = await _workspace(db_session, async_session, "m1", role="member")
        async with _as(ws.as_user) as client:
            got = await client.get("/api/v1/training-loop/settings")
            assert got.json() == {"use_feedback": True}
            denied = await client.put(
                "/api/v1/training-loop/settings", json={"use_feedback": False}
            )
            assert denied.status_code == 403
        assert await consent.use_feedback(ws.org) is True

        admin = await _workspace(db_session, async_session, "ad1", role="admin")
        async with _as(admin.as_user) as client:
            changed = await client.put(
                "/api/v1/training-loop/settings", json={"use_feedback": False}
            )
            assert changed.status_code == 200
            assert changed.json() == {"use_feedback": False}
        assert await consent.use_feedback(admin.org) is False

    async def test_the_summary_for_the_agent_page(self, on, a, b):
        await _decided(a, 1, prompt="P", shown="x", final="x")
        await _decided(a, 2, prompt="P", shown="x", how="rejected")
        async with _as(a.as_user) as client:
            got = await client.get(f"/api/v1/training-loop/agents/{a.workflow}/summary")
            assert got.status_code == 200
            assert got.json() == {
                "approved_this_week": 1,
                "rejected_this_week": 1,
                "use_feedback": True,
            }
            # Another workspace's agent is not found, whatever it holds.
            other = await client.get(
                f"/api/v1/training-loop/agents/{b.workflow}/summary"
            )
            assert other.status_code == 404

    async def test_the_owner_exports_their_own_workspace_as_jsonl(self, on, a, b):
        await _decided(a, 1, prompt="A-prompt", shown="A-out", final="A-final")
        await _decided(b, 1, prompt="B-prompt", shown="B-out", final="B-final")
        async with _as(a.as_user) as client:
            response = await client.get("/api/v1/training-loop/export?shape=sft")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["x-export-truncated"] == "false"
        lines = [json.loads(x) for x in response.text.splitlines()]
        assert [x["completion"] for x in lines] == ["A-final"]
        assert "B-" not in response.text

    async def test_preference_shape_over_http(self, on, a):
        await _decided(a, 1, prompt="P", shown="m", final="o", edited=True)
        async with _as(a.as_user) as client:
            response = await client.get("/api/v1/training-loop/export?shape=preference")
        (pair,) = [json.loads(x) for x in response.text.splitlines()]
        assert (pair["chosen"], pair["rejected"]) == ("o", "m")

    async def test_only_an_owner_exports_and_the_shape_and_agent_are_checked(
        self, on, a, b, db_session, async_session
    ):
        admin = await _workspace(db_session, async_session, "ad2", role="admin")
        async with _as(admin.as_user) as client:
            assert (await client.get("/api/v1/training-loop/export")).status_code == 403
        async with _as(a.as_user) as client:
            assert (
                await client.get("/api/v1/training-loop/export?shape=csv")
            ).status_code == 422
            assert (
                await client.get(f"/api/v1/training-loop/export?agent_id={b.workflow}")
            ).status_code == 404

    async def test_staff_export_is_one_named_workspace_and_is_audited(
        self, on, a, b, db_session, async_session
    ):
        await _decided(a, 1, prompt="A-prompt", shown="A-out", final="A-final")
        await _decided(b, 1, prompt="B-prompt", shown="B-out", final="B-final")
        staff, _ = await db_session.get_or_create_user_by_provider_id("tl-staff")
        async with _as(a.as_user, staff=staff) as client:
            response = await client.get(
                f"/api/v1/admin/training-loop/{b.org}/export?shape=sft"
            )
            missing = await client.get("/api/v1/admin/training-loop/99999999/export")
        assert response.status_code == 200
        assert "B-final" in response.text and "A-" not in response.text
        assert missing.status_code == 404
        staff_log = (
            (
                await async_session.execute(
                    select(AdminActionLogModel).where(
                        AdminActionLogModel.action == "training_data_export"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [(r.actor_user_id, r.target_organization_id) for r in staff_log] == [
            (staff.id, b.org)
        ]
        workspace_log = (
            (
                await async_session.execute(
                    select(AuditEntryModel).where(
                        AuditEntryModel.organization_id == b.org,
                        AuditEntryModel.action == "training_data_export",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(workspace_log) == 1

    async def test_staff_export_is_not_reachable_without_staff(self, on, a):
        async with _as(a.as_user) as client:
            response = await client.get(
                f"/api/v1/admin/training-loop/{a.org}/export",
                headers={"Authorization": "Bearer not-a-real-token"},
            )
        assert response.status_code in (401, 403)

    async def test_the_staff_route_is_staff_only_and_off_the_public_spec(self, a):
        from api.app import app

        route = next(
            r
            for r in app.routes
            if getattr(r, "path", "").endswith(
                "/admin/training-loop/{organization_id}/export"
            )
        )
        from api.services.auth.depends import get_superuser

        assert any(d.call is get_superuser for d in route.dependant.dependencies)
        assert "admin-training-loop" in route.tags
