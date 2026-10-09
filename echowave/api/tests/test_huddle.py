"""The huddle: talking to an agent as a teammate, in its thread.

Done when: every route (and the socket) is a 404 while ``huddle`` is off;
a huddle starts only with an agent of the caller's own workspace, refuses
honestly before anything connects (setup, the day's voice minutes, already
live), and its session is the caller's alone and never a Talk session; the
agent's tools read and change nothing, except a proposed edit, which is a
draft and a card on the agent's thread and is never published; the
huddle's lines are written into the agent's thread as one ``huddle`` event;
and the agent's notes from a huddle are this person's, come back next time,
can be forgotten, and never reach the agent's prompt.

Real database throughout; only the model (``client.stream``) and the
speech readiness are stubbed.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from api import constants
from api.db import db_client
from api.db.models import WorkflowDefinitionModel
from api.enums import AgentEventActor, AgentEventKind, WorkflowRunMode
from api.services import quotas
from api.services.agent_builder import client as model_client
from api.services.huddle import context, record, tools
from api.services.huddle import session as huddle_session
from api.services.huddle.turn import HuddleState
from api.services.voice import readiness, sessions
from api.services.voice.brain import Turn
from api.services.workflow import publish_gate, self_edit
from api.tests.support.voice import clean, client_as, make_people

GRAPH = {
    "nodes": [
        {
            "id": "1",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {
                "name": "Start",
                "prompt": "Greet the caller and ask how you can help.",
                "greeting": "Hello, Lakshmi Clinic.",
            },
        },
        {
            "id": "2",
            "type": "endCall",
            "position": {"x": 0, "y": 200},
            "data": {"name": "End", "prompt": "Say goodbye."},
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


@pytest.fixture
async def people(test_engine):
    p = await make_people("huddle")
    p.agent = await db_client.create_workflow(
        name="Front desk",
        workflow_definition=copy.deepcopy(GRAPH),
        user_id=p.a.id,
        organization_id=p.org,
    )
    p.elsewhere = await db_client.create_workflow(
        name="Their desk",
        workflow_definition=copy.deepcopy(GRAPH),
        user_id=p.c.id,
        organization_id=p.other,
    )
    yield p
    async with db_client.async_session() as session:
        for org in (p.org, p.other):
            await session.execute(
                text("DELETE FROM agent_events WHERE organization_id = :o"), {"o": org}
            )
        await session.commit()
    await clean(p)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(constants, "HUDDLE_ENABLED", True)


@pytest.fixture
def ready(monkeypatch):
    """Voice set up -- checked against the huddle's own switch."""
    asked: list[str] = []

    async def available(**kwargs):
        asked.append(kwargs.get("flag"))
        return readiness.Readiness(
            readiness.AVAILABLE,
            config={
                "stt": {"provider": "sarvam", "model": "saaras:v3"},
                "tts": {"provider": "sarvam", "model": "bulbul:v3"},
                "language": "en-IN",
            },
        )

    monkeypatch.setattr(readiness, "live_voice", available)
    return asked


async def _huddle_events(org: int, workflow_id: int) -> list:
    return list(
        await db_client.agent_events(
            organization_id=org, workflow_id=workflow_id, kinds=[record.KIND]
        )
    )


def _state(people, **kwargs) -> HuddleState:
    return HuddleState(
        organization_id=people.org,
        user_id=kwargs.get("user_id", people.a.id),
        workflow_id=people.agent.id,
        session_id=kwargs.get("session_id", 4242),
        agent_name="Front desk",
        system="You are Front desk.",
        payload=record.new_payload(
            session_id=kwargs.get("session_id", 4242),
            user_id=kwargs.get("user_id", people.a.id),
        ),
    )


# --- off means absent --------------------------------------------------------


