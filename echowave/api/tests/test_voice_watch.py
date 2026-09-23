"""A voice that takes text and sends back no sound ends the call, with a reason.

On 23 Sept 2026 ElevenLabs accepted every request from our key, returned no
audio and no error, and every call was silence until the caller hung up. These
pin the three things that now happen instead: the call ends on a fatal error
that names the voice, the failure is kept for the staff screen, and a working
voice -- or a caller cutting in -- never trips it.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    TextFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from api import constants
from api.db.models import UserModel
from api.enums import StaffRole
from api.services import voice_failures
from api.services.pipecat import voice_watch
from api.services.pipecat.voice_watch import VoiceSilenceWatch


async def _watch(seconds: float = 0.05):
    from pipecat.utils.asyncio.task_manager import TaskManager
    from pipecat.utils.base_object import BaseObject

    processor = VoiceSilenceWatch(
        provider="elevenlabs", model="eleven_flash_v2_5", run_id=411, seconds=seconds
    )
    await BaseObject.setup(processor, TaskManager())
    pushed: list[Frame] = []
    errors: list[dict] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    async def capture_error(error_msg, exception=None, fatal=False):
        errors.append({"msg": error_msg, "fatal": fatal})

    processor.push_frame = capture
    processor.push_error = capture_error
    processor._FrameProcessor__started = True
    return processor, pushed, errors


async def _send(processor, frame):
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)


def _audio() -> TTSAudioRawFrame:
    return TTSAudioRawFrame(audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1)


@pytest.fixture
def recorded(monkeypatch):
    calls: list[dict] = []

    async def fake_record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(voice_watch.voice_failures, "record", fake_record)
    return calls


class TestASilentVoice:
    @pytest.mark.asyncio
    async def test_ends_the_call_with_a_reason_that_names_the_voice(self, recorded):
        processor, _, errors = await _watch()

        await _send(processor, TTSStartedFrame())
        await asyncio.sleep(0.15)

        assert len(errors) == 1
        assert errors[0]["fatal"] is True
        assert "elevenlabs/eleven_flash_v2_5" in errors[0]["msg"]
        assert "no audio" in errors[0]["msg"]

    @pytest.mark.asyncio
    async def test_is_kept_for_the_staff_screen(self, recorded):
        processor, _, _ = await _watch()

        await _send(processor, TTSStartedFrame())
        await asyncio.sleep(0.15)

        assert recorded == [
            {"provider": "elevenlabs", "model": "eleven_flash_v2_5", "run_id": 411}
        ]

    @pytest.mark.asyncio
    async def test_fires_once_however_many_sentences_follow(self, recorded):
        processor, _, errors = await _watch()

        await _send(processor, TTSStartedFrame())
        await asyncio.sleep(0.15)
        await _send(processor, TTSStartedFrame())
        await asyncio.sleep(0.15)

        assert len(errors) == 1


class TestAWorkingVoice:
    @pytest.mark.asyncio
    async def test_sound_after_the_start_disarms_it(self, recorded):
        processor, _, errors = await _watch()

        await _send(processor, TTSStartedFrame())
        await _send(processor, _audio())
        await asyncio.sleep(0.15)

        assert errors == [] and recorded == []

    @pytest.mark.asyncio
    async def test_a_caller_cutting_in_then_hearing_a_reply_is_fine(self, recorded):
        processor, _, errors = await _watch(seconds=0.2)

        await _send(processor, TTSStartedFrame())
        await _send(processor, InterruptionFrame())
        await asyncio.sleep(0.1)
        await _send(processor, TTSStartedFrame())
        await _send(processor, _audio())
        await asyncio.sleep(0.3)

        assert errors == []


class TestThe23SeptCall:
    """The call that started this: greeting sent, silence, caller says
    "hello?" over it, still silence, caller hangs up a minute later."""

    @pytest.mark.asyncio
    async def test_a_caller_talking_into_the_silence_does_not_hide_it(self, recorded):
        processor, _, errors = await _watch(seconds=0.2)

        await _send(processor, TTSStartedFrame())
        await asyncio.sleep(0.1)
        await _send(processor, InterruptionFrame())  # "Hallo?"
        await asyncio.sleep(0.15)
        assert errors == [], "the cut-in restarts the clock rather than firing early"
        await asyncio.sleep(0.2)

        assert len(errors) == 1 and errors[0]["fatal"] is True

    @pytest.mark.asyncio
    async def test_a_stop_with_no_sound_before_it_is_still_a_silent_voice(
        self, recorded
    ):
        processor, _, errors = await _watch()

        await _send(processor, TTSStartedFrame())
        await _send(processor, TTSStoppedFrame())
        await asyncio.sleep(0.15)

        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_the_call_ending_stops_the_clock(self, recorded):
        from pipecat.frames.frames import EndFrame

        processor, _, errors = await _watch()

        await _send(processor, TTSStartedFrame())
        await _send(processor, EndFrame())
        await asyncio.sleep(0.15)

        assert errors == []


class TestPassThrough:
    @pytest.mark.asyncio
    async def test_every_frame_passes_through_untouched(self, recorded):
        processor, pushed, _ = await _watch(seconds=5)
        frames = [TTSStartedFrame(), _audio(), TextFrame(text="hi"), TTSStoppedFrame()]

        for frame in frames:
            await _send(processor, frame)

        assert pushed == frames
        await processor.cleanup()


class TestTheSwitch:
    def test_off_adds_nothing_to_the_call(self, monkeypatch):
        from api.services.pipecat.run_pipeline import _create_voice_watch

        monkeypatch.setattr(constants, "VOICE_WATCH_ENABLED", False)
        assert _create_voice_watch(object(), 1) is None

    def test_on_watches_the_configured_voice(self, monkeypatch):
        from types import SimpleNamespace

        from api.services.pipecat.run_pipeline import _create_voice_watch

        monkeypatch.setattr(constants, "VOICE_WATCH_ENABLED", True)
        config = SimpleNamespace(
            tts=SimpleNamespace(provider="elevenlabs", model="eleven_flash_v2_5")
        )
        watch = _create_voice_watch(config, 7)
        assert isinstance(watch, VoiceSilenceWatch)
        assert watch.voice.provider == "elevenlabs"

    def test_it_sits_directly_after_the_voice(self):
        from unittest.mock import MagicMock

        from pipecat.processors.frame_processor import FrameProcessor

        from api.services.pipecat.pipeline_builder import build_pipeline

        def proc(name):
            return FrameProcessor(name=name)

        transport = MagicMock()
        transport.input.return_value = proc("in")
        transport.output.return_value = proc("out")
        tts = proc("tts")
        watch = VoiceSilenceWatch(provider="x", model=None, run_id=None)
        pipeline = build_pipeline(
            transport,
            proc("stt"),
            proc("buffer"),
            proc("llm"),
            tts,
            proc("user_agg"),
            proc("assistant_agg"),
            proc("callbacks"),
            proc("metrics"),
            voice_watch=watch,
        )
        order = pipeline.processors
        assert order[order.index(tts) + 1] is watch
        assert order[order.index(watch) + 1].name == "out"


class TestTheRecord:
    @pytest.mark.asyncio
    async def test_keeps_a_day_and_reports_the_latest(self):
        provider = f"testvoice{time.time_ns()}"
        now = time.time()
        await voice_failures.record(
            provider=provider, model="old", run_id=1, at=now - 90_000
        )
        await voice_failures.record(
            provider=provider, model="m1", run_id=2, at=now - 60
        )
        await voice_failures.record(provider=provider, model="m2", run_id=3, at=now)

        mine = [f for f in await voice_failures.recent(now) if f.provider == provider]

        assert len(mine) == 1
        assert mine[0].count == 2, "the one from over a day ago is gone"
        assert mine[0].last_run_id == 3 and mine[0].last_model == "m2"


async def _staff(session) -> UserModel:
    user = UserModel(provider_id="staff-voice", staff_role=StaffRole.SUPERADMIN.value)
    session.add(user)
    await session.flush()
    return user


@asynccontextmanager
async def _client(user):
    from api.app import app
    from api.services.auth.depends import get_superuser

    async def _override():
        return user

    app.dependency_overrides[get_superuser] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_superuser, None)


class TestTheStaffRoute:
    async def test_is_not_there_while_the_switch_is_off(
        self, db_session, async_session, monkeypatch
    ):
        monkeypatch.setattr(constants, "VOICE_WATCH_ENABLED", False)
        async with _client(await _staff(async_session)) as client:
            response = await client.get("/api/v1/admin/provider-keys/voice-failures")
        assert response.status_code == 404

    async def test_lists_the_quiet_voices(self, db_session, async_session, monkeypatch):
        monkeypatch.setattr(constants, "VOICE_WATCH_ENABLED", True)
        provider = f"testroute{time.time_ns()}"
        await voice_failures.record(provider=provider, model="flash", run_id=411)

        async with _client(await _staff(async_session)) as client:
            response = await client.get("/api/v1/admin/provider-keys/voice-failures")

        assert response.status_code == 200
        body = response.json()
        row = next(f for f in body["failures"] if f["provider"] == provider)
        assert row["count"] == 1 and row["last_run_id"] == 411
        assert body["window_hours"] == 24


class TestInARealPipeline:
    """The same failure with pipecat running the frames: a voice that takes
    text, opens synthesis and never returns a byte, as ElevenLabs did."""

    @pytest.mark.asyncio
    async def test_a_voice_that_never_answers_raises_a_fatal_error(self, recorded):
        from pipecat.frames.frames import ErrorFrame, TTSSpeakFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.tests.mock_tts_service import MockTTSService
        from pipecat.tests.utils import SleepFrame, run_test

        class SilentVoice(MockTTSService):
            async def run_tts(self, text, context_id):
                self.received_texts.append(text)
                return
                yield  # an async generator that produces nothing

        voice = SilentVoice(pause_frame_processing=False)
        watch = VoiceSilenceWatch(provider="silent", model=None, run_id=1, seconds=0.5)

        _, up = await run_test(
            Pipeline([voice, watch]),
            frames_to_send=[
                TTSSpeakFrame(text="Hello, how can I help?"),
                SleepFrame(1.0),
            ],
        )

        assert voice.received_texts, "the voice was given the text"
        errors = [f for f in up if isinstance(f, ErrorFrame)]
        assert errors and errors[0].fatal
        assert recorded and recorded[0]["provider"] == "silent"

    @pytest.mark.asyncio
    async def test_a_voice_that_answers_is_left_alone(self, recorded):
        from pipecat.frames.frames import ErrorFrame, TTSSpeakFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.tests.mock_tts_service import MockTTSService
        from pipecat.tests.utils import SleepFrame, run_test

        voice = MockTTSService(pause_frame_processing=False, mock_audio_duration_ms=100)
        watch = VoiceSilenceWatch(provider="ok", model=None, run_id=1, seconds=0.5)

        down, up = await run_test(
            Pipeline([voice, watch]),
            frames_to_send=[
                TTSSpeakFrame(text="Hello, how can I help?"),
                SleepFrame(1.0),
            ],
        )

        assert any(isinstance(f, TTSAudioRawFrame) for f in down)
        assert not [f for f in up if isinstance(f, ErrorFrame)]
        assert recorded == []


class TestFailingOverToABackupVoice:
    """An agent with a backup voice keeps talking: the silent one is dropped,
    and what it swallowed is said again by the backup."""

    @staticmethod
    def _voices():
        from pipecat.pipeline.service_switcher import (
            ServiceSwitcher,
            ServiceSwitcherStrategyFailover,
        )
        from pipecat.tests.mock_tts_service import MockTTSService

        class SilentVoice(MockTTSService):
            async def run_tts(self, text, context_id):
                self.received_texts.append(text)
                return
                yield

        dead = SilentVoice(pause_frame_processing=False, name="dead")
        backup = MockTTSService(
            pause_frame_processing=False, mock_audio_duration_ms=100, name="backup"
        )
        switcher = ServiceSwitcher(
            services=[dead, backup], strategy_type=ServiceSwitcherStrategyFailover
        )
        return dead, backup, switcher

    @pytest.mark.asyncio
    async def test_the_backup_says_the_greeting_and_the_call_goes_on(self, recorded):
        from pipecat.frames.frames import ErrorFrame, TTSSpeakFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.tests.utils import SleepFrame, run_test

        from api.services.pipecat.voice_watch import Voice

        dead, backup, switcher = self._voices()
        watch = VoiceSilenceWatch(
            provider="elevenlabs",
            model="flash",
            run_id=411,
            seconds=0.5,
            switcher=switcher,
            backups=[Voice("cartesia", "sonic")],
        )

        down, up = await run_test(
            Pipeline([switcher, watch]),
            frames_to_send=[
                TTSSpeakFrame(text="Hello, Kriti Labs support."),
                SleepFrame(1.5),
            ],
        )

        assert dead.received_texts == ["Hello, Kriti Labs support."]
        assert any("Kriti Labs" in t for t in backup.received_texts), (
            "the backup said what the dead voice swallowed"
        )
        assert any(isinstance(f, TTSAudioRawFrame) for f in down)
        assert not [f for f in up if isinstance(f, ErrorFrame) and f.fatal]
        assert recorded == [{"provider": "elevenlabs", "model": "flash", "run_id": 411}]
        assert watch.voice == Voice("cartesia", "sonic")

    @pytest.mark.asyncio
    async def test_when_the_backup_is_silent_too_the_call_ends(self, recorded):
        from pipecat.frames.frames import ErrorFrame, TTSSpeakFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.service_switcher import (
            ServiceSwitcher,
            ServiceSwitcherStrategyFailover,
        )
        from pipecat.tests.mock_tts_service import MockTTSService
        from pipecat.tests.utils import SleepFrame, run_test

        from api.services.pipecat.voice_watch import Voice

        class SilentVoice(MockTTSService):
            async def run_tts(self, text, context_id):
                return
                yield

        switcher = ServiceSwitcher(
            services=[
                SilentVoice(pause_frame_processing=False, name="a"),
                SilentVoice(pause_frame_processing=False, name="b"),
            ],
            strategy_type=ServiceSwitcherStrategyFailover,
        )
        watch = VoiceSilenceWatch(
            provider="a",
            model=None,
            run_id=1,
            seconds=0.4,
            switcher=switcher,
            backups=[Voice("b")],
        )

        _, up = await run_test(
            Pipeline([switcher, watch]),
            frames_to_send=[TTSSpeakFrame(text="Hello."), SleepFrame(1.5)],
        )

        assert [r["provider"] for r in recorded] == ["a", "b"]
        assert [f for f in up if isinstance(f, ErrorFrame) and f.fatal]


class TestTheSwitchWithBackups:
    def test_the_watch_is_given_the_switcher_and_the_backup_names(self, monkeypatch):
        from types import SimpleNamespace

        from api.services.pipecat.run_pipeline import _create_voice_watch
        from api.services.pipecat.voice_watch import Voice

        _, _, switcher = TestFailingOverToABackupVoice._voices()
        monkeypatch.setattr(constants, "VOICE_WATCH_ENABLED", True)
        config = SimpleNamespace(
            tts=SimpleNamespace(provider="elevenlabs", model="flash"),
            fallback_tts=[SimpleNamespace(provider="cartesia", model="sonic")],
        )

        watch = _create_voice_watch(config, 7, switcher)

        assert watch._switcher is switcher
        assert watch._voices == [
            Voice("elevenlabs", "flash"),
            Voice("cartesia", "sonic"),
        ]

    def test_no_backups_means_no_switcher(self, monkeypatch):
        from types import SimpleNamespace

        from api.services.pipecat.run_pipeline import _create_voice_watch

        monkeypatch.setattr(constants, "VOICE_WATCH_ENABLED", True)
        config = SimpleNamespace(tts=SimpleNamespace(provider="elevenlabs", model=None))

        watch = _create_voice_watch(config, 7, object())

        assert watch._switcher is None
