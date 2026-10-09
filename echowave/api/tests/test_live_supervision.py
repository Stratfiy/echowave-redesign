"""Live supervision: listen in on a call and whisper to its agent.

Against a real Redis (``REDIS_URL``), with the database replaced by small
fakes: what is under test is the tap, the fan-out between workers, the
whisper's path into the model's context, and who may do any of it.

"Two workers" below are two Redis connections that share nothing in
process -- the call's session publishes on one, the listener reads on the
other -- which is the whole of what a second uvicorn process would add.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from types import SimpleNamespace

import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMMessagesAppendFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSTextFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.filters.identity_filter import IdentityFilter
from pipecat.processors.frame_processor import FrameDirection
from pipecat.tests.utils import run_test
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams

import api.services.live_supervision.session as live_session
import api.services.live_supervision.tap as live_tap
from api import constants
from api.db import db_client
from api.services.live_supervision import (
    access,
    channels,
    consent,
    registry,
    whisper,
)
from api.services.live_supervision.lines import LineBuilder
from api.services.live_supervision.listener import AUDIO_BUFFER, ListenerPump
from api.services.workflow import audit_log

ORG = 7101
OTHER_ORG = 7102
OWNER_ID = 501  # owns the agent; a plain member
ADMIN_ID = 502
MEMBER_ID = 503  # a member who does not own the agent
STRANGER_ID = 504  # in another workspace
WORKFLOW_ID = 31

START_NODE = {
    "id": "start",
    "type": "startCall",
    "data": {
        "name": "Start",
        "prompt": "Greet them.",
        "greeting": "Hello from Asha Dental.",
    },
}


def _user(user_id: int, org: int = ORG):
    return SimpleNamespace(
        id=user_id, selected_organization_id=org, email=f"user{user_id}@example.com"
    )


@pytest.fixture
async def redis_reset():
    channels.reset()
    yield
    client = channels._client
    channels.reset()
    if client is not None:
        await client.aclose()


@pytest.fixture
def run_id():
    return 900_000_000 + uuid.uuid4().int % 1_000_000


@pytest.fixture
def flag(monkeypatch):
    monkeypatch.setattr(constants, "LIVE_SUPERVISION_ENABLED", True)


@pytest.fixture
def audit(monkeypatch):
    rows: list[dict] = []

    async def record(organization_id, **kwargs):
        rows.append({"organization_id": organization_id, **kwargs})
        return True

    monkeypatch.setattr(audit_log, "record", record)
    return rows


@pytest.fixture
def fake_db(monkeypatch, run_id):
    """One workspace with one agent and one call in progress."""
    state = {
        "settings": {},
        "roles": {OWNER_ID: "member", ADMIN_ID: "admin", MEMBER_ID: "member"},
        "definition": {"nodes": [START_NODE], "edges": []},
        "events": [],
    }
    workflow = SimpleNamespace(
        id=WORKFLOW_ID,
        organization_id=ORG,
        user_id=OWNER_ID,
        name="Front desk",
        visibility="everyone",
        workflow_definition={"nodes": [START_NODE], "edges": []},
    )
    run = SimpleNamespace(
        id=run_id,
        workflow_id=WORKFLOW_ID,
        is_completed=False,
        mode="plivo",
        call_type="inbound",
        initial_context={"caller_number": "+919812345678"},
        definition=SimpleNamespace(workflow_json=state["definition"]),
    )
    state["workflow"] = workflow
    state["run"] = run

    async def get_workflow_run(rid, user_id=None, organization_id=None):
        return run if rid == run.id and organization_id == ORG else None

    async def get_workflow(wid, user_id=None, organization_id=None):
        return workflow if wid == WORKFLOW_ID and organization_id == ORG else None

    async def get_membership(user_id, organization_id):
        if organization_id != ORG or user_id not in state["roles"]:
            return None
        return SimpleNamespace(role=state["roles"][user_id])

    async def get_configuration_value(organization_id, key, default=None):
        return state["settings"].get((organization_id, key), default)

    async def upsert_configuration(organization_id, key, value, **_):
        state["settings"][(organization_id, key)] = value

    monkeypatch.setattr(db_client, "get_workflow_run", get_workflow_run)
    monkeypatch.setattr(db_client, "get_workflow", get_workflow)
    monkeypatch.setattr(db_client, "get_membership", get_membership)
    monkeypatch.setattr(db_client, "get_configuration_value", get_configuration_value)
    monkeypatch.setattr(db_client, "upsert_configuration", upsert_configuration)
    return state


def _allow(state, allowed: bool = True):
    state["settings"][(ORG, access.KEY)] = {"allow_listening": allowed}


class FakeTask:
    def __init__(self):
        self.queued: list = []
        self.observers: list = []

    async def queue_frames(self, frames):
        self.queued.extend(frames)

    def add_observer(self, observer):
        self.observers.append(observer)


class ContextTask(FakeTask):
    """Applies appended messages the way the user aggregator does (shown
    with the real aggregator in ``TestWhisper``)."""

    def __init__(self, context):
        super().__init__()
        self.context = context

    async def queue_frames(self, frames):
        await super().queue_frames(frames)
        for frame in frames:
            if isinstance(frame, LLMMessagesAppendFrame):
                self.context.add_messages(frame.messages)


class FakeLogs:
    current_node_name = "Booking"

    def __init__(self):
        self.events: list[dict] = []

    async def append(self, event, **_):
        self.events.append(event)


async def _live(run_id, *, context=None, task=None, logs=None, redis=None):
    session = live_session.LiveSession(
        run_id=run_id,
        organization_id=ORG,
        workflow_id=WORKFLOW_ID,
        agent_name="Front desk",
        direction="inbound",
        task=task or FakeTask(),
        context=context,
        logs_buffer=logs,
        step=lambda: "Booking",
        redis=redis,
    )
    await session.start()
    return session


async def _until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


@pytest.fixture
async def http(fake_db):
    from api.app import app
    from api.services.auth.depends import get_user

    holder = {"user": _user(ADMIN_ID)}

    async def current_user():
        return holder["user"]

    app.dependency_overrides[get_user] = current_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            client.holder = holder  # type: ignore[attr-defined]
            yield client
    finally:
        app.dependency_overrides.pop(get_user, None)


def _as(client, user_id, org=ORG):
    client.holder["user"] = _user(user_id, org)
    return client


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFlagOff:
    async def test_every_route_is_a_404(self, http, run_id, monkeypatch):
        monkeypatch.setattr(constants, "LIVE_SUPERVISION_ENABLED", False)
        assert (await http.get("/api/v1/live-calls")).status_code == 404
        assert (await http.get(f"/api/v1/live-calls/{run_id}")).status_code == 404
        response = await http.post(
            f"/api/v1/live-calls/{run_id}/whisper", json={"text": "hi"}
        )
        assert response.status_code == 404
        response = await http.put(
            "/api/v1/live-calls/settings", json={"allow_listening": True}
        )
        assert response.status_code == 404

    async def test_nothing_is_added_to_the_call(self, monkeypatch):
        monkeypatch.setattr(constants, "LIVE_SUPERVISION_ENABLED", False)
        task = FakeTask()
        result = await live_session.attach(
            task,
            workflow_run=SimpleNamespace(id=1, mode="plivo", call_type="inbound"),
            workflow=SimpleNamespace(id=2, organization_id=ORG, name="A"),
        )
        assert result is None
        assert task.observers == []

    async def test_a_text_chat_is_not_tapped(self, flag):
        task = FakeTask()
        result = await live_session.attach(
            task,
            workflow_run=SimpleNamespace(id=1, mode="textchat", call_type="inbound"),
            workflow=SimpleNamespace(id=2, organization_id=ORG, name="A"),
        )
        assert result is None
        assert task.observers == []


# ---------------------------------------------------------------------------
# The tap
# ---------------------------------------------------------------------------


def _pushed(
    frame, *, source=None, destination=None, direction=FrameDirection.DOWNSTREAM
):
    return FramePushed(
        source=source or IdentityFilter(),
        destination=destination or IdentityFilter(),
        frame=frame,
        direction=direction,
        timestamp=0,
    )


@pytest.mark.asyncio
class TestTap:
    async def test_a_listener_that_never_reads_costs_frames_not_time(self):
        """The caller's path: every frame is handed over in microseconds and
        the queue stays bounded, however far behind the consumer is."""
        tap = live_tap.LiveCallTap(audio_queue_size=5, event_queue_size=5)
        source = BaseInputTransport(TransportParams())
        frames = [
            _pushed(
                InputAudioRawFrame(
                    audio=b"\x00\x01" * 160, sample_rate=8000, num_channels=1
                ),
                source=source,
            )
            for _ in range(5000)
        ]
        started = time.perf_counter()
        for data in frames:
            await tap.on_push_frame(data)
        elapsed = time.perf_counter() - started
        assert tap.audio.qsize() == 5
        assert tap.dropped_audio == 4995
        # 5000 frames is 100 seconds of a call. Handing them all over must
        # take a sliver of that; a blocking tap would never return at all.
        assert elapsed < 1.0

    async def test_words_survive_an_audio_flood(self):
        tap = live_tap.LiveCallTap(audio_queue_size=2)
        source = BaseInputTransport(TransportParams())
        for _ in range(50):
            await tap.on_push_frame(
                _pushed(
                    InputAudioRawFrame(
                        audio=b"\x00\x00", sample_rate=8000, num_channels=1
                    ),
                    source=source,
                )
            )
        await tap.on_push_frame(_pushed(TranscriptionFrame("I need a slot", "u", "t")))
        assert tap.events.get_nowait() == (live_tap.CALLER, "I need a slot", True)

    async def test_takes_the_agreed_tap_points_and_each_frame_once(self):
        tap = live_tap.LiveCallTap()
        output = BaseOutputTransport(TransportParams())
        tts_audio = TTSAudioRawFrame(
            audio=b"\x01\x00" * 10, sample_rate=24000, num_channels=1
        )
        # Before the output transport: taken. Anywhere else: not.
        await tap.on_push_frame(_pushed(tts_audio, destination=output))
        await tap.on_push_frame(_pushed(tts_audio))
        # The agent's words only as the output transport releases them.
        words = TTSTextFrame("Sure", aggregated_by="word")
        await tap.on_push_frame(_pushed(words))
        await tap.on_push_frame(_pushed(words, source=output))
        await tap.on_push_frame(_pushed(words, source=output))
        interim = InterimTranscriptionFrame("I ne", "u", "t")
        for _ in range(3):  # observed at three hops
            await tap.on_push_frame(_pushed(interim))
        assert tap.audio.qsize() == 1
        assert tap.audio.get_nowait()[1] == "a"
        assert [tap.events.get_nowait() for _ in range(tap.events.qsize())] == [
            (live_tap.AGENT_TEXT, "Sure"),
            (live_tap.CALLER, "I ne", False),
        ]

    async def test_audio_is_not_copied_while_nobody_listens_to_it(self):
        tap = live_tap.LiveCallTap(audio_wanted=lambda: False)
        source = BaseInputTransport(TransportParams())
        await tap.on_push_frame(
            _pushed(
                InputAudioRawFrame(audio=b"\x00\x00", sample_rate=8000, num_channels=1),
                source=source,
            )
        )
        assert tap.audio.qsize() == 0

    async def test_the_pipeline_runs_the_same_with_a_stalled_tap(self):
        """Frames reach the end of a real pipeline, all of them, in order,
        while the tap's queues are full and nothing drains them."""
        tap = live_tap.LiveCallTap(audio_queue_size=1, event_queue_size=1)
        frames = []
        for i in range(40):
            frames.append(TranscriptionFrame(f"line {i}", "u", "t"))
            frames.append(
                TTSAudioRawFrame(
                    audio=b"\x00\x00" * 80, sample_rate=16000, num_channels=1
                )
            )
        down, _ = await run_test(
            IdentityFilter(),
            frames_to_send=frames,
            expected_down_frames=[type(f) for f in frames],
            observers=[tap],
        )
        texts = [f.text for f in down if isinstance(f, TranscriptionFrame)]
        assert texts == [f"line {i}" for i in range(40)]
        assert tap.dropped_events > 0