@pytest.mark.asyncio
class TestOffMeansAbsent:
    async def test_every_route_is_404_while_off(self, people):
        wid = people.agent.id
        async with client_as(people.as_a) as c:
            assert (await c.post(f"/api/v1/huddle/{wid}/sessions")).status_code == 404
            assert (await c.get("/api/v1/huddle/sessions/1")).status_code == 404
            assert (await c.get(f"/api/v1/huddle/{wid}/notes")).status_code == 404
            assert (
                await c.post("/api/v1/huddle/sessions/1/end", json={})
            ).status_code == 404

    async def test_the_socket_is_404_while_off(self, people):
        from api.routes.webrtc_signaling import huddle_signaling_websocket

        with pytest.raises(HTTPException) as refused:
            await huddle_signaling_websocket(
                SimpleNamespace(), session_id=1, user=people.as_a
            )
        assert refused.value.status_code == 404

    async def test_the_flag_is_reported_off_by_default(self):
        from api.services import features

        assert "huddle" in features.FLAGS
        assert features.public()["huddle"] is constants.HUDDLE_ENABLED


# --- starting ------------------------------------------------------------------


@pytest.mark.asyncio
class TestStarting:
    async def test_another_workspaces_agent_is_not_found(self, people, on, ready):
        async with client_as(people.as_a) as c:
            refused = await c.post(f"/api/v1/huddle/{people.elsewhere.id}/sessions")
        assert refused.status_code == 404

    async def test_needs_setup_is_said_and_nothing_starts(self, people, on):
        # No transcriber, voice or model key on a local instance.
        async with client_as(people.as_a) as c:
            refused = await c.post(f"/api/v1/huddle/{people.agent.id}/sessions")
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "needs_setup"
        async with db_client.async_session() as session:
            from api.db.voice_models import VoiceSessionModel

            rows = (
                await session.scalars(
                    select(VoiceSessionModel).where(
                        VoiceSessionModel.user_id == people.a.id
                    )
                )
            ).all()
        assert rows == []

    async def test_readiness_asks_the_huddle_switch_not_talks(self, people, on, ready):
        async with client_as(people.as_a) as c:
            started = await c.post(f"/api/v1/huddle/{people.agent.id}/sessions")
        assert started.status_code == 201
        assert ready == ["huddle"]

    async def test_real_readiness_follows_the_huddle_flag(self, people, monkeypatch):
        monkeypatch.setattr(constants, "HUDDLE_ENABLED", False)
        monkeypatch.setattr(constants, "DECIBYL_VOICE_ENABLED", True)
        state = await readiness.live_voice(
            organization_id=people.org, user_id=people.a.id, flag="huddle"
        )
        assert state.state == readiness.DISABLED_BY_POLICY

    async def test_over_the_daily_minutes_is_refused(
        self, people, on, ready, monkeypatch
    ):
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTAS_ENABLED", True)
        monkeypatch.setattr(constants, "OPERATIONAL_QUOTA_VOICE_MINUTES", 1)
        await quotas.consume(people.a.id, quotas.VOICE_MINUTES)
        async with client_as(people.as_a) as c:
            refused = await c.post(f"/api/v1/huddle/{people.agent.id}/sessions")
        assert refused.status_code == 429
        assert refused.json()["detail"]["code"] == "voice_limit_reached"

    async def test_starts_with_the_agent_named_and_one_live_per_person(
        self, people, on, ready
    ):
        async with client_as(people.as_a) as c:
            started = await c.post(f"/api/v1/huddle/{people.agent.id}/sessions")
            second = await c.post(f"/api/v1/huddle/{people.agent.id}/sessions")
        assert started.status_code == 201
        body = started.json()
        assert body["state"] == "connecting"
        assert body["workflow_id"] == people.agent.id
        assert body["agent_name"] == "Front desk"
        assert body["notes"] == []
        assert second.status_code == 409
        assert second.json()["detail"]["code"] == "already_live"
        assert second.json()["detail"]["session_id"] == body["id"]

    async def test_works_with_talk_off(self, people, on, ready, monkeypatch):
        monkeypatch.setattr(constants, "DECIBYL_VOICE_ENABLED", False)
        async with client_as(people.as_a) as c:
            started = await c.post(f"/api/v1/huddle/{people.agent.id}/sessions")
        assert started.status_code == 201


# --- whose session -----------------------------------------------------------


