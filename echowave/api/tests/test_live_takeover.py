"""Live take-over: a supervisor joins a call, barges in or takes over.

Against a real Redis (``REDIS_URL``), with the database replaced by the same
small fakes as the listen-in tests (``test_live_supervision``). The call is
a real pipecat pipeline -- the real user context aggregator, the real gates,
a stand-in model and voice -- so "the agent is silent" is checked where it
matters: no reply asked for, no agent audio reaching the output, the
supervisor's audio reaching it instead. Nothing here talks to Plivo; the
MPC bridge is checked against a recorded fake.
"""

from __future__ import annotations

import asyncio
import json
import struct
import threading
from types import SimpleNamespace

import pytest
import redis.asyncio as aioredis

from api import constants
from api.services.live_supervision import channels as live_channels
from api.services.live_takeover import access as join_access
from api.services.live_takeover import bridge as bridges
from api.services.live_takeover import (
    channels,
    controller,
    plivo_mpc,
    registry,
    speech,
)
from api.services.live_takeover.frames import SupervisorAudioFrame
from api.services.live_takeover.gates import (
    AI,
    BARGE,
    TAKEOVER,
    GateState,
    LLMGate,
    OutputGate,
)
from api.services.pipecat import pipeline_builder
from api.tests import test_live_supervision as listen
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMMessagesAppendFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSTextFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
)
from pipecat.processors.filters.identity_filter import IdentityFilter
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.utils import run_test
from pipecat.workers.runner import WorkerRunner

ORG = listen.ORG
OTHER_ORG = listen.OTHER_ORG
OWNER_ID = listen.OWNER_ID
ADMIN_ID = listen.ADMIN_ID
MEMBER_ID = listen.MEMBER_ID
STRANGER_ID = listen.STRANGER_ID

_as = listen._as
_until = listen._until
_user = listen._user

# The listen-in tests' fixtures: the same workspace, call, audit and client.
fake_db = listen.fake_db
audit = listen.audit
http = listen.http
redis_reset = listen.redis_reset
run_id = listen.run_id


@pytest.fixture
def flags(monkeypatch):
    monkeypatch.setattr(constants, "LIVE_SUPERVISION_ENABLED", True)
    monkeypatch.setattr(constants, "LIVE_TAKEOVER_ENABLED", True)
    monkeypatch.setattr(constants, "LIVE_TAKEOVER_BRIDGE", "pipeline")
    # The call in these tests is on Plivo, and they are about the pipeline
    # bridge carrying a voice; mixing into a phone call is held back by
    # default, which ``TestPhoneCallsByDefault`` checks with this off.
    monkeypatch.setattr(constants, "ALLOW_SERVER_MIXED_PSTN_BARGE", True)


def _allow(state, *, listening: bool = True, joining: bool = True):
    state["settings"][(ORG, listen.access.KEY)] = {"allow_listening": listening}
    state["settings"][(ORG, join_access.KEY)] = {"allow_joining": joining}


def _mic(level: int, samples: int = 320, rate: int = 16000) -> bytes:
    pcm = struct.pack(f"<{samples}h", *([level] * samples))
    return live_channels.encode_audio(channels.SIDE_SUPERVISOR, rate, 1, pcm)


LOUD = 4000
QUIET = 10


# ---------------------------------------------------------------------------
# A call to join: a real pipeline with a stand-in model and voice
# ---------------------------------------------------------------------------


class StandInModel(FrameProcessor):
    """Answers every request for a reply with words and a slice of voice,
    the way the model and the voice together would."""

    def __init__(self):
        super().__init__()
        self.replies = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            self.replies += 1
            await self.push_frame(TTSTextFrame("Sure", aggregated_by="word"))
            await self.push_frame(
                TTSAudioRawFrame(
                    audio=b"\x10\x00" * 160, sample_rate=8000, num_channels=1
                )
            )
            return
        await self.push_frame(frame, direction)


class Speaker(FrameProcessor):
    """Where the output transport would be: what would reach the caller."""

    def __init__(self):
        super().__init__()
        self.frames: list[Frame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            self.frames.append(frame)
        await self.push_frame(frame, direction)

    def of(self, kind) -> list[Frame]:
        return [f for f in self.frames if isinstance(f, kind)]

    def agent_audio(self) -> list[Frame]:
        return [
            f
            for f in self.of(TTSAudioRawFrame)
            if not isinstance(f, SupervisorAudioFrame)
        ]


class Call:
    """One live call on "another worker": the pipeline, its supervision
    session and its take-over controller, each on its own Redis client."""

    def __init__(self, run_id: int, run: object, *, recovery_seconds: float = 30.0):
        self.run_id = run_id
        self.run = run
        self.recovery_seconds = recovery_seconds
        self.context = LLMContext([{"role": "system", "content": "Front desk."}])
        self.model = StandInModel()
        self.speaker = Speaker()
        self.logs = listen.FakeLogs()
        self.queued: list[Frame] = []

    async def __aenter__(self):
        self.redis = aioredis.from_url(constants.REDIS_URL, decode_responses=False)
        self.takeover = controller.TakeoverController(
            run_id=self.run_id,
            organization_id=ORG,
            workflow_run=self.run,
            recovery_seconds=self.recovery_seconds,
            bridge_name="pipeline",
            redis=self.redis,
        )
        pair = LLMContextAggregatorPair(self.context)
        self.pipeline = Pipeline(
            [
                pair.user(),
                self.takeover.llm_gate,
                self.model,
                self.takeover.output_gate,
                self.speaker,
            ]
        )
        self.worker = PipelineWorker(self.pipeline, cancel_on_idle_timeout=False)
        original = self.worker.queue_frames

        async def queue_frames(frames):
            frames = list(frames)
            self.queued.extend(frames)
            await original(frames)

        self.worker.queue_frames = queue_frames  # type: ignore[method-assign]
        self.runner = WorkerRunner(handle_sigint=False)
        await self.runner.add_workers(self.worker)
        self.running = asyncio.create_task(self.runner.run())
        await asyncio.sleep(0.05)
        self.live = await listen._live(
            self.run_id, task=self.worker, logs=self.logs, redis=self.redis
        )
        await self.takeover.attach(self.worker, logs_buffer=self.logs, live=self.live)
        return self

    async def __aexit__(self, *exc):
        await self.takeover.close()
        await self.live.close()
        await self.worker.queue_frame(EndFrame())
        try:
            await asyncio.wait_for(self.running, 5)
        except TimeoutError:
            self.running.cancel()
        await self.redis.aclose()

    async def caller_says(self, text: str) -> None:
        """A finished caller turn, as the aggregator would make it."""
        await self.worker.queue_frames(
            [
                LLMMessagesAppendFrame(
                    messages=[{"role": "user", "content": text}], run_llm=True
                )
            ]
        )

    def events(self, action: str | None = None) -> list[dict]:
        out = [
            e["payload"]
            for e in self.logs.events
            if e["type"] == controller.EVENT_TAKEOVER
        ]
        return [e for e in out if action is None or e["action"] == action]


async def _backlog(run_id: int) -> list[dict]:
    return await listen.registry.backlog(run_id)


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFlagOff:
    @pytest.mark.parametrize(
        "supervision,takeover", [(True, False), (False, True), (False, False)]
    )
    async def test_every_route_is_a_404(
        self, http, run_id, monkeypatch, supervision, takeover
    ):
        monkeypatch.setattr(constants, "LIVE_SUPERVISION_ENABLED", supervision)
        monkeypatch.setattr(constants, "LIVE_TAKEOVER_ENABLED", takeover)
        base = f"/api/v1/live-calls/{run_id}"
        assert (await http.get(f"{base}/takeover")).status_code == 404
        response = await http.post(f"{base}/takeover", json={"mode": "barge"})
        assert response.status_code == 404
        assert (await http.post(f"{base}/let-agent-answer")).status_code == 404
        assert (await http.post(f"{base}/hand-back")).status_code == 404
        response = await http.put(
            "/api/v1/live-calls/join-settings", json={"allow_joining": True}
        )
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "supervision,takeover", [(True, False), (False, True), (False, False)]
    )
    async def test_nothing_is_added_to_the_call(
        self, monkeypatch, supervision, takeover
    ):
        monkeypatch.setattr(constants, "LIVE_SUPERVISION_ENABLED", supervision)
        monkeypatch.setattr(constants, "LIVE_TAKEOVER_ENABLED", takeover)
        prepared = controller.prepare(
            workflow_run=SimpleNamespace(id=1, mode="plivo", call_type="inbound"),
            workflow=SimpleNamespace(id=2, organization_id=ORG, name="A"),
        )
        assert prepared is None

    async def test_a_text_chat_is_never_joined(self, flags):
        prepared = controller.prepare(
            workflow_run=SimpleNamespace(id=1, mode="textchat", call_type="inbound"),
            workflow=SimpleNamespace(id=2, organization_id=ORG, name="A"),
        )
        assert prepared is None

    async def test_on_it_is_prepared(self, flags):
        prepared = controller.prepare(
            workflow_run=SimpleNamespace(id=1, mode="plivo", call_type="inbound"),
            workflow=SimpleNamespace(id=2, organization_id=ORG, name="A"),
        )
        assert isinstance(prepared, controller.TakeoverController)
        assert prepared.state.mode == AI


