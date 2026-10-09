"""A fixed greeting is said the moment the line is up.

Run 23 on staging (outbound Plivo, Sarvam STT and TTS, Claude for the brain):
the WebSocket connected at 09:05:14.880 and the greeting reached the voice at
09:05:23.564. The caller heard nearly nine seconds of nothing and hung up at
23.622. None of that time was the greeting's own: it waited for

1. ``on_pipeline_started``, which fires only once the StartFrame has crossed
   every processor -- and Sarvam's transcriber opened its websocket inside
   ``start()`` (16.467 -> 19.527), with the voice's handshake queued behind it;
2. ``set_node`` on the start node, whose ``_setup_llm_context`` read shared
   knowledge, today's date, memory and skills one after another (-> 21.473);
3. the pipeline's source, where it then queued behind every frame of caller
   audio that had piled up while the transcriber was connecting (-> 23.564).

The Anthropic request at 19.122 that looked like a culprit is
``_warm_llm_connection``: backgrounded, never awaited, and not in the path.

These tests hold each repair in place, against slow stand-ins for the parts the
greeting must no longer wait for.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.frames.frames import LLMContextFrame, TTSSpeakFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.sarvam.stt import SarvamSTTService, SarvamSTTSettings
from pipecat.services.stt_service import STTService

from api.services.billing.usage import provider_from_processor
from api.services.pipecat import event_handlers
from api.services.pipecat.context_ready_gate import LLMContextReadyGate
from api.services.pipecat.sarvam_stt import DecibylSarvamSTTService
from api.services.workflow import pipecat_engine as engine_module
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow_graph import WorkflowGraph
from pipecat.tests import MockLLMService

GREETING = "Namaste, {{clinic_name}}. How may I help you today?"
DISCLOSURE = "This call is recorded."

#: How long each slow stand-in takes. Four of them in a row is 1.6s; side by
#: side they are 0.4s. The bounds below sit between the two.
SLOW = 0.4


@pytest.fixture(autouse=True)
def platform_default(monkeypatch):
    monkeypatch.setattr(engine_module, "RECORDING_DISCLOSURE_ENABLED", True)
    monkeypatch.setattr(engine_module, "RECORDING_DISCLOSURE_TEXT", DISCLOSURE)
    monkeypatch.setattr(engine_module, "AI_DISCLOSURE_ENABLED", False)


class _RecordingTask:
    """A PipelineWorker stand-in that notes what was queued, and when."""

    def __init__(self):
        self.queued: list[tuple[float, object]] = []
        self.handlers: dict[str, object] = {}
        self.user_bot_latency_observer = None

    async def queue_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.queued.append((time.monotonic(), frame))

    def event_handler(self, name: str):
        def decorator(fn):
            self.handlers[name] = fn
            return fn

        return decorator

    def spoken(self) -> list[TTSSpeakFrame]:
        return [f for _, f in self.queued if isinstance(f, TTSSpeakFrame)]


def _engine(
    workflow: WorkflowGraph, **greeting
) -> tuple[PipecatEngine, _RecordingTask]:
    start = workflow.nodes[workflow.start_node_id]
    start.greeting = greeting.get("greeting", GREETING)
    start.greeting_type = greeting.get("greeting_type", "text")
    start.greeting_recording_id = greeting.get("greeting_recording_id")
    start.delayed_start = greeting.get("delayed_start", False)
    start.speaks_first = greeting.get("speaks_first", "agent")
    task = _RecordingTask()
    engine = PipecatEngine(
        llm=MockLLMService(mock_steps=[]),
        context=LLMContext(),
        workflow=workflow,
        call_context_vars={},
        workflow_run_id=1,
        task=task,
        is_voice=True,
    )
    return engine, task


def _make_reads_slow(engine: PipecatEngine, started: list[float]) -> None:
    """Every per-call read the start node makes, each a slow round trip."""

    def slow(value):
        async def read():
            started.append(time.monotonic())
            await asyncio.sleep(SLOW)
            return value

        return read

    engine._get_scoped_document_uuids = slow([])
    engine._get_today_line = slow("Today is Thursday.")
    engine._get_remembered_block = slow("")
    engine._get_skills_block = slow("")
    engine._get_routines_block = slow("")
    engine._get_organization_id = AsyncMock(return_value=1)
    engine._get_workflow_id = AsyncMock(return_value=3)


def _register(engine, task, *, early_opening=True, pre_call_fetch_task=None):
    transport = _RecordingTask()
    transport.output = MagicMock()
    event_handlers.register_event_handlers(
        task=task,
        transport=transport,
        workflow_run_id=1,
        engine=engine,
        audio_buffer=AsyncMock(),
        in_memory_logs_buffer=AsyncMock(),
        transcript_log_coordinator=AsyncMock(),
        pipeline_metrics_aggregator=AsyncMock(),
        pre_call_fetch_task=pre_call_fetch_task,
        early_opening=early_opening,
    )
    return transport


@pytest.mark.asyncio
class TestTheGreetingDoesNotWaitForTheStartNode:
    async def test_it_is_queued_when_the_client_connects(
        self, simple_workflow, monkeypatch
    ):
        """The line is up and nothing else is: the pipeline has not started
        and the start node has not been set. The greeting goes anyway."""
        monkeypatch.setattr(event_handlers, "_capture_call_event", AsyncMock())
        engine, task = _engine(simple_workflow)
        reads: list[float] = []
        _make_reads_slow(engine, reads)
        transport = _register(engine, task)

        before = time.monotonic()
        await transport.handlers["on_client_connected"](transport, None)

        spoken = task.spoken()
        assert len(spoken) == 1
        queued_at = task.queued[0][0]
        assert queued_at - before < 0.1
        # Disclosure and greeting as one utterance, and the unset clinic name
        # leaves no ", ." behind it.
        assert spoken[0].text == (f"{DISCLOSURE} Namaste. How may I help you today?")
        # Committed to the context, so the model does not greet again.
        assert spoken[0].append_to_context is True
        assert reads == []  # the slow parts had not even begun
        assert not engine.llm_context_ready.is_set()

    async def test_the_start_node_still_sets_up_and_does_not_greet_twice(
        self, simple_workflow, monkeypatch
    ):
        monkeypatch.setattr(event_handlers, "_capture_call_event", AsyncMock())
        engine, task = _engine(simple_workflow)
        reads: list[float] = []
        _make_reads_slow(engine, reads)
        transport = _register(engine, task)

        await transport.handlers["on_client_connected"](transport, None)
        await task.handlers["on_pipeline_started"](task, None)

        assert len(task.spoken()) == 1
        assert engine.llm_context_ready.is_set()
        assert engine._current_node.id == simple_workflow.start_node_id
        assert engine.llm._settings.system_instruction
        # The greeting went out before any of the slow reads started.
        assert task.queued[0][0] <= min(reads)

    async def test_the_start_node_reads_run_side_by_side(
        self, simple_workflow, monkeypatch
    ):
        """Five reads of SLOW each: one after another is 5 x SLOW; together,
        about one."""
        monkeypatch.setattr(event_handlers, "_capture_call_event", AsyncMock())
        engine, _task = _engine(simple_workflow)
        reads: list[float] = []
        _make_reads_slow(engine, reads)

        before = time.monotonic()
        await engine.set_node(simple_workflow.start_node_id)
        took = time.monotonic() - before

        assert len(reads) == 5
        assert took < 2.5 * SLOW, f"start node took {took:.2f}s"

    async def test_without_early_opening_the_old_order_holds(
        self, simple_workflow, monkeypatch
    ):
        """A realtime pipeline passes early_opening=False: nothing is said on
        connect, and the greeting comes from open_call as it always did."""
        monkeypatch.setattr(event_handlers, "_capture_call_event", AsyncMock())
        engine, task = _engine(simple_workflow)
        _make_reads_slow(engine, [])
        transport = _register(engine, task, early_opening=False)

        await transport.handlers["on_client_connected"](transport, None)
        assert task.spoken() == []

        await task.handlers["on_pipeline_started"](task, None)
        assert len(task.spoken()) == 1

    async def test_a_pre_call_fetch_still_comes_first(
        self, simple_workflow, monkeypatch
    ):
        """The fetch templates the greeting, so it is not said on connect."""
        monkeypatch.setattr(event_handlers, "_capture_call_event", AsyncMock())
        engine, task = _engine(simple_workflow)
        _make_reads_slow(engine, [])
        fetch: asyncio.Future = asyncio.get_running_loop().create_future()
        fetch.set_result({"clinic_name": "Smile Dental"})
        monkeypatch.setattr(event_handlers, "db_client", MagicMock())
        event_handlers.db_client.update_workflow_run = AsyncMock()
        transport = _register(engine, task, pre_call_fetch_task=fetch)

        await transport.handlers["on_client_connected"](transport, None)
        assert task.spoken() == []

        await task.handlers["on_pipeline_started"](task, None)
        spoken = task.spoken()
        assert len(spoken) == 1
        assert "Namaste, Smile Dental. How may I help you today?" in spoken[0].text


class TestOnlyAPlainTextGreetingGoesEarly:
    def test_a_text_greeting_does(self, simple_workflow):
        engine, _ = _engine(simple_workflow)
        assert engine.static_opening_line()

    def test_a_dynamic_greeting_does_not(self, simple_workflow):
        """It is meant to be asked for at answer time."""
        engine, _ = _engine(simple_workflow)
        engine.set_fetch_dynamic_greeting(AsyncMock(return_value="hi"))
        assert engine.static_opening_line() is None

    def test_an_audio_greeting_does_not(self, simple_workflow):
        engine, _ = _engine(
            simple_workflow, greeting_type="audio", greeting_recording_id="7"
        )
        assert engine.static_opening_line() is None

    def test_no_greeting_does_not(self, simple_workflow):
        """The model writes the opening, and needs the start node's context."""
        engine, _ = _engine(simple_workflow, greeting=None)
        assert engine.static_opening_line() is None

    def test_a_delayed_start_does_not(self, simple_workflow):
        engine, _ = _engine(simple_workflow, delayed_start=True)
        assert engine.static_opening_line() is None

    def test_a_caller_first_agent_does_not(self, simple_workflow):
        engine, _ = _engine(simple_workflow, speaks_first="caller")
        assert engine.static_opening_line() is None

    def test_a_text_chat_does_not(self, simple_workflow):
        engine, _ = _engine(simple_workflow)
        engine._is_voice = False
        assert engine.static_opening_line() is None