@pytest.mark.asyncio
class TestWhoseSession:
    async def _huddle(self, people) -> dict:
        async with client_as(people.as_a) as c:
            return (await c.post(f"/api/v1/huddle/{people.agent.id}/sessions")).json()

    async def test_a_colleague_and_another_workspace_cannot_see_or_end_it(
        self, people, on, ready
    ):
        huddle = await self._huddle(people)
        for other in (people.as_b, people.as_c):
            async with client_as(other) as c:
                assert (
                    await c.get(f"/api/v1/huddle/sessions/{huddle['id']}")
                ).status_code == 404
                assert (
                    await c.post(f"/api/v1/huddle/sessions/{huddle['id']}/end", json={})
                ).status_code == 404
        async with client_as(people.as_a) as c:
            mine = await c.get(f"/api/v1/huddle/sessions/{huddle['id']}")
            ended = await c.post(f"/api/v1/huddle/sessions/{huddle['id']}/end", json={})
            again = await c.post(f"/api/v1/huddle/sessions/{huddle['id']}/end", json={})
        assert mine.status_code == 200
        assert ended.json()["state"] == "ended"
        assert again.json()["state"] == "ended"

    async def test_a_talk_session_is_not_a_huddle(self, people, on, ready):
        talk = await sessions.start(
            organization_id=people.org,
            user_id=people.a.id,
            thread_id=None,
            language="en-IN",
            voice=None,
            config={"stt": {"provider": "sarvam"}},
        )
        async with client_as(people.as_a) as c:
            assert (
                await c.get(f"/api/v1/huddle/sessions/{talk['id']}")
            ).status_code == 404
        assert (
            await huddle_session.get(
                organization_id=people.org, user_id=people.a.id, session_id=talk["id"]
            )
            is None
        )

    async def test_each_socket_connects_only_its_own_kind(
        self, people, on, ready, monkeypatch
    ):
        from api.routes.webrtc_signaling import (
            huddle_signaling_manager,
            voice_signaling_manager,
        )

        monkeypatch.setattr(constants, "DECIBYL_VOICE_ENABLED", True)
        huddle = await self._huddle(people)
        sent: list = []
        ws = SimpleNamespace(send_json=AsyncMock(side_effect=sent.append))
        user = SimpleNamespace(id=people.a.id)
        assert not await voice_signaling_manager._authorize_start(
            ws, 0, huddle["id"], people.org, user
        )
        assert sent[-1]["payload"]["error_type"] == "session_ended"
        assert await huddle_signaling_manager._authorize_start(
            ws, 0, huddle["id"], people.org, user
        )

    async def test_the_socket_refuses_somebody_elses_huddle(self, people, on, ready):
        from api.routes.webrtc_signaling import huddle_signaling_websocket

        huddle = await self._huddle(people)
        with pytest.raises(HTTPException) as refused:
            await huddle_signaling_websocket(
                SimpleNamespace(), session_id=huddle["id"], user=people.as_b
            )
        assert refused.value.status_code == 404


# --- the agent's tools --------------------------------------------------------


async def _published(workflow_id: int) -> dict:
    async with db_client.async_session() as session:
        row = (
            await session.scalars(
                select(WorkflowDefinitionModel).where(
                    WorkflowDefinitionModel.workflow_id == workflow_id,
                    WorkflowDefinitionModel.status == "published",
                )
            )
        ).first()
    return row.workflow_json


async def _a_call(people, *, escalated: bool = True) -> int:
    run = await db_client.create_workflow_run(
        name="call",
        workflow_id=people.agent.id,
        mode=WorkflowRunMode.SMALLWEBRTC.value,
        user_id=people.a.id,
        organization_id=people.org,
    )
    if escalated:
        await db_client.record_agent_event(
            organization_id=people.org,
            kind=AgentEventKind.ESCALATED.value,
            actor=AgentEventActor.AGENT.value,
            summary="Handed to Priya: caller asked for a refund",
            workflow_id=people.agent.id,
            workflow_run_id=run.id,
            payload={"reason": "explicit_request"},
        )
    await db_client.record_agent_event(
        organization_id=people.org,
        kind=AgentEventKind.CALL_ENDED.value,
        actor=AgentEventActor.SYSTEM.value,
        summary="Call · 2m10s",
        workflow_id=people.agent.id,
        workflow_run_id=run.id,
        payload={"answered": True, "run_id": run.id},
    )
    return run.id