def _transport():
    output = IdentityFilter()
    return SimpleNamespace(
        input=lambda: IdentityFilter(), output=lambda: output
    ), output


def _built(**gates):
    transport, output = _transport()
    parts = [IdentityFilter() for _ in range(8)]
    stt, audio_buffer, llm, tts, user_agg, asst_agg, callbacks, metrics = parts
    pipeline = pipeline_builder.build_pipeline(
        transport,
        stt,
        audio_buffer,
        llm,
        tts,
        user_agg,
        asst_agg,
        callbacks,
        metrics,
        **gates,
    )
    return pipeline.processors, llm, output


class TestPipelineShape:
    def test_without_the_feature_the_pipeline_is_unchanged(self):
        processors, _llm, _output = _built()
        assert not [p for p in processors if isinstance(p, (LLMGate, OutputGate))]

    def test_the_gates_sit_in_front_of_the_model_and_the_output(self):
        state = GateState()
        llm_gate, output_gate = LLMGate(state), OutputGate(state)
        processors, llm, output = _built(
            takeover_llm_gate=llm_gate, takeover_output_gate=output_gate
        )
        assert processors[processors.index(llm) - 1] is llm_gate
        assert processors[processors.index(output) - 1] is output_gate

    def test_realtime_gets_the_output_gate(self):
        transport, output = _transport()
        gate = OutputGate(GateState())
        pipeline = pipeline_builder.build_realtime_pipeline(
            transport,
            *[IdentityFilter() for _ in range(6)],
            takeover_output_gate=gate,
        )
        processors = pipeline.processors
        assert processors[processors.index(output) - 1] is gate


# ---------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGates:
    async def test_nothing_changes_while_the_agent_has_the_call(self):
        state = GateState()
        frames = [
            LLMContextFrame(LLMContext()),
            TTSTextFrame("Hi", aggregated_by="word"),
            TTSAudioRawFrame(audio=b"\x00\x00" * 80, sample_rate=8000, num_channels=1),
            InterruptionFrame(),
        ]
        down, _ = await run_test(
            Pipeline([LLMGate(state), OutputGate(state)]), frames_to_send=frames
        )
        # A system frame overtakes the others; what matters is that all pass.
        assert sorted(type(f).__name__ for f in down) == sorted(
            type(f).__name__ for f in frames
        )

    async def test_silenced_no_reply_no_voice_but_the_caller_is_still_heard(self):
        state = GateState(mode=TAKEOVER)
        frames = [
            TranscriptionFrame("I still need the slot", "u", "t"),
            LLMContextFrame(LLMContext()),
            TTSTextFrame("Sure", aggregated_by="word"),
            TTSAudioRawFrame(audio=b"\x00\x00" * 80, sample_rate=8000, num_channels=1),
        ]
        down, _ = await run_test(
            Pipeline([LLMGate(state), OutputGate(state)]), frames_to_send=frames
        )
        assert [type(f) for f in down] == [TranscriptionFrame]
        assert state.dropped_replies == 1 and state.dropped_audio == 1

    async def test_a_caller_noise_does_not_cut_the_supervisor_off(self):
        """Interruptions are swallowed while a supervisor has the call, all
        but the one the controller sends on purpose."""
        state = GateState(mode=BARGE)
        on_purpose = InterruptionFrame()
        state.let_through(on_purpose)
        down, _ = await run_test(
            OutputGate(state),
            frames_to_send=[InterruptionFrame(), on_purpose, InterruptionFrame()],
        )
        assert [f.id for f in down] == [on_purpose.id]
        assert state.swallowed_interruptions == 2

    async def test_a_reply_asked_for_upstream_is_dropped_too(self):
        state = GateState(mode=TAKEOVER)
        _, up = await run_test(
            OutputGate(state),
            frames_to_send=[LLMContextFrame(LLMContext())],
            frames_to_send_direction=FrameDirection.UPSTREAM,
        )
        assert not [f for f in up if isinstance(f, LLMContextFrame)]

    async def test_let_answer_opens_both_gates(self):
        state = GateState(mode=BARGE, ai_may_speak=True)
        frames = [
            LLMContextFrame(LLMContext()),
            TTSAudioRawFrame(audio=b"\x00\x00" * 80, sample_rate=8000, num_channels=1),
            InterruptionFrame(),
        ]
        down, _ = await run_test(
            Pipeline([LLMGate(state), OutputGate(state)]), frames_to_send=frames
        )
        assert sorted(type(f).__name__ for f in down) == sorted(
            type(f).__name__ for f in frames
        )


