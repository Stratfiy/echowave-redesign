"""Escalation v2 on a simulated call: the record, the card, the ladder on a
fake carrier, the fallbacks, hand back to AI, the routes and the flag.

No real calls. The carrier is a fake provider that reports each person's leg
the way Plivo's callbacks do (through ``report_leg_outcome`` and Redis), and
the engine is a fake that records what it was asked to say and do.

Done when: an explicit request rings people in order and bridges the first
person (never a machine); nobody answering brings the agent back with an
honest line and the callback, ticket and voicemail rungs in order; outside
transfer hours nobody is rung and a callback is offered; duplicate events
make one escalation and one dial; a caller who hangs up on hold leaves a
callback task; hand back to AI moves the caller and the new run opens with
the person's note; every read is scoped to the organization; and with the
flag off nothing is offered and every route is a 404.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import LLMMessagesAppendFrame, TTSSpeakFrame
from pipecat.utils.enums import EndTaskReason
from sqlalchemy import select, text

from api import constants
from api.db import db_client
from api.db.escalation_models import EscalationModel
from api.db.models import AgentEventModel, AgentTaskModel
from api.enums import CallType
from api.services.escalation import actions, record
from api.services.escalation import runtime as runtime_module
from api.services.escalation.dialer import report_leg_outcome
from api.services.escalation.policy import EscalationPolicy
from api.services.escalation.runtime import EscalationRuntime
from api.tests.support.voice import client_as, make_people

FIRST, SECOND, THIRD = "+919800000001", "+919800000002", "+919800000003"
CALLER = "+919876543210"


# --- fakes --------------------------------------------------------------------


class FakeTask:
    def __init__(self):
        self.frames: list = []

    async def queue_frame(self, frame):
        self.frames.append(frame)


class FakeEngine:
    def __init__(self):
        self.task = FakeTask()
        self.muted: list[bool] = []
        self._queued_speech_mute_state = "idle"
        self._transport_output = None
        self._audio_config = None
        self._bot_is_speaking = False
        self._call_context_vars = {"caller_number": CALLER}
        self._gathered_context = {"call_id": "CALL-1", "customer_name": "Asha"}
        self.context = SimpleNamespace(
            get_messages=lambda: [
                {"role": "user", "content": "My order 1234 never came."},
                {"role": "user", "content": "Let me talk to a human."},
            ]
        )
        self.inference_llm = None
        self.workflow = SimpleNamespace(start_node_id="start")
        self._call_recorded = True
        self.ended: list[str] = []

    def set_mute_pipeline(self, mute: bool) -> None:
        self.muted.append(mute)

    def resolve_recording_disclosure(self, node_id):
        return "This call is recorded."

    def resolve_ai_disclosure(self, node_id):
        return "You are speaking with an AI assistant."

    async def end_call_with_reason(self, reason, abort_immediately=False):
        self.ended.append(reason)

    def spoken(self) -> list[str]:
        return [f.text for f in self.task.frames if isinstance(f, TTSSpeakFrame)]

    def notes(self) -> list[LLMMessagesAppendFrame]:
        return [f for f in self.task.frames if isinstance(f, LLMMessagesAppendFrame)]


_REASONS = {
    "machine": "answered_by_machine",
    "no_answer": "no_answer",
    "busy": "busy",
}


class FakeProvider:
    PROVIDER_NAME = "plivo"

    def __init__(self, outcomes: dict[str, str] | None = None):
        self.outcomes = outcomes or {}
        self.dialled: list[tuple[str, str, dict]] = []
        self.hung: list[str] = []
        self.handed_back: list[dict] = []
        self.from_numbers = ["+918000000000"]

    def supports_transfers(self):
        return True

    def validate_config(self):
        return True

    def escalation_dial_options(self, *, transfer_id, backend_endpoint):
        return {"machine_detection": "true"}

    async def transfer_call(
        self, destination, transfer_id, conference_name, timeout=30, briefing=None, **kw
    ):
        self.dialled.append((destination, briefing, kw))
        outcome = self.outcomes.get(destination)
        if outcome:
            asyncio.create_task(self._report(transfer_id, outcome))
        return {"call_sid": f"leg-{len(self.dialled)}"}

    async def _report(self, transfer_id, outcome):
        await asyncio.sleep(0.05)
        await report_leg_outcome(
            transfer_id,
            human=outcome == "human",
            reason=_REASONS.get(outcome),
            call_id="human-leg",
        )

    async def hangup_transfer_leg(self, call_id):
        self.hung.append(call_id)
        return True

    def supports_escalation_hand_back(self):
        return True

    async def hand_back_to_ai(self, *, caller_call_id, human_call_id, resume_url):
        self.handed_back.append(
            {"caller": caller_call_id, "human": human_call_id, "url": resume_url}
        )
        return True

    async def start_inbound_stream(
        self, *, websocket_url, workflow_run_id, normalized_data, backend_endpoint
    ):
        return {"stream": websocket_url, "workflow_run_id": workflow_run_id}


# --- fixtures -------------------------------------------------------------------


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(constants, "ESCALATION_V2_ENABLED", True)


@pytest.fixture
def quick(monkeypatch):
    """Speech takes no time, and the backend has an address."""
    monkeypatch.setattr(runtime_module, "_estimate_speech_seconds", lambda _t: 0.0)

    async def endpoints():
        return "https://api.example.test", "wss://api.example.test"

    import api.services.escalation.dialer as dialer_module
    import api.utils.common as common

    monkeypatch.setattr(dialer_module, "get_backend_endpoints", endpoints)
    monkeypatch.setattr(common, "get_backend_endpoints", endpoints)


@pytest.fixture
async def people(test_engine):
    p = await make_people("esc")
    yield p
    async with db_client.async_session() as session:
        for org in (p.org, p.other):
            for table in (
                "call_escalation_outcomes",
                "escalations",
                "agent_tasks",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE organization_id = :o"), {"o": org}
                )
        await session.commit()


@pytest.fixture
async def call(people):
    workflow = await db_client.create_workflow(
        "Receptionist",
        {"nodes": [], "edges": []},
        people.a.id,
        organization_id=people.org,
    )
    run = await db_client.create_workflow_run(
        "WR-ESC-1",
        workflow.id,
        "plivo",
        user_id=people.a.id,
        call_type=CallType.INBOUND,
        initial_context={"caller_number": CALLER},
        gathered_context={"call_id": "CALL-1"},
        organization_id=people.org,
    )
    return SimpleNamespace(workflow=workflow, run=run, people=people)


def _provider(monkeypatch, provider: FakeProvider, run=None):
    import api.services.telephony.factory as factory
    import api.services.telephony.webhook_guard as guard

    async def for_run(_run, _org):
        return provider

    async def for_run_id(_run_id):
        return provider, run

    monkeypatch.setattr(factory, "get_telephony_provider_for_run", for_run)
    monkeypatch.setattr(guard, "provider_for_run_id", for_run_id)


def _runtime(call, engine=None, **policy) -> EscalationRuntime:
    policy.setdefault(
        "transfer_numbers",
        [{"number": FIRST, "name": "Priya"}, {"number": SECOND}, {"number": THIRD}],
    )
    return EscalationRuntime(
        engine=engine or FakeEngine(),
        policy=EscalationPolicy(**policy),
        organization_id=call.people.org,
        workflow_id=call.workflow.id,
        workflow_run=call.run,
        owner_user_id=call.people.a.id,
        language="en-IN",
    )


async def _rows(org: int, run_id: int) -> list[EscalationModel]:
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(EscalationModel).where(
                        EscalationModel.organization_id == org,
                        EscalationModel.workflow_run_id == run_id,
                    )
                )
            ).scalars()
        )


async def _outcome(org: int, run_id: int):
    return await db_client.get_call_escalation_outcome(run_id, organization_id=org)


async def _tasks(org: int) -> list[AgentTaskModel]:
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(AgentTaskModel).where(AgentTaskModel.organization_id == org)
                )
            ).scalars()
        )


async def _bridged(call, monkeypatch) -> tuple[EscalationRuntime, FakeProvider]:
    provider = FakeProvider({FIRST: "human"})
    _provider(monkeypatch, provider, call.run)
    runtime = _runtime(call)
    runtime.on_user_text("Let me talk to a human")
    await runtime._task
    return runtime, provider


# --- the ladder on a call --------------------------------------------------------


@pytest.mark.asyncio
async def test_an_explicit_request_rings_in_order_and_bridges_a_person(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "no_answer", SECOND: "machine", THIRD: "human"})
    _provider(monkeypatch, provider)
    engine = FakeEngine()
    runtime = _runtime(call, engine)

    ahead = runtime.on_user_text("Let me talk to a human please")
    assert ahead == [] and engine.muted[0] is True  # muted before the model sees it
    await runtime._task

    assert [d[0] for d in provider.dialled] == [FIRST, SECOND, THIRD]
    # Answering-machine detection is asked for on every person's leg.
    assert all(d[2].get("machine_detection") == "true" for d in provider.dialled)
    # The private briefing is the card, short.
    assert (
        provider.dialled[0][1].startswith("Asha.")
        and len(provider.dialled[0][1]) <= 240
    )
    # The no-answer and machine legs were hung up; the bridged one was not.
    assert provider.hung == ["leg-1", "leg-2"]
    assert engine.ended == [EndTaskReason.TRANSFER_CALL.value]
    spoken = " ".join(engine.spoken())
    assert "bring in Priya" in spoken and "putting you through" in spoken

    [row] = await _rows(call.people.org, call.run.id)
    assert row.state == record.BRIDGED and row.reason_code == "explicit_request"
    assert [a["outcome"] for a in row.attempts] == ["no_answer", "machine", "human"]
    assert row.time_to_human_ms is not None
    assert row.handoff_card["caller"]["name"] == "Asha"
    assert row.handoff_card["consent"]["ai_disclosed"] is True

    async with db_client.async_session() as session:
        card = await session.get(AgentEventModel, row.timeline_event_id)
    assert card.kind == "escalated" and card.workflow_id == call.workflow.id
    assert card.payload["state"] == record.BRIDGED

    outcome = await _outcome(call.people.org, call.run.id)
    assert outcome.outcome == "escalated" and outcome.transfer_result == "bridged"
    assert (
        outcome.reason_code == "explicit_request"
        and outcome.time_to_human_ms is not None
    )


@pytest.mark.asyncio
async def test_a_machine_on_the_only_line_is_never_bridged(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "machine"})
    _provider(monkeypatch, provider)
    engine = FakeEngine()
    runtime = _runtime(call, engine, transfer_numbers=[{"number": FIRST}])
    runtime.on_user_text("connect me to a human")
    await runtime._task

    assert engine.ended == []  # not handed to anything
    assert provider.hung == ["leg-1"]
    [row] = await _rows(call.people.org, call.run.id)
    assert row.state == record.FAILED and row.failure_reason == "machine"


@pytest.mark.asyncio
async def test_nobody_answering_walks_the_fallback_ladder_in_order(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "no_answer", SECOND: "busy"})
    _provider(monkeypatch, provider)
    sent: list[dict] = []

    async def send_message(**kwargs):
        sent.append(kwargs)
        return SimpleNamespace(ok=True)

    import api.services.messaging.send as send_module

    monkeypatch.setattr(send_module, "send_message", send_message)
    engine = FakeEngine()
    runtime = _runtime(
        call,
        engine,
        transfer_numbers=[{"number": FIRST}, {"number": SECOND}],
        ticket_channel="sms",
    )
    runtime.on_user_text("I want to speak to a human")
    await runtime._task

    # 1. next human, 2. back to the agent with an honest explanation
    assert [d[0] for d in provider.dialled] == [FIRST, SECOND]
    assert engine.muted[-1] is False
    assert any("couldn't reach anyone" in line for line in engine.spoken())
    # 3. a callback is offered first
    assert "call back" in engine.notes()[-1].messages[0]["content"]

    refused = await runtime.choose_fallback("ticket")
    assert refused["status"] == "not_yet"  # out of order
    unread = await runtime.choose_fallback("callback", number="98765 43210")
    assert unread["status"] == "not_filed"  # not read back yet

    declined = await runtime.choose_fallback("declined")
    assert "reference number" in declined["next"]  # 4. the ticket
    ticket = await runtime.choose_fallback("ticket")
    assert ticket["status"] == "sent" and ticket["reference"].startswith("ESC-")
    assert sent and sent[0]["to"] == CALLER and sent[0]["provider"] == "plivo"

    [row] = await _rows(call.people.org, call.run.id)
    assert row.state == record.FAILED and row.fallback == "ticket"

    await runtime.finalise()
    outcome = await _outcome(call.people.org, call.run.id)
    assert outcome.transfer_result == "failed" and outcome.fallback == "ticket"


@pytest.mark.asyncio
async def test_a_callback_is_filed_with_the_number_read_back(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "no_answer"})
    _provider(monkeypatch, provider)
    runtime = _runtime(call, transfer_numbers=[{"number": FIRST}])
    runtime.on_user_text("let me talk to a human")
    await runtime._task

    filed = await runtime.choose_fallback(
        "callback", number="098765 43210", number_read_back=True
    )
    assert filed["status"] == "filed"
    [task] = await _tasks(call.people.org)
    assert task.title == "Call back Asha" and task.priority == "high"
    assert "+919876543210" in task.brief and task.created_by == call.people.a.id
    assert task.due_at is not None


@pytest.mark.asyncio
async def test_voicemail_for_the_team_is_the_last_rung(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "no_answer"})
    _provider(monkeypatch, provider)
    runtime = _runtime(call, transfer_numbers=[{"number": FIRST}])
    runtime.on_user_text("let me talk to a human")
    await runtime._task
    await runtime.choose_fallback("declined")  # no callback; no ticket configured
    left = await runtime.choose_fallback(
        "voicemail", message="Please call about order 1234"
    )
    assert left["status"] == "left"
    async with db_client.async_session() as session:
        [note] = list(
            (
                await session.execute(
                    select(AgentEventModel).where(
                        AgentEventModel.organization_id == call.people.org,
                        AgentEventModel.kind == "needs_attention",
                    )
                )
            ).scalars()
        )
    assert note.payload["voicemail"]["message"] == "Please call about order 1234"
    # The run page; live while the call lasts, the full transcript after.
    assert f"/run/{call.run.id}?live=1" in note.payload["transcript_url"]


@pytest.mark.asyncio
async def test_outside_transfer_hours_nobody_is_rung_and_a_callback_offered(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "human"})
    _provider(monkeypatch, provider)
    engine = FakeEngine()
    runtime = _runtime(call, engine)
    monkeypatch.setattr(runtime, "humans_available", lambda: False)
    runtime.evaluator.humans_available = lambda: False

    runtime.on_user_text("Let me talk to a human")
    await runtime._task

    assert provider.dialled == []
    assert any("available right now" in line for line in engine.spoken())
    assert "call back" in engine.notes()[-1].messages[0]["content"]
    [row] = await _rows(call.people.org, call.run.id)
    assert row.state == record.FAILED and row.failure_reason == "no_human_available"
    outcome_after = await runtime.choose_fallback(
        "callback", number=CALLER, number_read_back=True
    )
    assert outcome_after["status"] == "filed"


@pytest.mark.asyncio
async def test_duplicate_events_make_one_escalation_and_one_dial(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "human"})
    _provider(monkeypatch, provider)
    runtime = _runtime(call)

    runtime.on_user_text("Let me talk to a human")
    runtime.on_user_text("A HUMAN. Now.")
    params = SimpleNamespace(arguments={"kind": "explicit_request"}, results=[])

    async def result_callback(result, properties=None):
        params.results.append(result)

    params.result_callback = result_callback
    await runtime._signal_handler(params)
    tool = SimpleNamespace(definition={"config": {"destination": SECOND}})
    assert await runtime.handle_transfer_tool(tool, params)
    await runtime._task

    assert [d[0] for d in provider.dialled] == [FIRST]
    assert len(await _rows(call.people.org, call.run.id)) == 1
    # A second worker seeing the same trigger opens nothing new.
    again, created = await record.open_escalation(
        organization_id=call.people.org,
        workflow_id=call.workflow.id,
        workflow_run_id=call.run.id,
        sequence=1,
        reason="explicit_request",
    )
    assert created is False
    # And cannot dial attempt one a second time.
    assert not await record.claim_attempt(
        again,
        organization_id=call.people.org,
        expected_count=0,
        transfer_id="t-x",
        target="x",
    )


@pytest.mark.asyncio
async def test_a_caller_who_hangs_up_on_hold_gets_a_callback_task(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({})  # nobody ever answers
    _provider(monkeypatch, provider)
    runtime = _runtime(call)
    runtime.on_user_text("Let me talk to a human")
    for _ in range(50):
        await asyncio.sleep(0.05)
        if provider.dialled:
            break
    await runtime.finalise()

    [row] = await _rows(call.people.org, call.run.id)
    assert row.state == record.FAILED and row.failure_reason == "caller_hung_up"
    [task] = await _tasks(call.people.org)
    assert "not confirmed" in task.brief
    outcome = await _outcome(call.people.org, call.run.id)
    assert outcome.failure_reason == "caller_hung_up" and outcome.fallback == "callback"


@pytest.mark.asyncio
async def test_a_soft_signal_repairs_before_it_escalates(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({FIRST: "human"})
    _provider(monkeypatch, provider)
    runtime = _runtime(call)
    runtime.evaluator.observe_failure("no_match")
    runtime.evaluator.observe_failure("no_match")
    params = SimpleNamespace(arguments={"kind": "no_match"}, results=[])

    async def result_callback(result, properties=None):
        params.results.append(result)

    params.result_callback = result_callback
    await runtime._signal_handler(params)
    assert params.results[-1]["status"] == "repair"
    assert provider.dialled == []
    await runtime._signal_handler(params)
    assert params.results[-1]["status"] == "handing_over"
    await runtime._task
    [row] = await _rows(call.people.org, call.run.id)
    assert row.reason_code == "repair_loop"


@pytest.mark.asyncio
async def test_a_call_resolved_by_the_agent_is_recorded_as_such(call, flag_on):
    runtime = _runtime(call)
    runtime.on_user_text("What time do you open?")
    await runtime.finalise()
    outcome = await _outcome(call.people.org, call.run.id)
    assert outcome.outcome == "resolved_by_ai" and outcome.escalation_id is None


# --- hand back to AI ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_hand_back_moves_the_caller_and_the_new_run_opens_with_the_note(
    call, monkeypatch, flag_on, quick
):
    runtime, provider = await _bridged(call, monkeypatch)
    row = runtime.row
    await db_client.update_escalation(
        row.id, organization_id=call.people.org, human_call_id="human-leg"
    )

    result = await actions.hand_back(
        call.people.org,
        row.escalation_uuid,
        user_id=call.people.a.id,
        note="Refund approved, reference R-55. Book the pickup.",
    )
    assert result["state"] == record.COMPLETED and result["handed_back_at"]
    assert result["outcome_note"].startswith("Refund approved")
    [moved] = provider.handed_back
    assert moved["caller"] == "CALL-1" and moved["human"] == "human-leg"
    assert "/telephony/plivo/escalation-handback/" in moved["url"]

    # A second click does not move the caller twice.
    await actions.hand_back(
        call.people.org, row.escalation_uuid, user_id=call.people.a.id, note="again"
    )
    assert len(provider.handed_back) == 1

    import api.services.telephony.stream_capability as stream_capability

    async def stream_url(**kw):
        return f"wss://api.example.test/ws/{kw['workflow_run_id']}"

    monkeypatch.setattr(stream_capability, "stream_url", stream_url)
    token = moved["url"].rsplit("/", 1)[1]
    claim = await actions.claim_handback(token)
    assert await actions.claim_handback(token) is None  # spent once

    from api.services.escalation.handback import start_resumed_run

    response = await start_resumed_run(
        claim, provider=provider, original_run=call.run, call_id="CALL-1"
    )
    resumed = await db_client.get_workflow_run_by_id(response["workflow_run_id"])
    note = resumed.initial_context["escalation_handback"]
    assert (
        note["note"].startswith("Refund approved")
        and note["previous_run_id"] == call.run.id
    )

    from api.services.call_concurrency import call_concurrency

    await call_concurrency.release_workflow_run_slot(resumed.id)

    engine = FakeEngine()
    back = EscalationRuntime(
        engine=engine,
        policy=EscalationPolicy(),
        organization_id=call.people.org,
        workflow_id=call.workflow.id,
        workflow_run=resumed,
    )
    assert back.resuming and await back.announce_handback()
    [frame] = engine.notes()
    assert frame.run_llm is True
    assert "Refund approved, reference R-55" in frame.messages[0]["content"]
    assert "Do not greet them" in frame.messages[0]["content"]


@pytest.mark.asyncio
async def test_hand_back_is_refused_where_the_carrier_cannot(
    call, monkeypatch, flag_on, quick
):
    runtime, provider = await _bridged(call, monkeypatch)
    monkeypatch.setattr(provider, "supports_escalation_hand_back", lambda: False)
    with pytest.raises(actions.NotSupported):
        await actions.hand_back(
            call.people.org,
            runtime.row.escalation_uuid,
            user_id=call.people.a.id,
            note="",
        )


@pytest.mark.asyncio
async def test_declining_while_it_rings_moves_to_the_next_person(
    call, monkeypatch, flag_on, quick
):
    provider = FakeProvider({SECOND: "human"})  # the first never answers
    _provider(monkeypatch, provider)
    runtime = _runtime(call)
    runtime.on_user_text("Let me talk to a human")
    for _ in range(50):
        await asyncio.sleep(0.05)
        if provider.dialled:
            break
    await asyncio.sleep(0.1)
    await actions.decline(
        call.people.org, runtime.row.escalation_uuid, user_id=call.people.b.id
    )
    await asyncio.wait_for(runtime._task, timeout=10)
    assert [d[0] for d in provider.dialled] == [FIRST, SECOND]
    [row] = await _rows(call.people.org, call.run.id)
    assert row.state == record.BRIDGED and row.human_response == "declined"
    assert row.attempts[0]["outcome"] == "declined"


# --- organization scoping and the routes -------------------------------------------------


@pytest.mark.asyncio
async def test_another_organization_cannot_see_or_act_on_a_handover(
    call, monkeypatch, flag_on, quick
):
    runtime, _provider_ = await _bridged(call, monkeypatch)
    uuid = runtime.row.escalation_uuid
    with pytest.raises(actions.NotFound):
        await actions.get(call.people.other, uuid)
    with pytest.raises(actions.NotFound):
        await actions.hand_back(
            call.people.other, uuid, user_id=call.people.c.id, note=""
        )

    async with client_as(call.people.as_c) as client:
        assert (await client.get(f"/api/v1/escalations/{uuid}")).status_code == 404
        assert (
            await client.post(f"/api/v1/escalations/{uuid}/accept")
        ).status_code == 404
        assert (
            await client.get(f"/api/v1/escalations/policy/{call.workflow.id}")
        ).status_code == 404
        put = await client.put(
            f"/api/v1/escalations/policy/{call.workflow.id}",
            json={"policy": {"max_ai_attempts": 3}},
        )
        assert put.status_code == 404
    async with client_as(call.people.as_a) as client:
        mine = await client.get(f"/api/v1/escalations/{uuid}")
        assert mine.status_code == 200 and mine.json()["state"] == record.BRIDGED
        assert mine.json()["can_hand_back"] is True
        accepted = await client.post(f"/api/v1/escalations/{uuid}/accept")
        assert accepted.json()["human_response"] == "accepted"
        opened = await client.get("/api/v1/escalations/open")
        assert opened.status_code == 200


@pytest.mark.asyncio
async def test_the_policy_is_saved_into_the_agents_draft(call, flag_on):
    async with client_as(call.people.as_a) as client:
        bad = await client.put(
            f"/api/v1/escalations/policy/{call.workflow.id}",
            json={"policy": {"always_transfer_topics": ["weather"]}},
        )
        assert bad.status_code == 422
        saved = await client.put(
            f"/api/v1/escalations/policy/{call.workflow.id}",
            json={
                "policy": {
                    "transfer_numbers": [{"number": "098765 43210", "name": "Priya"}],
                    "always_transfer_topics": ["emergency", "fraud"],
                    "max_ai_attempts": 3,
                }
            },
        )
        assert saved.status_code == 200
        body = saved.json()
        assert body["policy"]["transfer_numbers"][0]["number"] == "+919876543210"
        assert body["unpublished"] is True
        assert [t["key"] for t in body["topics"]][:3] == [
            "emergency",
            "fraud",
            "legal_threat",
        ]
    draft = await db_client.get_draft_version(call.workflow.id)
    assert draft.workflow_configurations["escalation_policy"]["max_ai_attempts"] == 3


@pytest.mark.asyncio
async def test_the_policy_can_be_changed_by_chat_as_a_draft_card(call, flag_on):
    from api.services.workflow import self_edit

    assert "escalation" in self_edit.tool_properties()
    result = await self_edit.propose(
        organization_id=call.people.org,
        workflow_id=call.workflow.id,
        workflow_run_id=None,
        arguments={
            "why": "Send refund disputes over 2000 to Priya",
            "escalation": {
                "refund_limit": 2000,
                "always_transfer_topics": ["emergency", "refund_over_limit"],
            },
        },
    )
    assert result["status"] == "proposed"
    draft = await db_client.get_draft_version(call.workflow.id)
    assert draft.workflow_configurations["escalation_policy"]["refund_limit"] == 2000
    async with db_client.async_session() as session:
        [card] = list(
            (
                await session.execute(
                    select(AgentEventModel).where(
                        AgentEventModel.organization_id == call.people.org,
                        AgentEventModel.kind == "edit_proposed",
                    )
                )
            ).scalars()
        )
    assert card.payload["step"] == "Escalation"
    assert "Refund limit: Rs 2000" in card.payload["new"]


# --- the flag ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_with_the_flag_off_nothing_changes(call, monkeypatch):
    monkeypatch.setattr(constants, "ESCALATION_V2_ENABLED", False)
    runtime = await EscalationRuntime.for_run(
        engine=FakeEngine(),
        run_configs={},
        organization_id=call.people.org,
        workflow=call.workflow,
        workflow_run=call.run,
        is_phone_call=True,
    )
    assert runtime is None

    from api.services.workflow import self_edit
    from api.services.workflow.pipecat_engine_context_composer import (
        compose_functions_for_node,
    )

    node = SimpleNamespace(
        document_uuids=[], tool_uuids=[], out_edges=[], is_end=False, data=None
    )
    names = [
        f["function"]["name"] if "function" in f else f.get("name")
        for f in await compose_functions_for_node(node=node, custom_tool_manager=None)
    ]
    assert "report_escalation_signal" not in names
    assert "escalation" not in self_edit.tool_properties()

    async with client_as(call.people.as_a) as client:
        assert (
            await client.get(f"/api/v1/escalations/policy/{call.workflow.id}")
        ).status_code == 404
        assert (await client.get("/api/v1/escalations/open")).status_code == 404


@pytest.mark.asyncio
async def test_the_runtime_is_built_only_for_phone_calls(call, flag_on):
    kwargs = dict(
        engine=FakeEngine(),
        run_configs={"escalation_policy": {"max_ai_attempts": 4}},
        organization_id=call.people.org,
        workflow=call.workflow,
        workflow_run=call.run,
    )
    assert await EscalationRuntime.for_run(**kwargs, is_phone_call=False) is None
    built = await EscalationRuntime.for_run(**kwargs, is_phone_call=True)
    assert built is not None and built.policy.max_ai_attempts == 4


# --- the carrier's machine-detection callback -----------------------------------------------


@pytest.mark.asyncio
async def test_plivo_machine_detection_drops_a_machine_and_reports_a_person(
    monkeypatch,
):
    import api.services.escalation.dialer as dialer_module
    import api.services.telephony.webhook_guard as guard
    from api.services.telephony.providers.plivo.routes import (
        handle_plivo_escalation_amd,
    )

    provider = FakeProvider()
    reported: list[tuple] = []

    async def resolved(_transfer_id):
        return provider, SimpleNamespace()

    async def signed(*_a, **_k):
        return None

    async def report(transfer_id, *, human, reason=None, call_id=None):
        reported.append((transfer_id, human, reason, call_id))

    async def answered(_t, _c):
        return None

    monkeypatch.setattr(guard, "provider_for_transfer", resolved)
    monkeypatch.setattr(guard, "require_signature", signed)
    monkeypatch.setattr(dialer_module, "report_leg_outcome", report)
    monkeypatch.setattr(actions, "on_human_answered", answered)

    class Req:
        def __init__(self, form):
            self._form = form

        async def form(self):
            return self._form

    machine = await handle_plivo_escalation_amd(
        "t-1", Req({"Machine": "true", "CallUUID": "leg-9"})
    )
    assert machine["status"] == "machine"
    assert reported[-1] == ("t-1", False, "answered_by_machine", "leg-9")
    assert provider.hung == ["leg-9"]

    person = await handle_plivo_escalation_amd(
        "t-2", Req({"Machine": "false", "CallUUID": "leg-10"})
    )
    assert person["status"] == "human" and reported[-1][1] is True


def test_the_caller_conference_carries_the_intro_only_for_an_escalation():
    import asyncio as _asyncio

    from api.services.telephony.providers.plivo.routes import (
        handle_plivo_transfer_caller,
    )

    plain = _asyncio.run(handle_plivo_transfer_caller("conf-1")).body.decode()
    assert "callbackUrl" not in plain