async def _row_count(org: int) -> int:
    async with db_client.async_session() as session:
        return (
            await session.execute(
                text("SELECT count(*) FROM agent_events WHERE organization_id = :o"),
                {"o": org},
            )
        ).scalar_one()


def test_no_tool_can_publish_settle_or_discard():
    names = {schema["name"] for schema in tools.schemas()}
    assert names == tools.READS | tools.PROPOSALS
    for name in names:
        assert not any(
            word in name for word in ("publish", "settle", "discard", "delete")
        )


@pytest.mark.asyncio
class TestTools:
    async def test_reads_answer_and_change_nothing(self, people):
        run_id = await _a_call(people)
        before_rows = await _row_count(people.org)
        before_graph = await _published(people.agent.id)
        state = _state(people)

        work = await state.run_tool(tools.RECENT_WORK, {"days": 1})
        assert work["status"] == "ok"
        assert work["calls"]["total"] == 1
        assert work["counts"][AgentEventKind.ESCALATED.value] == 1
        assert {item["run_id"] for item in work["latest"]} == {run_id}

        detail = await state.run_tool(tools.CALL_DETAIL, {"run_id": run_id})
        assert detail["status"] == "ok"
        escalation = [s for s in detail["steps"] if s["kind"] == "escalated"][0]
        assert escalation["details"]["reason"] == "explicit_request"

        files = await state.run_tool(tools.SEARCH_FILES, {"query": "refund window"})
        assert files["status"] == "no_match"

        assert await _row_count(people.org) == before_rows
        assert await _published(people.agent.id) == before_graph
        assert await db_client.get_draft_version(people.agent.id) is None

    async def test_a_run_of_another_agent_or_workspace_is_not_found(self, people):
        theirs = await db_client.create_workflow_run(
            name="theirs",
            workflow_id=people.elsewhere.id,
            mode=WorkflowRunMode.SMALLWEBRTC.value,
            user_id=people.c.id,
            organization_id=people.other,
        )
        state = _state(people)
        detail = await state.run_tool(tools.CALL_DETAIL, {"run_id": theirs.id})
        assert detail["status"] == "not_found"
        assert "steps" not in detail

    async def test_a_proposed_edit_is_a_card_and_is_not_published(
        self, people, monkeypatch
    ):
        publish = AsyncMock()
        monkeypatch.setattr(publish_gate, "publish_draft", publish)
        monkeypatch.setattr(db_client, "publish_workflow_draft", publish)
        before = await _published(people.agent.id)
        state = _state(people)

        result = await state.run_tool(
            tools.PROPOSE_EDIT,
            {
                "find": "Lakshmi Clinic",
                "replace_with": "Lakshmi Hospital",
                "why": "renamed",
            },
        )

        assert result["status"] == "proposed"
        publish.assert_not_called()
        assert await _published(people.agent.id) == before
        assert await db_client.get_draft_version(people.agent.id) is not None
        cards = list(
            await db_client.agent_events(
                organization_id=people.org,
                workflow_id=people.agent.id,
                kinds=[AgentEventKind.EDIT_PROPOSED.value],
            )
        )
        assert len(cards) == 1
        assert cards[0].payload["replace_with"] == "Lakshmi Hospital"
        assert "decided" not in cards[0].payload
        # The huddle's row names the card it produced.
        assert state.payload["cards"] == [cards[0].id]

    async def test_a_spoken_yes_cannot_publish(self, people, monkeypatch):
        """The model asking for a tool that publishes is told it has none."""
        settle = AsyncMock()
        monkeypatch.setattr(self_edit, "settle", settle)
        state = _state(people)
        for name in ("publish_edit", "settle_edit", "publish"):
            result = await state.run_tool(name, {"event_id": 1, "action": "publish"})
            assert result["status"] == "error"
        settle.assert_not_called()


# --- the turn and its transcript --------------------------------------------


def _replies(*replies):
    """A stand-in for the model: each call returns the next reply, speaking
    its text through ``on_text`` as the real stream does."""
    calls: list[dict] = []
    queue = list(replies)

    async def stream(**kwargs):
        calls.append(kwargs)
        reply = queue.pop(0)
        if reply.text:
            await kwargs["on_text"](reply.text)
        return reply

    return stream, calls