# ---------------------------------------------------------------------------
# Barge, take-over, hand-back: the API to the call and back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBargeAndHandBack:
    async def test_barge_pauses_the_voice_and_hand_back_resumes_it(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]) as call:
            # The agent answers as usual.
            await call.caller_says("Is Friday free?")
            assert await _until(lambda: len(call.speaker.agent_audio()) == 1)

            response = await _as(http, ADMIN_ID).post(
                f"/api/v1/live-calls/{run_id}/takeover", json={"mode": "barge"}
            )
            assert response.status_code == 200, response.text
            assert response.json()["mode"] == "barge"
            assert await _until(lambda: call.takeover.state.mode == BARGE)
            # The agent was cut off on the spot, through its own gate.
            interruptions = [f for f in call.queued if isinstance(f, InterruptionFrame)]
            assert len(interruptions) == 1
            assert call.takeover.state.passes(interruptions[0])

            # The caller speaks: the words go into the agent's context (it
            # is still listening), but no reply is asked for or heard.
            replies = call.model.replies
            await call.caller_says("Hello? Who is this?")
            await asyncio.sleep(0.2)
            assert call.model.replies == replies
            assert len(call.speaker.agent_audio()) == 1
            assert any(
                m.get("content") == "Hello? Who is this?"
                for m in call.context.get_messages()
            )

            # The supervisor speaks: the caller hears them.
            for _ in range(5):
                assert await registry.forward_mic(run_id, _mic(LOUD))
            assert await _until(lambda: len(call.speaker.of(SupervisorAudioFrame)) == 5)
            assert len(call.speaker.agent_audio()) == 1

            # Hand back: the agent picks the call up with a line.
            response = await http.post(f"/api/v1/live-calls/{run_id}/hand-back")
            assert response.status_code == 200, response.text
            assert await _until(lambda: call.takeover.state.mode == AI)
            assert await _until(lambda: len(call.speaker.agent_audio()) == 2)
            note = call.context.get_messages()[-1]["content"]
            assert note.startswith("[supervisor-handback]")
            assert "user502" in note

            # Recorded on the call and in the audit log.
            assert [e["action"] for e in call.events()] == [
                controller.JOINED,
                controller.HANDED_BACK,
            ]
            assert call.events()[0]["by"] == "user502"
            actions = [r["action"] for r in audit]
            assert "call_barged" in actions and "call_handed_back" in actions
            [barged] = [r for r in audit if r["action"] == "call_barged"]
            assert barged["actor_user_id"] == ADMIN_ID
            assert barged["subject_id"] == run_id
            assert barged["after"]["mode"] == "barge"
            assert await registry.read_state(run_id) is None

            # The listen panel's backlog has both, in order with the words.
            backlog = await _backlog(run_id)
            kinds = [(e["type"], e.get("action")) for e in backlog]
            assert ("takeover", "joined") in kinds and (
                "takeover",
                "handed_back",
            ) in kinds
            # And after the hand-back, the caller is answered as before.
            await call.caller_says("Thanks")
            assert await _until(lambda: len(call.speaker.agent_audio()) == 3)

    async def test_let_the_agent_answer_until_the_supervisor_speaks(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]) as call:
            await _as(http, ADMIN_ID).post(
                f"/api/v1/live-calls/{run_id}/takeover", json={"mode": "barge"}
            )
            assert await _until(lambda: call.takeover.state.mode == BARGE)
            response = await http.post(f"/api/v1/live-calls/{run_id}/let-agent-answer")
            assert response.status_code == 204, response.text
            # The agent answers now.
            assert await _until(lambda: len(call.speaker.agent_audio()) == 1)
            assert call.takeover.state.ai_may_speak is True
            assert call.context.get_messages()[-1]["content"].startswith(
                "[supervisor-answer]"
            )
            # Quiet from the supervisor does not stop it...
            await registry.forward_mic(run_id, _mic(QUIET))
            await asyncio.sleep(0.2)
            assert call.takeover.state.ai_may_speak is True
            # ...their voice does, and cuts it off.
            before = len([f for f in call.queued if isinstance(f, InterruptionFrame)])
            await registry.forward_mic(run_id, _mic(LOUD))
            assert await _until(lambda: call.takeover.state.ai_may_speak is False)
            after = [f for f in call.queued if isinstance(f, InterruptionFrame)]
            assert len(after) == before + 1
            await call.caller_says("Are you there?")
            await asyncio.sleep(0.2)
            assert len(call.speaker.agent_audio()) == 1
            assert [e["action"] for e in call.events()] == [
                controller.JOINED,
                controller.AGENT_ANSWERING,
                controller.AGENT_PAUSED,
            ]

    async def test_a_stretch_of_speech_is_one_labelled_span(
        self, http, flags, audit, redis_reset, run_id, fake_db, monkeypatch
    ):
        monkeypatch.setattr(controller, "SPEECH_HANGOVER_SECONDS", 0.3)
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]) as call:
            await _as(http, ADMIN_ID).post(
                f"/api/v1/live-calls/{run_id}/takeover", json={"mode": "takeover"}
            )
            assert await _until(lambda: call.takeover.state.mode == TAKEOVER)
            for _ in range(3):
                await registry.forward_mic(run_id, _mic(LOUD))
            assert await _until(lambda: call.takeover.speech is not None)
            assert await _until(
                lambda: any(
                    e["type"] == controller.EVENT_SPEECH for e in call.logs.events
                ),
                timeout=3.0,
            )
            [span] = [
                e["payload"]
                for e in call.logs.events
                if e["type"] == controller.EVENT_SPEECH
            ]
            assert span["by"] == "user502"
            assert span["spoken"] is True and span["transcribed"] is False
            backlog = await _backlog(run_id)
            spans = [e for e in backlog if e["type"] == "supervisor"]
            assert len(spans) == 1 and spans[0]["final"] is True

    async def test_take_over_keeps_the_agent_quiet_and_can_switch(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]) as call:
            base = f"/api/v1/live-calls/{run_id}"
            response = await _as(http, ADMIN_ID).post(
                f"{base}/takeover", json={"mode": "takeover"}
            )
            assert response.status_code == 200
            assert await _until(lambda: call.takeover.state.mode == TAKEOVER)
            # No "let the agent answer" in a take-over.
            response = await http.post(f"{base}/let-agent-answer")
            assert response.status_code == 409
            for text in ("One", "Two", "Three"):
                await call.caller_says(text)
            await asyncio.sleep(0.3)
            assert call.model.replies == 0
            assert call.speaker.agent_audio() == []
            # The state the panel reads.
            state = (await http.get(f"{base}/takeover")).json()
            assert state["mode"] == "takeover" and state["mine"] is True
            assert state["by"] == "user502" and state["bridge"] == "pipeline"
            # Switch to a barge: the same person, audited again.
            response = await http.post(f"{base}/takeover", json={"mode": "barge"})
            assert response.status_code == 200
            assert await _until(lambda: call.takeover.state.mode == BARGE)
            assert [e["action"] for e in call.events()] == ["joined", "switched"]
            actions = [r["action"] for r in audit]
            assert actions.count("call_taken_over") == 1
            assert actions.count("call_barged") == 1

    async def test_the_idle_timer_stands_down_while_a_supervisor_has_the_call(
        self, flags, redis_reset, run_id, fake_db
    ):
        async with Call(run_id, fake_db["run"]) as call:
            assert call.takeover.holds_the_call is False
            await call.takeover.join(TAKEOVER, by="Priya", by_user_id=ADMIN_ID)
            assert call.takeover.holds_the_call is True