@pytest.mark.asyncio
class TestTheCallersFirstTurnWaitsForTheInstructions:
    async def _gate(self, ready, **kwargs):
        gate = LLMContextReadyGate(ready, **kwargs)
        gate.push_frame = AsyncMock()
        return gate

    async def test_a_turn_before_the_start_node_is_held(self):
        ready = asyncio.Event()
        gate = await self._gate(ready)
        frame = LLMContextFrame(LLMContext())

        held = asyncio.create_task(gate.process_frame(frame, FrameDirection.DOWNSTREAM))
        await asyncio.sleep(0.05)
        gate.push_frame.assert_not_awaited()

        ready.set()
        await held
        gate.push_frame.assert_awaited_once_with(frame, FrameDirection.DOWNSTREAM)

    async def test_once_ready_nothing_is_held(self):
        ready = asyncio.Event()
        ready.set()
        gate = await self._gate(ready)
        frame = LLMContextFrame(LLMContext())
        await asyncio.wait_for(
            gate.process_frame(frame, FrameDirection.DOWNSTREAM), 0.1
        )
        gate.push_frame.assert_awaited_once()

    async def test_the_hold_has_a_ceiling(self):
        """A start node that never finishes must not mean a caller who is
        never answered."""
        gate = await self._gate(asyncio.Event(), max_hold_secs=0.05)
        await gate.process_frame(
            LLMContextFrame(LLMContext()), FrameDirection.DOWNSTREAM
        )
        gate.push_frame.assert_awaited_once()

    async def test_other_frames_pass_straight_through(self):
        gate = await self._gate(asyncio.Event())
        frame = TTSSpeakFrame("hello")
        await asyncio.wait_for(
            gate.process_frame(frame, FrameDirection.DOWNSTREAM), 0.1
        )
        gate.push_frame.assert_awaited_once()