# ---------------------------------------------------------------------------
# Transcript lines
# ---------------------------------------------------------------------------


class TestLines:
    def test_order_speakers_and_finality(self):
        lines = LineBuilder()
        events = []
        events += lines.agent_text("Hello,")
        events += lines.agent_text("how can I help?")
        events += lines.agent_done()
        events += lines.caller("I want", False)
        events += lines.caller("I want to book", True)
        events += lines.agent_text("Which day")
        events += lines.interrupted()
        seqs = [e["seq"] for e in events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        line_events = [e for e in events if e["type"] == "line"]
        assert [
            (e["speaker"], e["line"], e["text"], e["final"]) for e in line_events
        ] == [
            ("agent", 1, "Hello,", False),
            ("agent", 1, "Hello, how can I help?", False),
            ("agent", 1, "Hello, how can I help?", True),
            ("caller", 2, "I want", False),
            ("caller", 2, "I want to book", True),
            ("agent", 3, "Which day", False),
            ("agent", 3, "Which day", True),
        ]
        assert line_events[-1]["cut_off"] is True
        assert events[-1]["type"] == "interrupted"

    def test_the_caller_starting_closes_the_agents_line(self):
        lines = LineBuilder()
        lines.agent_text("One moment")
        events = lines.caller("wait", False)
        assert events[0]["speaker"] == "agent" and events[0]["final"] is True
        assert events[1]["speaker"] == "caller"

    def test_a_code_the_agent_asked_for_is_masked(self):
        lines = LineBuilder()
        lines.agent_text("Please read me the OTP")
        lines.agent_done()
        event = lines.caller("it is 482913", True)[-1]
        assert "482913" not in event["text"]


# ---------------------------------------------------------------------------
# Fan-out between workers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFanOut:
    async def test_words_and_sound_reach_listeners_on_another_worker(
        self, redis_reset, run_id
    ):
        call_worker = aioredis.from_url(constants.REDIS_URL, decode_responses=False)
        listener_worker = aioredis.from_url(constants.REDIS_URL, decode_responses=False)
        texts: list[list[str]] = [[], []]
        sounds: list[list[bytes]] = [[], []]

        def pump(i, audio):
            async def send_text(body):
                texts[i].append(body)

            async def send_bytes(body):
                sounds[i].append(body)

            return ListenerPump(
                run_id,
                send_text=send_text,
                send_bytes=send_bytes,
                audio=audio,
                redis=listener_worker,
            )

        session = await _live(run_id, redis=call_worker)
        pumps = [pump(0, True), pump(1, False)]
        for p in pumps:
            await p.subscribe()
        tasks = [asyncio.create_task(p.read()) for p in pumps] + [
            asyncio.create_task(p.write()) for p in pumps
        ]
        try:
            session.tap.events.put_nowait((live_tap.CALLER, "I want to", False))
            session.tap.events.put_nowait((live_tap.CALLER, "I want to book", True))
            session.tap.events.put_nowait((live_tap.AGENT_TEXT, "Sure"))
            session.tap.events.put_nowait((live_tap.AGENT_DONE,))
            session.tap.audio.put_nowait(
                (live_tap.AUDIO, "c", 8000, 1, b"\x01\x00" * 160)
            )
            session.tap.audio.put_nowait(
                (live_tap.AUDIO, "c", 8000, 1, b"\x02\x00" * 160)
            )

            def lines(i):
                return [
                    json.loads(t)
                    for t in texts[i]
                    if json.loads(t).get("type") == "line"
                ]

            assert await _until(lambda: len(lines(0)) >= 4 and len(lines(1)) >= 4)
            assert await _until(lambda: len(sounds[0]) >= 1)
            for i in (0, 1):
                got = [(e["speaker"], e["text"], e["final"]) for e in lines(i)]
                assert got == [
                    ("caller", "I want to", False),
                    ("caller", "I want to book", True),
                    ("agent", "Sure", False),
                    ("agent", "Sure", True),
                ]
                seqs = [e["seq"] for e in lines(i)]
                assert seqs == sorted(seqs)
            # Two 20 ms frames from the same side became one packet.
            side, rate, chans, pcm = channels.decode_audio(sounds[0][0])
            assert (side, rate, chans) == (b"c", 8000, 1)
            assert pcm == b"\x01\x00" * 160 + b"\x02\x00" * 160
            # The listener who did not ask for sound got none.
            assert sounds[1] == []
            # A late listener reads the same final lines from the backlog.
            backlog = [
                json.loads(raw)
                for raw in await listener_worker.lrange(
                    channels.backlog_key(run_id), 0, -1
                )
            ]
            assert [e["text"] for e in backlog] == ["I want to book", "Sure"]
        finally:
            await session.close()
            assert await _until(
                lambda: (
                    any('"type": "ended"' in t for t in texts[0])
                    and any('"type": "ended"' in t for t in texts[1])
                )
            )
            for t in tasks:
                t.cancel()
            for p in pumps:
                await p.close()
            await call_worker.aclose()
            await listener_worker.aclose()

    async def test_no_audio_listener_means_no_audio_copied(self, redis_reset, run_id):
        session = await _live(run_id)
        try:
            session.tap.audio.put_nowait((live_tap.AUDIO, "c", 8000, 1, b"\x00\x00"))
            assert await _until(lambda: not session.audio_wanted())
        finally:
            await session.close()

    async def test_the_call_is_listed_while_live_and_gone_after(
        self, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        viewer = await access.viewer_for(_user(ADMIN_ID))
        session = await _live(run_id)
        try:
            calls, allowed, more = await registry.live_calls(viewer)
            mine = [c for c in calls if c.run_id == run_id]
            assert allowed and not more and len(mine) == 1
            assert mine[0].step == "Booking"
            assert mine[0].caller == "+919812345678"
        finally:
            await session.close()
        calls, _, _ = await registry.live_calls(viewer)
        assert [c for c in calls if c.run_id == run_id] == []


@pytest.mark.asyncio
class TestSlowListener:
    async def test_audio_beyond_the_buffer_is_dropped_not_held(self):
        stuck = asyncio.Event()

        async def never_returns(_body):
            await stuck.wait()

        pump = ListenerPump(
            1, send_text=never_returns, send_bytes=never_returns, audio=True
        )
        writer = asyncio.create_task(pump.write())
        for _ in range(200):
            pump.offer_audio(b"x" * 100)
        await asyncio.sleep(0.05)
        assert pump.audio_queue.qsize() <= AUDIO_BUFFER
        assert pump.dropped_audio >= 200 - AUDIO_BUFFER - 1
        writer.cancel()


# ---------------------------------------------------------------------------
# Whispers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWhisper:
    async def test_goes_into_the_context_and_never_toward_the_voice(self):
        context = LLMContext([{"role": "system", "content": "You are the front desk."}])
        pair = LLMContextAggregatorPair(context)
        message = whisper.supervisor_message("Priya", "Offer the 5pm slot first.")
        down, _ = await run_test(
            pair.user(),
            frames_to_send=whisper.frames_for(message, urgent=False),
        )
        assert not [f for f in down if isinstance(f, LLMContextFrame)]
        assert context.get_messages()[-1] == message
        assert message["content"].startswith("[supervisor:Priya]")
        assert "never read it out" in message["content"]
        assert not [f for f in down if isinstance(f, (TTSSpeakFrame, TextFrame))]

    async def test_urgent_interrupts_and_has_the_model_answer_now(self):
        message = whisper.supervisor_message(
            "Priya", "Stop: the clinic is closed today."
        )
        frames = whisper.frames_for(message, urgent=True)
        assert isinstance(frames[0], InterruptionFrame)
        assert isinstance(frames[1], LLMMessagesAppendFrame) and frames[1].run_llm
        context = LLMContext()
        pair = LLMContextAggregatorPair(context)
        down, _ = await run_test(
            pair.user(),
            frames_to_send=[frames[1]],
        )
        assert [f for f in down if isinstance(f, LLMContextFrame)]
        assert not [f for f in down if isinstance(f, (TTSSpeakFrame, TextFrame))]

    async def test_not_urgent_waits_for_the_agents_next_turn(self):
        frames = whisper.frames_for(whisper.supervisor_message("A", "x"), urgent=False)
        assert len(frames) == 1 and frames[0].run_llm is False

    async def test_route_to_context_with_audit_and_call_record(
        self, http, flag, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        task, logs = FakeTask(), FakeLogs()
        context = LLMContext([{"role": "system", "content": "Front desk."}])
        session = await _live(run_id, task=task, logs=logs, context=context)
        try:
            response = await _as(http, ADMIN_ID).post(
                f"/api/v1/live-calls/{run_id}/whisper",
                json={"text": "Offer the 5pm slot first.", "urgent": False},
            )
            assert response.status_code == 200, response.text
            assert await _until(lambda: task.queued and logs.events)
            [frame] = task.queued
            assert isinstance(frame, LLMMessagesAppendFrame) and frame.run_llm is False
            content = frame.messages[0]["content"]
            assert content.startswith("[supervisor:user502]")
            assert content.endswith("Offer the 5pm slot first.")
            # Not a frame the voice would speak.
            assert not [
                f for f in task.queued if isinstance(f, (TTSSpeakFrame, TextFrame))
            ]
            # On the call's record, marked as not spoken.
            assert logs.events[0]["type"] == whisper.EVENT_TYPE
            assert logs.events[0]["payload"]["spoken"] is False
            assert logs.events[0]["payload"]["by"] == "user502"
            # And in the audit log: who, which call, what.
            [row] = [r for r in audit if r["action"] == "call_whispered"]
            assert row["organization_id"] == ORG
            assert row["actor_user_id"] == ADMIN_ID
            assert row["subject_id"] == run_id
            assert row["after"]["text"] == "Offer the 5pm slot first."
            # The listen panel's backlog shows it too.
            for _ in range(50):
                backlog = await registry.backlog(run_id)
                if backlog:
                    break
                await asyncio.sleep(0.02)
            assert [e["type"] for e in backlog] == ["whisper"]
        finally:
            await session.close()

    async def test_expires_when_the_call_ends(
        self, http, flag, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        context = LLMContext([{"role": "system", "content": "Front desk."}])
        session = await _live(run_id, task=ContextTask(context), context=context)
        await session.apply_whisper({"text": "Mention the offer.", "by": "Priya"})
        assert any(whisper.is_whisper(m) for m in context.get_messages())
        await session.close()
        # Gone from the context the call leaves behind...
        assert not any(whisper.is_whisper(m) for m in context.get_messages())
        assert context.get_messages() == [{"role": "system", "content": "Front desk."}]
        # ...and nothing more can be sent to a call that has ended.
        assert await session.apply_whisper({"text": "Again", "by": "Priya"}) is False
        response = await _as(http, ADMIN_ID).post(
            f"/api/v1/live-calls/{run_id}/whisper", json={"text": "Too late"}
        )
        assert response.status_code == 409
        assert not [r for r in audit if r["action"] == "call_whispered"]


# ---------------------------------------------------------------------------
# Who may
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPermissions:
    async def test_the_setting_is_off_until_somebody_turns_it_on(
        self, http, flag, audit, redis_reset, run_id, fake_db
    ):
        session = await _live(run_id)
        try:
            listing = (await _as(http, ADMIN_ID).get("/api/v1/live-calls")).json()
            assert listing["allow_listening"] is False
            mine = [c for c in listing["calls"] if c["run_id"] == run_id]
            assert (
                mine[0]["can_listen"] is False and mine[0]["blocked"] == "setting_off"
            )
            response = await http.get(f"/api/v1/live-calls/{run_id}")
            assert response.status_code == 403
            assert "off for this workspace" in response.json()["detail"]

            # A member cannot switch it; an admin can, and it is audited.
            response = await _as(http, MEMBER_ID).put(
                "/api/v1/live-calls/settings", json={"allow_listening": True}
            )
            assert response.status_code == 403
            response = await _as(http, ADMIN_ID).put(
                "/api/v1/live-calls/settings", json={"allow_listening": True}
            )
            assert response.status_code == 200
            [row] = [r for r in audit if r["action"] == "live_listening_setting"]
            assert row["before"] == {"allow_listening": False}
            assert row["after"] == {"allow_listening": True}
            assert (await http.get(f"/api/v1/live-calls/{run_id}")).status_code == 200
        finally:
            await session.close()

    async def test_admins_and_the_agents_owner_only(
        self, http, flag, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        session = await _live(run_id)
        try:
            # A member who does not own the agent sees the call, masked, and
            # cannot open or whisper to it.
            listing = (await _as(http, MEMBER_ID).get("/api/v1/live-calls")).json()
            [call] = [c for c in listing["calls"] if c["run_id"] == run_id]
            assert call["can_listen"] is False and call["blocked"] == "not_permitted"
            assert call["caller"] == "•••• 5678" and call["caller_masked"] is True
            assert listing["can_change_setting"] is False
            assert (await http.get(f"/api/v1/live-calls/{run_id}")).status_code == 403
            response = await http.post(
                f"/api/v1/live-calls/{run_id}/whisper", json={"text": "x"}
            )
            assert response.status_code == 403
            assert not [r for r in audit if r["action"] == "call_whispered"]

            # The agent's owner, a plain member, may.
            listing = (await _as(http, OWNER_ID).get("/api/v1/live-calls")).json()
            [call] = [c for c in listing["calls"] if c["run_id"] == run_id]
            assert call["can_listen"] is True and call["caller"] == "+919812345678"
            detail = await http.get(f"/api/v1/live-calls/{run_id}")
            assert detail.status_code == 200
            assert detail.json()["call"]["step"] == "Booking"
        finally:
            await session.close()

    async def test_another_workspace_gets_a_404(
        self, http, flag, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        session = await _live(run_id)
        try:
            stranger = _as(http, STRANGER_ID, OTHER_ORG)
            assert (
                await stranger.get(f"/api/v1/live-calls/{run_id}")
            ).status_code == 404
            response = await stranger.post(
                f"/api/v1/live-calls/{run_id}/whisper", json={"text": "x"}
            )
            assert response.status_code == 404
            response = await stranger.post(f"/api/v1/live-calls/{run_id}/consent-fix")
            assert response.status_code == 404
            listing = (await stranger.get("/api/v1/live-calls")).json()
            assert [c for c in listing["calls"] if c["run_id"] == run_id] == []
            # Even an entry planted in the other workspace's list is not shown:
            # the run is read back through the viewer's workspace.
            r = channels.redis()
            await r.zadd(channels.org_key(OTHER_ORG), {str(run_id): time.time()})
            listing = (await stranger.get("/api/v1/live-calls")).json()
            assert [c for c in listing["calls"] if c["run_id"] == run_id] == []
            await r.zrem(channels.org_key(OTHER_ORG), str(run_id))
            assert not [r for r in audit if r["action"] == "call_whispered"]
        finally:
            await session.close()

    async def test_an_ended_call_says_so(
        self, http, flag, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        response = await _as(http, ADMIN_ID).get(f"/api/v1/live-calls/{run_id}")
        assert response.status_code == 409


@pytest.mark.asyncio
async def test_listening_is_audited(audit, fake_db, run_id):
    viewer = await access.viewer_for(_user(ADMIN_ID))
    call = SimpleNamespace(run_id=run_id, agent_name="Front desk")
    await registry.note_listening(viewer, call, audio=True)
    [row] = audit
    assert row["action"] == "call_listened" and row["after"] == {"audio": True}


def test_the_socket_is_closed_while_the_flag_is_off(monkeypatch, fake_db):
    from fastapi.testclient import TestClient

    from api.app import app
    from api.services.auth.depends import get_user_ws

    monkeypatch.setattr(constants, "LIVE_SUPERVISION_ENABLED", False)
    app.dependency_overrides[get_user_ws] = lambda: _user(ADMIN_ID)
    try:
        client = TestClient(app)
        with client.websocket_connect("/api/v1/ws/live-calls/1") as ws:
            assert json.loads(ws.receive_text())["detail"] == "Not Found"
    finally:
        app.dependency_overrides.pop(get_user_ws, None)


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


class TestConsent:
    def test_the_default_opening_does_not_mention_monitoring(self):
        definition = {"nodes": [START_NODE]}
        assert consent.mentions_monitoring(definition) is False
        assert consent.notice(definition)["warning"] == consent.WARNING

    def test_a_greeting_that_says_so_is_enough(self):
        node = {
            **START_NODE,
            "data": {**START_NODE["data"], "greeting": "Hi! Calls may be monitored."},
        }
        assert consent.notice({"nodes": [node]}) == {
            "mentions_monitoring": True,
            "warning": None,
        }

    def test_the_proposed_greeting_keeps_what_was_there(self):
        assert consent.proposed_greeting("Hello from Asha Dental") == (
            "Hello from Asha Dental. " + consent.MONITORING_SENTENCE
        )
        assert consent.proposed_greeting("") == consent.MONITORING_SENTENCE


@pytest.mark.asyncio
async def test_the_fix_is_a_card_not_an_edit(
    http, flag, audit, redis_reset, run_id, fake_db, monkeypatch
):
    from datetime import UTC, datetime

    from api.services.workflow import self_edit

    _allow(fake_db)
    asked: list[dict] = []

    async def propose(**kwargs):
        asked.append(kwargs)
        return {"status": "proposed"}

    card = SimpleNamespace(
        id=77,
        at=datetime.now(UTC),
        kind="edit_proposed",
        actor="agent",
        summary="Proposed a change to Start's greeting",
        payload={"greetings": [{"old": "Hello", "new": "Hello. ..."}]},
        is_deliverable=False,
        workflow_id=WORKFLOW_ID,
        workflow_run_id=None,
        folder_id=None,
    )

    async def agent_events(**kwargs):
        assert kwargs["organization_id"] == ORG
        return [card]

    async def get_agent_event(event_id, *, organization_id):
        return card if (event_id, organization_id) == (77, ORG) else None

    monkeypatch.setattr(self_edit, "propose", propose)
    monkeypatch.setattr(db_client, "agent_events", agent_events)
    monkeypatch.setattr(db_client, "get_agent_event", get_agent_event)
    session = await _live(run_id)
    try:
        detail = (await _as(http, OWNER_ID).get(f"/api/v1/live-calls/{run_id}")).json()
        assert detail["consent"]["warning"] == consent.WARNING
        response = await http.post(f"/api/v1/live-calls/{run_id}/consent-fix")
        assert response.status_code == 200, response.text
        assert response.json()["id"] == 77
        [call] = asked
        assert call["organization_id"] == ORG and call["workflow_id"] == WORKFLOW_ID
        assert call["arguments"]["new_greeting"] == (
            "Hello from Asha Dental. " + consent.MONITORING_SENTENCE
        )
        # The greeting itself is untouched: only the card's draft changes it.
        assert fake_db["workflow"].workflow_definition["nodes"][0]["data"][
            "greeting"
        ] == ("Hello from Asha Dental.")
    finally:
        await session.close()