# ---------------------------------------------------------------------------
# A supervisor who drops off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRecovery:
    async def test_the_agent_takes_the_call_back_after_n_seconds(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        async with Call(run_id, fake_db["run"], recovery_seconds=0.6) as call:
            await _as(http, ADMIN_ID).post(
                f"/api/v1/live-calls/{run_id}/takeover", json={"mode": "takeover"}
            )
            assert await _until(lambda: call.takeover.state.mode == TAKEOVER)
            # Present for a while: pings and audio keep the call theirs.
            viewer = await listen.access.viewer_for(_user(ADMIN_ID))
            for _ in range(6):
                await registry.ping(viewer, run_id)
                await asyncio.sleep(0.2)
            assert call.takeover.state.mode == TAKEOVER
            # Then nothing: the browser is gone.
            assert await _until(lambda: call.takeover.state.mode == AI, timeout=3.0)
            # The agent speaks first, with the recovery line.
            assert await _until(lambda: len(call.speaker.agent_audio()) == 1)
            note = call.context.get_messages()[-1]["content"]
            assert note.startswith("[supervisor-recovery]")
            assert "apologise" in note
            [event] = call.events(controller.RECOVERED)
            assert event["supervisor"] == "user502"
            [row] = [r for r in audit if r["action"] == "call_takeover_recovered"]
            assert row["subject_id"] == run_id and row["organization_id"] == ORG
            assert await registry.read_state(run_id) is None
            # The panel can join again.
            state = (await http.get(f"/api/v1/live-calls/{run_id}/takeover")).json()
            assert state["mode"] == "ai" and state["can_join"] is True

    async def test_another_persons_ping_does_not_keep_the_call(
        self, flags, audit, redis_reset, run_id, fake_db
    ):
        async with Call(run_id, fake_db["run"], recovery_seconds=0.5) as call:
            await call.takeover.join(BARGE, by="Priya", by_user_id=ADMIN_ID)
            for _ in range(5):
                await call.takeover.on_command({"kind": "ping", "by_user_id": OWNER_ID})
                await asyncio.sleep(0.2)
            assert await _until(lambda: call.takeover.state.mode == AI, timeout=2.0)

    async def test_a_call_ending_with_a_supervisor_on_it_clears_the_claim(
        self, flags, redis_reset, run_id, fake_db
    ):
        async with Call(run_id, fake_db["run"]) as call:
            await call.takeover.join(TAKEOVER, by="Priya", by_user_id=ADMIN_ID)
            assert await registry.read_state(run_id) is not None
        assert await registry.read_state(run_id) is None
        assert call.takeover.state.mode == AI


def test_the_talking_socket_end_to_end(
    flags, audit, redis_reset, run_id, fake_db, monkeypatch
):
    """The browser's side: open the socket, send the microphone, close it;
    the caller hears the supervisor, and when the socket goes the agent
    takes the call back. The socket runs in the test client's own thread
    and event loop, the call in this one."""
    from fastapi.testclient import TestClient

    from api.app import app
    from api.services.auth.depends import get_user_ws

    _allow(fake_db)
    clients: dict = {}

    def per_loop():
        loop = asyncio.get_running_loop()
        if loop not in clients:
            clients[loop] = aioredis.from_url(
                constants.REDIS_URL, decode_responses=False
            )
        return clients[loop]

    monkeypatch.setattr(live_channels, "redis", per_loop)
    app.dependency_overrides[get_user_ws] = lambda: _user(ADMIN_ID)
    seen: dict = {}

    def browser(opened: threading.Event, close: threading.Event):
        client = TestClient(app)
        with client.websocket_connect(f"/api/v1/ws/live-calls/{run_id}/talk") as ws:
            seen["hello"] = json.loads(ws.receive_text())
            for _ in range(5):
                ws.send_bytes(_mic(LOUD))
            ws.send_bytes(b"not audio")
            opened.set()
            close.wait(5)

    async def scenario():
        async with Call(run_id, fake_db["run"], recovery_seconds=1.0) as call:
            viewer = await listen.access.viewer_for(_user(ADMIN_ID))
            await registry.join(viewer, run_id, mode=TAKEOVER)
            assert await _until(lambda: call.takeover.state.mode == TAKEOVER)
            opened, close = threading.Event(), threading.Event()
            thread = threading.Thread(target=browser, args=(opened, close))
            thread.start()
            assert await asyncio.to_thread(opened.wait, 5)
            assert await _until(lambda: len(call.speaker.of(SupervisorAudioFrame)) == 5)
            # Held open past the recovery window: the socket's pings keep it.
            await asyncio.sleep(1.6)
            assert call.takeover.state.mode == TAKEOVER
            close.set()
            await asyncio.to_thread(thread.join, 5)
            assert await _until(lambda: call.takeover.state.mode == AI, timeout=4.0)
            assert call.events(controller.RECOVERED)
            # The malformed packet never reached the call.
            assert len(call.speaker.of(SupervisorAudioFrame)) == 5

    try:
        asyncio.run(scenario())
    finally:
        app.dependency_overrides.pop(get_user_ws, None)
        for client in clients.values():
            try:
                asyncio.run(client.aclose())
            except Exception:  # noqa: BLE001 - its loop is gone
                pass
    assert seen["hello"] == {"type": "live"}


def test_the_talking_socket_is_closed_while_the_flag_is_off(monkeypatch, fake_db):
    from fastapi.testclient import TestClient

    from api.app import app
    from api.services.auth.depends import get_user_ws

    monkeypatch.setattr(constants, "LIVE_SUPERVISION_ENABLED", True)
    monkeypatch.setattr(constants, "LIVE_TAKEOVER_ENABLED", False)
    app.dependency_overrides[get_user_ws] = lambda: _user(ADMIN_ID)
    try:
        client = TestClient(app)
        with client.websocket_connect("/api/v1/ws/live-calls/1/talk") as ws:
            assert json.loads(ws.receive_text())["detail"] == "Not Found"
    finally:
        app.dependency_overrides.pop(get_user_ws, None)


def test_the_talking_socket_needs_a_join_first(
    flags, monkeypatch, fake_db, run_id, redis_reset
):
    from fastapi.testclient import TestClient

    from api.app import app
    from api.services.auth.depends import get_user_ws

    _allow(fake_db)

    async def live():
        r = aioredis.from_url(constants.REDIS_URL, decode_responses=False)
        await r.set(
            live_channels.meta_key(run_id),
            json.dumps({"run_id": run_id, "organization_id": ORG, "workflow_id": 31}),
            ex=20,
        )
        await r.aclose()

    asyncio.run(live())
    live_channels.reset()
    app.dependency_overrides[get_user_ws] = lambda: _user(ADMIN_ID)
    try:
        client = TestClient(app)
        with client.websocket_connect(f"/api/v1/ws/live-calls/{run_id}/talk") as ws:
            message = json.loads(ws.receive_text())
            assert message == {"type": "error", "detail": "Join the call first."}
    finally:
        app.dependency_overrides.pop(get_user_ws, None)
        live_channels.reset()


# ---------------------------------------------------------------------------
# Who may
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPermissions:
    async def test_joining_is_off_until_an_admin_turns_it_on(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db, joining=False)
        fake_db["settings"].pop((ORG, join_access.KEY))
        async with Call(run_id, fake_db["run"]) as call:
            base = f"/api/v1/live-calls/{run_id}"
            state = (await _as(http, ADMIN_ID).get(f"{base}/takeover")).json()
            assert state["allow_joining"] is False
            assert state["can_join"] is False and state["blocked"] == "joining_off"
            assert state["can_change_setting"] is True
            response = await http.post(f"{base}/takeover", json={"mode": "barge"})
            assert response.status_code == 403
            assert "Joining calls is off" in response.json()["detail"]
            assert call.takeover.state.mode == AI
            # A member cannot switch it; an admin can, and it is audited.
            response = await _as(http, OWNER_ID).put(
                "/api/v1/live-calls/join-settings", json={"allow_joining": True}
            )
            assert response.status_code == 403
            response = await _as(http, ADMIN_ID).put(
                "/api/v1/live-calls/join-settings", json={"allow_joining": True}
            )
            assert response.status_code == 200
            [row] = [r for r in audit if r["action"] == "live_joining_setting"]
            assert row["before"] == {"allow_joining": False}
            assert row["after"] == {"allow_joining": True}
            response = await http.post(f"{base}/takeover", json={"mode": "barge"})
            assert response.status_code == 200

    async def test_listening_off_means_joining_off(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db, listening=False)
        async with Call(run_id, fake_db["run"]):
            response = await _as(http, ADMIN_ID).post(
                f"/api/v1/live-calls/{run_id}/takeover", json={"mode": "barge"}
            )
            assert response.status_code == 403
            assert "Live listening is off" in response.json()["detail"]

    async def test_admins_and_the_agents_owner_only(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]) as call:
            base = f"/api/v1/live-calls/{run_id}"
            response = await _as(http, MEMBER_ID).post(
                f"{base}/takeover", json={"mode": "takeover"}
            )
            assert response.status_code == 403
            assert "owner or a workspace admin" in response.json()["detail"]
            response = await _as(http, OWNER_ID).post(
                f"{base}/takeover", json={"mode": "takeover"}
            )
            assert response.status_code == 200
            assert await _until(lambda: call.takeover.state.mode == TAKEOVER)

    async def test_one_supervisor_at_a_time(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]) as call:
            base = f"/api/v1/live-calls/{run_id}"
            await _as(http, OWNER_ID).post(f"{base}/takeover", json={"mode": "barge"})
            assert await _until(lambda: call.takeover.state.mode == BARGE)
            response = await _as(http, ADMIN_ID).post(
                f"{base}/takeover", json={"mode": "takeover"}
            )
            assert response.status_code == 409
            assert response.json()["detail"] == "user501 is already on this call."
            state = (await http.get(f"{base}/takeover")).json()
            assert state["blocked"] == "taken" and state["mine"] is False
            assert not [r for r in audit if r["action"] == "call_taken_over"]
            # An admin can still hand a call back for somebody who walked away.
            response = await http.post(f"{base}/hand-back")
            assert response.status_code == 200
            assert await _until(lambda: call.takeover.state.mode == AI)
            [row] = [r for r in audit if r["action"] == "call_handed_back"]
            assert row["actor_user_id"] == ADMIN_ID
            assert row["before"]["by"] == "user501"

    async def test_the_owner_cannot_hand_back_someone_elses_call(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]) as call:
            base = f"/api/v1/live-calls/{run_id}"
            await _as(http, ADMIN_ID).post(f"{base}/takeover", json={"mode": "barge"})
            assert await _until(lambda: call.takeover.state.mode == BARGE)
            response = await _as(http, OWNER_ID).post(f"{base}/hand-back")
            assert response.status_code == 409
            assert call.takeover.state.mode == BARGE

    async def test_another_workspace_gets_a_404(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]) as call:
            base = f"/api/v1/live-calls/{run_id}"
            stranger = _as(http, STRANGER_ID, OTHER_ORG)
            assert (await stranger.get(f"{base}/takeover")).status_code == 404
            response = await stranger.post(f"{base}/takeover", json={"mode": "barge"})
            assert response.status_code == 404
            assert (await stranger.post(f"{base}/hand-back")).status_code == 404
            assert call.takeover.state.mode == AI
            assert await registry.read_state(run_id) is None

    async def test_an_ended_call_is_a_409(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        base = f"/api/v1/live-calls/{run_id}"
        # Never live on any worker:
        response = await _as(http, ADMIN_ID).post(
            f"{base}/takeover", json={"mode": "barge"}
        )
        assert response.status_code == 409
        # Live, then finished:
        async with Call(run_id, fake_db["run"]):
            fake_db["run"].is_completed = True
            response = await http.post(f"{base}/takeover", json={"mode": "barge"})
            assert response.status_code == 409
            assert response.json()["detail"] == "This call has ended."
            assert (await http.post(f"{base}/hand-back")).status_code == 409
        assert not [r for r in audit if r["action"].startswith("call_")]
        assert await registry.read_state(run_id) is None

    async def test_a_call_started_before_the_feature_cannot_be_joined(
        self, http, flags, audit, redis_reset, run_id, fake_db
    ):
        """Live and listened to, but no controller on its worker: the join
        reaches nobody, is withdrawn and is not audited."""
        _allow(fake_db)
        session = await listen._live(run_id)
        try:
            response = await _as(http, ADMIN_ID).post(
                f"/api/v1/live-calls/{run_id}/takeover", json={"mode": "barge"}
            )
            assert response.status_code == 409
            assert await registry.read_state(run_id) is None
            assert not [r for r in audit if r["action"] == "call_barged"]
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# The Plivo MPC bridge (a recorded fake; no Plivo)
# ---------------------------------------------------------------------------


class Recorder:
    def __init__(self, supervisor_call="sup-call-1"):
        self.sent: list[tuple[str, str, dict | None]] = []
        self.supervisor_call = supervisor_call

    async def __call__(self, method, url, body):
        self.sent.append((method, url, body))
        if method == "POST" and url.endswith("/name_mpc-41/"):
            return 201, {"calls": [{"call_uuid": self.supervisor_call}]}
        if method == "GET":
            return 200, {
                "objects": [
                    {"member_id": "7", "call_uuid": "agent-call"},
                    {"member_id": "9", "call_uuid": self.supervisor_call},
                ]
            }
        return 204, None


@pytest.mark.asyncio
class TestPlivoBridge:
    def _bridge(self, recorder):
        return plivo_mpc.PlivoMPCBridge(
            auth_id="AUTH",
            mpc_name="mpc-41",
            agent_member_id="7",
            caller_id="+918000000000",
            send=recorder,
        )

    async def test_barge_dials_the_supervisor_heard_by_the_caller(self):
        recorder = Recorder()
        bridge = self._bridge(recorder)
        await bridge.join(
            BARGE, bridges.SupervisorLeg("Priya", 1, phone="+919800000001")
        )
        await bridge.set_agent_muted(True)
        base = "https://api.plivo.com/v1/Account/AUTH/MultiPartyCall/name_mpc-41"
        assert recorder.sent == [
            (
                "POST",
                f"{base}/",
                {
                    "role": "Supervisor",
                    "from": "+918000000000",
                    "to": "+919800000001",
                    "coach_mode": False,
                },
            ),
            ("POST", f"{base}/Participant/7/", {"mute": True}),
        ]

    async def test_coach_mode_and_leaving(self):
        recorder = Recorder()
        bridge = self._bridge(recorder)
        await bridge.join(
            BARGE, bridges.SupervisorLeg("Priya", 1, phone="+919800000001")
        )
        await bridge.set_mode(plivo_mpc.COACH)
        await bridge.leave()
        base = "https://api.plivo.com/v1/Account/AUTH/MultiPartyCall/name_mpc-41"
        assert recorder.sent[1:] == [
            ("GET", f"{base}/Participant/", None),
            ("POST", f"{base}/Participant/9/", {"coach_mode": True}),
            ("DELETE", f"{base}/Participant/9/", None),
            ("POST", f"{base}/Participant/7/", {"mute": False}),
        ]

    async def test_no_phone_no_join(self):
        bridge = self._bridge(Recorder())
        with pytest.raises(bridges.BridgeUnavailable):
            await bridge.join(TAKEOVER, bridges.SupervisorLeg("Priya", 1))

    async def test_a_call_not_in_an_mpc_is_refused(self):
        run = SimpleNamespace(gathered_context={}, initial_context={})
        with pytest.raises(bridges.BridgeUnavailable):
            await plivo_mpc.for_run(run, ORG)

    async def test_a_refused_join_leaves_the_agent_with_the_call(
        self, flags, redis_reset, run_id, fake_db
    ):
        fake_db["run"].gathered_context = {}
        async with Call(run_id, fake_db["run"]) as call:
            call.takeover.bridge_name = bridges.PLIVO_MPC
            joined = await call.takeover.join(
                TAKEOVER, by="Priya", by_user_id=ADMIN_ID, phone="+919800000001"
            )
            assert joined is False
            assert call.takeover.state.mode == AI
            [failed] = call.events(controller.FAILED)
            assert "can't be joined by phone" in failed["detail"]
            await call.caller_says("Hello?")
            assert await _until(lambda: len(call.speaker.agent_audio()) == 1)

    async def test_the_api_asks_for_a_phone_when_joining_by_phone(
        self, http, flags, audit, redis_reset, run_id, fake_db, monkeypatch
    ):
        monkeypatch.setattr(constants, "LIVE_TAKEOVER_BRIDGE", "plivo_mpc")
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]):
            base = f"/api/v1/live-calls/{run_id}"
            state = (await _as(http, ADMIN_ID).get(f"{base}/takeover")).json()
            assert state["bridge"] == "plivo_mpc" and state["needs_phone"] is True
            response = await http.post(f"{base}/takeover", json={"mode": "barge"})
            assert response.status_code == 409
            assert "phone number" in response.json()["detail"]