@pytest.mark.asyncio
class TestTheTurn:
    async def test_lines_are_written_to_the_agents_thread_as_one_huddle_event(
        self, people, monkeypatch
    ):
        stream, calls = _replies(
            model_client.ModelReply(text="Four calls today, one escalated.")
        )
        monkeypatch.setattr(model_client, "stream", stream)
        state = _state(people)
        state.model = SimpleNamespace(provider="anthropic", model="m", api_key="k")
        said: list[str] = []

        async def on_words(piece):
            said.append(piece)

        turn = Turn(index=0, text="How many calls today?", started_at=0.0)
        body = await state.answer(None, turn, on_words)
        assert body == "Four calls today, one escalated."
        assert "".join(said) == body
        assert calls[0]["system"] == "You are Front desk."
        turn.heard = ["Four calls today,"]
        turn.interrupted = True
        await state.heard(None, turn)

        rows = await _huddle_events(people.org, people.agent.id)
        assert len(rows) == 1
        row = rows[0]
        assert row.actor == AgentEventActor.HUMAN.value
        assert row.folder_id is None
        assert row.summary == "Huddle with Front desk"
        turns = row.payload["turns"]
        assert [(t["who"], t["text"]) for t in turns] == [
            ("you", "How many calls today?"),
            ("agent", "Four calls today,"),
        ]
        assert turns[1]["interrupted"] is True
        assert row.payload["user_id"] == people.a.id

        # The next turn carries what was said, and the row is the same row.
        stream2, calls2 = _replies(model_client.ModelReply(text="Yes."))
        monkeypatch.setattr(model_client, "stream", stream2)
        await state.answer(
            None, Turn(index=1, text="Just one?", started_at=0.0), on_words
        )
        sent = calls2[0]["conversation"].messages
        assert sent[0] == {"role": "user", "content": "How many calls today?"}
        assert sent[1]["role"] == "assistant"
        assert sent[-1] == {"role": "user", "content": "Just one?"}
        assert len(await _huddle_events(people.org, people.agent.id)) == 1

    async def test_a_reconnect_carries_on_in_the_same_row(self, people):
        state = _state(people, session_id=777)
        await state.line("you", "Hello")
        found = await record.find(
            organization_id=people.org, workflow_id=people.agent.id, session_id=777
        )
        assert found is not None
        event_id, payload = found
        assert event_id == state.event_id
        assert payload["turns"][0]["text"] == "Hello"

    async def test_after_a_proposal_the_model_is_given_no_more_tools(
        self, people, monkeypatch
    ):
        call = model_client.ToolCall(
            id="t1",
            name=tools.PROPOSE_EDIT,
            arguments={"find": "Lakshmi Clinic", "replace_with": "LC", "why": "short"},
        )
        stream, calls = _replies(
            model_client.ModelReply(text="", tool_calls=(call,)),
            model_client.ModelReply(text="It is on screen for you to review."),
        )
        monkeypatch.setattr(model_client, "stream", stream)
        told: list = []

        async def tell(message):
            told.append(message)

        state = _state(people)
        state.tell = tell
        state.model = SimpleNamespace(provider="anthropic", model="m", api_key="k")
        turn = Turn(index=0, text="Call us LC from now on", started_at=0.0)

        async def on_words(_piece):
            return None

        await state.answer(None, turn, on_words)
        assert calls[0]["tools"] is not None
        assert calls[1]["tools"] is None
        assert turn.tool_turn is True
        assert told and told[0]["type"] == "huddle-card"


# --- teammate memory ---------------------------------------------------------