def _stt() -> DecibylSarvamSTTService:
    return DecibylSarvamSTTService(
        api_key="test-key",
        settings=SarvamSTTSettings(model="saaras:v3"),
        sample_rate=8000,
    )


async def _drain(gen):
    return [frame async for frame in gen]


@pytest.mark.asyncio
class TestTheTranscriberConnectsBesideTheCall:
    async def test_start_does_not_wait_for_the_handshake(self, monkeypatch):
        """The StartFrame has to move on so the voice behind it can start."""
        stt = _stt()
        connected = asyncio.Event()

        async def slow_connect():
            await asyncio.sleep(SLOW)
            connected.set()

        monkeypatch.setattr(STTService, "start", AsyncMock())
        stt._connect = slow_connect
        stt.create_task = lambda coro, name=None: asyncio.create_task(coro)

        before = time.monotonic()
        await stt.start(MagicMock())
        assert time.monotonic() - before < 0.1
        assert stt.connecting
        assert not connected.is_set()

        await asyncio.wait_for(connected.wait(), 2 * SLOW)

    async def test_audio_during_the_handshake_is_held_and_sent_in_order(self):
        """Never dropped: a caller's "hello?" over the greeting is still heard."""
        stt = _stt()
        pending = asyncio.get_running_loop().create_future()
        stt._connect_task = pending

        await _drain(stt.run_stt(b"\x01\x00"))
        await _drain(stt.run_stt(b"\x02\x00"))

        socket = MagicMock(transcribe=AsyncMock(), flush=AsyncMock())
        stt._socket_client = socket
        pending.set_result(None)

        await _drain(stt.run_stt(b"\x03\x00"))

        import base64

        sent = [
            base64.b64decode(call.kwargs["audio"])
            for call in socket.transcribe.await_args_list
        ]
        assert sent == [b"\x01\x00", b"\x02\x00", b"\x03\x00"]
        assert not stt._held_audio

    async def test_a_held_flush_is_sent_after_the_held_audio(self):
        stt = _stt()
        pending = asyncio.get_running_loop().create_future()
        stt._connect_task = pending
        await _drain(stt.run_stt(b"\x01\x00"))
        stt._flush_after_held = True

        socket = MagicMock(transcribe=AsyncMock(), flush=AsyncMock())
        stt._socket_client = socket
        pending.set_result(None)
        await _drain(stt.run_stt(b"\x02\x00"))

        socket.flush.assert_awaited_once()
        assert stt._flush_after_held is False


def test_the_transcriber_is_still_a_sarvam_service_and_bills_as_one():
    stt = _stt()
    assert isinstance(stt, SarvamSTTService)
    assert provider_from_processor(stt.name) == "sarvam"