# ---------------------------------------------------------------------------
# The listen tap and the microphone format
# ---------------------------------------------------------------------------


class TestWire:
    def test_mic_packets_are_checked(self):
        assert channels.valid_mic_packet(_mic(LOUD))
        assert not channels.valid_mic_packet(b"not audio")
        bad_side = live_channels.encode_audio(b"a", 16000, 1, b"\x00\x00")
        assert not channels.valid_mic_packet(bad_side)
        bad_rate = live_channels.encode_audio(b"s", 11025, 1, b"\x00\x00")
        assert not channels.valid_mic_packet(bad_rate)
        stereo = live_channels.encode_audio(b"s", 16000, 2, b"\x00\x00\x00\x00")
        assert not channels.valid_mic_packet(stereo)
        odd = live_channels.encode_audio(b"s", 16000, 1, b"\x00\x00\x00")
        assert not channels.valid_mic_packet(odd)

    def test_loudness(self):
        assert controller.rms(_mic(LOUD)[6:]) == pytest.approx(LOUD)
        assert controller.rms(b"") == 0.0


@pytest.mark.asyncio
async def test_the_tap_labels_the_supervisor_as_their_own_side():
    from api.services.live_supervision import tap as live_tap
    from pipecat.transports.base_output import BaseOutputTransport
    from pipecat.transports.base_transport import TransportParams

    tap = live_tap.LiveCallTap()
    output = BaseOutputTransport(TransportParams())
    frame = SupervisorAudioFrame(
        audio=b"\x01\x00" * 10, sample_rate=16000, num_channels=1
    )
    await tap.on_push_frame(listen._pushed(frame, destination=output))
    assert tap.audio.get_nowait()[1] == "s"