@pytest.mark.asyncio
class TestTeammateMemory:
    async def test_notes_are_kept_bounded_and_come_back_for_this_person(self, people):
        state = _state(people)
        assert (await state.run_tool(tools.REMEMBER, {"note": "Weekly numbers"}))[
            "status"
        ] == "remembered"
        assert (await state.run_tool(tools.REMEMBER, {"note": "weekly numbers"}))[
            "status"
        ] == "not_kept"
        for i in range(10):
            await state.run_tool(tools.REMEMBER, {"note": f"note {i}"})
        assert len(state.payload["notes"]) == record.MAX_NOTES_PER_HUDDLE
        await state.run_tool(tools.REMEMBER, {"note": "x" * 1000})

        mine = await record.notes_for(
            organization_id=people.org, user_id=people.a.id, workflow_id=people.agent.id
        )
        assert mine[-1] == "Weekly numbers"
        assert all(len(n) <= record.MAX_NOTE_CHARS for n in mine)
        assert (
            await record.notes_for(
                organization_id=people.org,
                user_id=people.b.id,
                workflow_id=people.agent.id,
            )
            == []
        )

    async def test_notes_load_into_the_next_huddle_not_the_agents_prompt(self, people):
        state = _state(people)
        await state.run_tool(tools.REMEMBER, {"note": "Priya wants numbers weekly"})
        notes = await record.notes_for(
            organization_id=people.org, user_id=people.a.id, workflow_id=people.agent.id
        )
        workflow = await db_client.get_workflow(
            people.agent.id, organization_id=people.org
        )
        prompt = await context.system_prompt(
            organization_id=people.org, workflow=workflow, notes=notes
        )
        assert "Priya wants numbers weekly" in prompt
        assert "You are Front desk" in prompt
        assert "not talking to a customer" in prompt.lower()
        assert "### Start" in prompt
        # The customer-facing prompt is the agent's steps and confirmed
        # memory; neither holds the note.
        assert "Priya" not in str(await _published(people.agent.id))
        from api.services.workflow import organisation_memory

        assert "Priya wants numbers weekly" not in str(
            await organisation_memory.recall_for_bot(
                organization_id=people.org, workflow_id=people.agent.id
            )
        )

    async def test_notes_routes_read_and_forget_and_are_scoped(self, people, on):
        state = _state(people)
        await state.run_tool(tools.REMEMBER, {"note": "Short answers"})
        await state.line("you", "Keep it short")
        async with client_as(people.as_a) as c:
            read = await c.get(f"/api/v1/huddle/{people.agent.id}/notes")
            assert read.json() == {"notes": ["Short answers"]}
            other = await c.get(f"/api/v1/huddle/{people.elsewhere.id}/notes")
            assert other.status_code == 404
            forgot = await c.delete(f"/api/v1/huddle/{people.agent.id}/notes")
            assert forgot.json() == {"notes": []}
        async with client_as(people.as_b) as c:
            assert (await c.get(f"/api/v1/huddle/{people.agent.id}/notes")).json() == {
                "notes": []
            }
        rows = await _huddle_events(people.org, people.agent.id)
        # The transcript stays; only the notes went.
        assert rows[0].payload["turns"][0]["text"] == "Keep it short"
        assert rows[0].payload["notes"] == []


# --- the connection ------------------------------------------------------------


@pytest.mark.asyncio
class TestTheConnection:
    async def test_prepare_reads_the_agent_and_carries_on_its_row(
        self, people, on, ready
    ):
        from api.services.huddle import voice

        started = await huddle_session.start(
            organization_id=people.org, user_id=people.a.id, workflow_id=people.agent.id
        )
        session = await huddle_session.get(
            organization_id=people.org, user_id=people.a.id, session_id=started["id"]
        )
        first = await voice.prepare(
            session=session, organization_id=people.org, user_id=people.a.id, tell=None
        )
        assert first is not None
        assert first.workflow_id == people.agent.id
        assert "You are Front desk" in first.system
        await first.line("you", "Hello")
        again = await voice.prepare(
            session=session, organization_id=people.org, user_id=people.a.id, tell=None
        )
        assert again.event_id == first.event_id
        assert again.payload["turns"][0]["text"] == "Hello"
        # Somebody else cannot take over the conversation by its id.
        assert (
            await voice.prepare(
                session=session,
                organization_id=people.other,
                user_id=people.c.id,
                tell=None,
            )
            is None
        )

    async def test_a_heard_reply_goes_where_the_ledger_says(self):
        from api.services.voice import brain

        written: list = []

        async def heard(ledger, turn):
            written.append(turn.text)

        ledger = brain.TurnLedger(
            session_id=1, organization_id=1, user_id=1, thread_id=None, on_heard=heard
        )
        turn = ledger.new_turn("hello")
        await brain.close_turn(ledger, turn, interrupted=False)
        await ledger.drain()
        assert written == ["hello"]