# ---------------------------------------------------------------------------
# Phone calls: no browser audio mixed in on our servers, by default
# ---------------------------------------------------------------------------


@pytest.fixture
def pstn_default(monkeypatch):
    """The shipped default: mixing into phone calls held back."""
    monkeypatch.setattr(constants, "ALLOW_SERVER_MIXED_PSTN_BARGE", False)


def test_the_default_holds_server_mixing_into_phone_calls_back():
    import ast
    from pathlib import Path

    # Read from the source, not the module: other tests switch it.
    tree = ast.parse(Path(constants.__file__).read_text())
    [value] = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            getattr(t, "id", None) == "ALLOW_SERVER_MIXED_PSTN_BARGE"
            for t in node.targets
        )
    ]
    assert isinstance(value, ast.Constant) and value.value is False


@pytest.mark.asyncio
class TestPhoneCallsByDefault:
    async def test_barge_on_a_phone_call_is_refused_and_says_why(
        self, http, flags, pstn_default, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]) as call:
            base = f"/api/v1/live-calls/{run_id}"
            state = (await _as(http, ADMIN_ID).get(f"{base}/takeover")).json()
            assert state["bridge"] == "pipeline"
            assert state["voice"] is False and state["can_barge"] is False
            assert state["notice"] == bridges.PSTN_VOICE_OFF
            assert state["can_join"] is True
            response = await http.post(f"{base}/takeover", json={"mode": "barge"})
            assert response.status_code == 409
            assert response.json()["detail"] == bridges.PSTN_VOICE_OFF
            # Nothing was claimed and the agent still has the call.
            assert await registry.read_state(run_id) is None
            assert call.takeover.state.mode == AI
            assert not [r for r in audit if r["action"] == "call_barged"]

    async def test_take_over_on_a_phone_call_is_silent_typed_and_handed_back(
        self, http, flags, pstn_default, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]) as call:
            base = f"/api/v1/live-calls/{run_id}"
            client = _as(http, ADMIN_ID)
            response = await client.post(f"{base}/takeover", json={"mode": "takeover"})
            assert response.status_code == 200, response.text
            assert await _until(lambda: call.takeover.state.mode == TAKEOVER)
            assert call.takeover.bridge.browser_audio is False
            # The browser's microphone is never played into the phone call.
            for _ in range(3):
                await registry.forward_mic(run_id, _mic(LOUD))
            await asyncio.sleep(0.3)
            assert call.speaker.of(SupervisorAudioFrame) == []
            assert call.takeover.played_frames == 0
            # The agent is silent, and the supervisor guides it by typing.
            await call.caller_says("Hello?")
            await asyncio.sleep(0.2)
            assert call.model.replies == 0
            response = await client.post(
                f"{base}/whisper", json={"text": "Offer the 4pm slot", "urgent": False}
            )
            assert response.status_code == 200, response.text
            assert await _until(lambda: call.live.whispers_applied == 1)
            # No switching to a barge either.
            response = await client.post(f"{base}/takeover", json={"mode": "barge"})
            assert response.status_code == 409
            assert response.json()["detail"] == bridges.PSTN_VOICE_OFF
            assert call.takeover.state.mode == TAKEOVER
            # Hand back: the agent picks the call up, the instruction in hand.
            response = await client.post(f"{base}/hand-back")
            assert response.status_code == 200
            assert await _until(lambda: call.takeover.state.mode == AI)
            assert await _until(lambda: len(call.speaker.agent_audio()) == 1)
            contents = [m["content"] for m in call.context.get_messages()]
            assert any("Offer the 4pm slot" in c for c in contents)
            [joined] = [r for r in audit if r["action"] == "call_taken_over"]
            assert joined["after"]["voice"] is False

    async def test_the_worker_refuses_a_barge_on_a_phone_call_too(
        self, flags, pstn_default, redis_reset, run_id, fake_db
    ):
        async with Call(run_id, fake_db["run"]) as call:
            joined = await call.takeover.join(BARGE, by="Priya", by_user_id=ADMIN_ID)
            assert joined is False
            assert call.takeover.state.mode == AI
            [failed] = call.events(controller.FAILED)
            assert failed["detail"] == bridges.PSTN_VOICE_OFF
            # A take-over is accepted, and a switch to barge is not.
            assert await call.takeover.join(TAKEOVER, by="Priya", by_user_id=ADMIN_ID)
            assert not await call.takeover.join(BARGE, by="Priya", by_user_id=ADMIN_ID)
            assert call.takeover.state.mode == TAKEOVER

    async def test_a_web_call_keeps_the_browser_voice(
        self, http, flags, pstn_default, audit, redis_reset, run_id, fake_db
    ):
        _allow(fake_db)
        fake_db["run"].mode = "webrtc"
        async with Call(run_id, fake_db["run"]) as call:
            call.live.direction = "web"
            await call.live._beat()
            base = f"/api/v1/live-calls/{run_id}"
            state = (await _as(http, ADMIN_ID).get(f"{base}/takeover")).json()
            assert state["voice"] is True and state["notice"] is None
            response = await http.post(f"{base}/takeover", json={"mode": "barge"})
            assert response.status_code == 200, response.text
            assert await _until(lambda: call.takeover.state.mode == BARGE)
            for _ in range(3):
                await registry.forward_mic(run_id, _mic(LOUD))
            assert await _until(lambda: len(call.speaker.of(SupervisorAudioFrame)) == 3)

    async def test_the_phone_conference_says_it_cannot_reach_todays_calls(
        self,
        http,
        flags,
        pstn_default,
        audit,
        redis_reset,
        run_id,
        fake_db,
        monkeypatch,
    ):
        monkeypatch.setattr(constants, "LIVE_TAKEOVER_BRIDGE", "plivo_mpc")
        _allow(fake_db)
        async with Call(run_id, fake_db["run"]):
            base = f"/api/v1/live-calls/{run_id}"
            state = (await _as(http, ADMIN_ID).get(f"{base}/takeover")).json()
            assert state["needs_phone"] is True
            assert state["notice"] == bridges.MPC_NOT_YET

    async def test_make_gives_a_voiceless_pipeline_bridge_on_a_phone_call(
        self, pstn_default
    ):
        async def made(mode: str):
            return await bridges.make(
                bridges.PIPELINE,
                workflow_run=SimpleNamespace(mode=mode),
                organization_id=ORG,
            )

        assert (await made("plivo")).browser_audio is False
        assert (await made("smallwebrtc")).browser_audio is True
        assert (await made("webrtc")).browser_audio is True
        # A mode nobody listed counts as a phone call: held back, not mixed.
        assert (await made("some-new-carrier")).browser_audio is False


# ---------------------------------------------------------------------------
# The supervisor's words: transcribed, labelled, and "speak when addressed"
# ---------------------------------------------------------------------------


class ScriptedSTT(FrameProcessor):
    """Stands in for the call's transcriber: hears audio, and at the end of
    each stretch of speech says the next scripted line (interim, then final)."""

    def __init__(self, lines: list[str]):
        super().__init__()
        self.lines = list(lines)
        self.audio = 0
        self.rates: set[int] = set()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            self.audio += 1
            self.rates.add(frame.sample_rate)
            return
        if isinstance(frame, VADUserStoppedSpeakingFrame) and self.lines:
            line = self.lines.pop(0)
            half = " ".join(line.split()[:2])
            await self.push_frame(InterimTranscriptionFrame(half, "supervisor", "t"))
            await self.push_frame(TranscriptionFrame(line, "supervisor", "t"))
            return
        await self.push_frame(frame, direction)


class TestAddressed:
    NAMES = ("asha front desk", "asha")

    @pytest.mark.parametrize(
        "line",
        [
            "Asha, can you confirm the time?",
            "Asha what's the refund window",
            "What is the refund window, Asha?",
            "Okay Asha, go ahead",
            "AI, answer that please",
            "Assistant, read the order back.",
            "Can Asha check the refund?",
        ],
    )
    def test_said_to_the_agent(self, line):
        assert speech.addressed(line, self.NAMES)

    @pytest.mark.parametrize(
        "line",
        [
            "I'll check with Asha later",
            "Let me check your booking.",
            "Sorry, the AI will call you back",
            "Can you tell me your order number?",
            "Thanks for waiting",
            "",
        ],
    )
    def test_said_to_the_caller(self, line):
        assert not speech.addressed(line, self.NAMES)

    def test_names(self):
        assert speech.names_for("Asha Front Desk") == ("asha front desk", "asha")
        # An ordinary first word is not listened for on its own.
        assert speech.names_for("Front desk") == ("front desk",)
        assert speech.names_for(None) == ()


@pytest.mark.asyncio
class TestSupervisorSpeech:
    async def test_words_are_transcribed_labelled_and_answered_only_when_addressed(
        self, http, flags, audit, redis_reset, run_id, fake_db, monkeypatch
    ):
        monkeypatch.setattr(controller, "SPEECH_HANGOVER_SECONDS", 0.3)
        _allow(fake_db)
        stt = ScriptedSTT(
            ["Let me look at your booking.", "Asha, what time is the slot?"]
        )
        async with Call(run_id, fake_db["run"]) as call:
            call.takeover.agent_names = speech.names_for("Asha Front Desk")
            call.takeover.transcribe_with(lambda: stt, sample_rate=8000)
            base = f"/api/v1/live-calls/{run_id}"
            response = await _as(http, ADMIN_ID).post(
                f"{base}/takeover", json={"mode": "barge"}
            )
            assert response.status_code == 200
            assert await _until(lambda: call.takeover.state.mode == BARGE)

            # First stretch: said to the caller. Transcribed under the
            # supervisor's name, into the agent's context, and no reply.
            for _ in range(3):
                await registry.forward_mic(run_id, _mic(LOUD))
            assert await _until(
                lambda: any(
                    "[supervisor-said]" in m["content"]
                    for m in call.context.get_messages()
                ),
                timeout=4.0,
            )
            said = [
                m["content"]
                for m in call.context.get_messages()
                if "[supervisor-said]" in m["content"]
            ]
            assert said == [
                controller.said_message("user502", "Let me look at your booking.")[
                    "content"
                ]
            ]
            await asyncio.sleep(0.2)
            assert call.model.replies == 0
            assert call.takeover.state.ai_may_speak is False
            # The transcriber got the call's rate, resampled from the mic's.
            assert stt.rates == {8000} and stt.audio >= 3

            # Its own segment in the call's record, labelled, with the words.
            assert await _until(
                lambda: any(
                    e["type"] == controller.EVENT_SPEECH and e["payload"].get("text")
                    for e in call.logs.events
                )
            )
            spans = [
                e["payload"]
                for e in call.logs.events
                if e["type"] == controller.EVENT_SPEECH and e["payload"].get("text")
            ]
            assert spans[-1]["speaker"] == "supervisor"
            assert spans[-1]["by"] == "user502"
            assert spans[-1]["transcribed"] is True
            assert spans[-1]["text"] == "Let me look at your booking."
            # And in the live transcript under their name.
            backlog = await _backlog(run_id)
            lines = [e for e in backlog if e["type"] == "supervisor" and e.get("text")]
            assert lines and lines[-1]["by"] == "user502"
            assert lines[-1]["text"] == "Let me look at your booking."

            # Second stretch: addressed to the agent by name. It answers.
            await asyncio.sleep(0.4)
            for _ in range(3):
                await registry.forward_mic(run_id, _mic(LOUD))
            assert await _until(lambda: call.model.replies == 1, timeout=4.0)
            assert await _until(lambda: len(call.speaker.agent_audio()) == 1)
            assert call.takeover.state.ai_may_speak is True
            asked = [
                m["content"]
                for m in call.context.get_messages()
                if m["content"].startswith("[supervisor-answer]")
            ]
            assert asked and "Asha, what time is the slot?" in asked[-1]
            [answering] = call.events(controller.AGENT_ANSWERING)
            assert answering["addressed"] is True

            # Hand back: the note no longer says their words were lost.
            response = await http.post(f"{base}/hand-back")
            assert response.status_code == 200
            assert await _until(lambda: call.takeover.state.mode == AI)
            note = call.context.get_messages()[-1]["content"]
            assert note.startswith("[supervisor-handback]")
            assert "What they said is above" in note
            assert call.takeover.transcriber is None

    async def test_take_over_words_go_to_context_and_never_start_a_reply(
        self, flags, redis_reset, run_id, fake_db, monkeypatch
    ):
        monkeypatch.setattr(controller, "SPEECH_HANGOVER_SECONDS", 0.3)
        stt = ScriptedSTT(["Asha, what time is the slot?"])
        async with Call(run_id, fake_db["run"]) as call:
            call.takeover.agent_names = speech.names_for("Asha Front Desk")
            call.takeover.transcribe_with(lambda: stt)
            await call.takeover.join(TAKEOVER, by="Priya", by_user_id=ADMIN_ID)
            for _ in range(3):
                await call.takeover.on_mic(_mic(LOUD))
            assert await _until(
                lambda: any(
                    "[supervisor-said]" in m["content"]
                    for m in call.context.get_messages()
                ),
                timeout=4.0,
            )
            await asyncio.sleep(0.2)
            # In a take-over the agent says nothing, addressed or not.
            assert call.model.replies == 0
            assert call.events(controller.AGENT_ANSWERING) == []

    async def test_without_a_transcriber_nothing_is_transcribed(
        self, flags, redis_reset, run_id, fake_db, monkeypatch
    ):
        monkeypatch.setattr(controller, "SPEECH_HANGOVER_SECONDS", 0.2)
        async with Call(run_id, fake_db["run"]) as call:
            await call.takeover.join(BARGE, by="Priya", by_user_id=ADMIN_ID)
            await call.takeover.on_mic(_mic(LOUD))
            assert call.takeover.transcriber is None
            assert await _until(
                lambda: any(
                    e["type"] == controller.EVENT_SPEECH for e in call.logs.events
                )
            )
            [span] = [
                e["payload"]
                for e in call.logs.events
                if e["type"] == controller.EVENT_SPEECH
            ]
            assert span["transcribed"] is False and "text" not in span


# ---------------------------------------------------------------------------
# Escalation held back while a supervisor has the call (the panel's side)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_held_back_escalation_is_recorded_and_shown_on_the_panel(
    flags, redis_reset, run_id, fake_db
):
    async with Call(run_id, fake_db["run"]) as call:
        await call.takeover.join(TAKEOVER, by="Priya", by_user_id=ADMIN_ID)
        await call.takeover.note_escalation_suppressed(
            {"label": "Caller asked for a manager", "reason_code": "explicit_request"}
        )
        [entry] = [
            e["payload"]
            for e in call.logs.events
            if e["type"] == controller.EVENT_ESCALATION_SUPPRESSED
        ]
        assert entry["label"] == "Caller asked for a manager"
        assert entry["status"] == "suppressed"
        assert entry["because"] == "supervisor_on_call"
        assert entry["supervisor"] == "Priya"
        backlog = await _backlog(run_id)
        shown = [e for e in backlog if e["type"] == "escalation"]
        assert shown and shown[0]["label"] == "Caller asked for a manager"
