"""Decibyl spoken: turn-taking, interruption and what was heard (handoff 12,
21; screen 05).

Done when: a finished turn reaches Decibyl's own brain and its words stream
to the voice as they form; an interruption cancels the turn in flight; only
what was actually played is written back to the thread, marked when cut off,
so the next turn never assumes it was heard; stage timings are durations on
one clock with missing stages left unknown; the spoken turn uses Decibyl's
voice rules and writes no chat draft; and a session runs on the speech it
started with, with the person's voice applied only where it can be.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSTextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import SleepFrame, run_test

from api.services.voice import brain, pipeline
from api.services.voice.brain import DecibylVoiceBrain, HeardTracker, Turn, TurnLedger


def _ledger(sent: list) -> TurnLedger:
    async def send(message):
        sent.append(message)

    return TurnLedger(
        session_id=9, organization_id=1, user_id=2, thread_id="t-1", send=send
    )


def _said(text: str) -> LLMContextFrame:
    return LLMContextFrame(
        context=LLMContext(messages=[{"role": "user", "content": text}])
    )


@pytest.mark.asyncio
class TestTheBrain:
    async def test_a_turn_streams_decibyls_words(self):
        sent: list = []
        heard: list = []

        async def answer(ledger, turn, on_words):
            heard.append(turn.text)
            await on_words("Your next meeting ")
            await on_words("is at four.")
            return "Your next meeting is at four."

        ledger = _ledger(sent)
        down, _ = await run_test(
            DecibylVoiceBrain(ledger, answer=answer),
            frames_to_send=[_said("When is my next meeting?"), SleepFrame(0.1)],
            expected_down_frames=[
                LLMFullResponseStartFrame,
                LLMTextFrame,
                LLMTextFrame,
                LLMFullResponseEndFrame,
            ],
        )
        assert heard == ["When is my next meeting?"]
        assert [f.text for f in down if isinstance(f, LLMTextFrame)] == [
            "Your next meeting ",
            "is at four.",
        ]
        assert sent[0] == {
            "type": "voice-phase",
            "payload": {"phase": "processing", "turn_index": 0},
        }
        assert ledger.current.first_text_at is not None

    async def test_an_interruption_cancels_the_turn_in_flight(self):
        cancelled = asyncio.Event()

        async def answer(ledger, turn, on_words):
            await on_words("Let me tell you a long ")
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return ""

        down, _ = await run_test(
            DecibylVoiceBrain(_ledger([]), answer=answer),
            frames_to_send=[
                _said("Tell me everything"),
                SleepFrame(0.05),
                InterruptionFrame(),
                SleepFrame(0.05),
            ],
        )
        assert cancelled.is_set()
        kinds = [type(f) for f in down]
        assert InterruptionFrame in kinds
        assert LLMFullResponseEndFrame not in kinds

    async def test_a_failure_still_says_something(self):
        async def answer(ledger, turn, on_words):
            raise RuntimeError("model down")

        down, _ = await run_test(
            DecibylVoiceBrain(_ledger([]), answer=answer),
            frames_to_send=[_said("Hello"), SleepFrame(0.05)],
        )
        words = [f.text for f in down if isinstance(f, LLMTextFrame)]
        assert words == ["Sorry, I could not answer that just now. Please try again."]

    async def test_an_empty_turn_asks_nothing(self):
        answer = AsyncMock()
        down, _ = await run_test(
            DecibylVoiceBrain(_ledger([]), answer=answer),
            frames_to_send=[_said("   "), SleepFrame(0.02)],
        )
        answer.assert_not_awaited()
        assert down == []


@pytest.mark.asyncio
class TestWhatWasHeard:
    async def test_cut_off_records_only_what_played(self, monkeypatch):
        recorded: list = []
        measured: list = []

        async def record_heard(ledger, turn):
            recorded.append((turn.heard_text(), turn.interrupted))

        async def record_server(**kw):
            measured.append(kw)

        from api.services.voice import latency

        monkeypatch.setattr(brain, "record_heard", record_heard)
        monkeypatch.setattr(latency, "record_server", record_server)
        sent: list = []
        ledger = _ledger(sent)
        turn = ledger.new_turn("What's the weather?")
        turn.said = "It is sunny and warm all week, with rain on Sunday."
        await run_test(
            HeardTracker(ledger),
            frames_to_send=[
                BotStartedSpeakingFrame(),
                TTSTextFrame("It is sunny", aggregated_by="sentence"),
                TTSTextFrame("and warm", aggregated_by="sentence"),
                # An interruption is a system frame and overtakes queued
                # words; in a call the words before it have already played.
                SleepFrame(0.05),
                InterruptionFrame(),
                TTSTextFrame("all week", aggregated_by="sentence"),
            ],
        )
        await ledger.drain()
        assert recorded == [("It is sunny and warm", True)]
        assert measured[0]["turn_index"] == 0
        assert measured[0]["stages"]["tts_first_audio"] is not None
        closed = [m for m in sent if m["type"] == "voice-turn-closed"]
        assert closed == [
            {
                "type": "voice-turn-closed",
                "payload": {"turn_index": 0, "interrupted": True, "tool_turn": False},
            }
        ]

    async def test_a_finished_reply_closes_once(self, monkeypatch):
        recorded: list = []

        async def record_heard(ledger, turn):
            recorded.append(turn.interrupted)

        from api.services.voice import latency

        monkeypatch.setattr(brain, "record_heard", record_heard)
        monkeypatch.setattr(latency, "record_server", AsyncMock())
        ledger = _ledger([])
        ledger.new_turn("Hi")
        await run_test(
            HeardTracker(ledger),
            frames_to_send=[
                TTSTextFrame("Hello!", aggregated_by="sentence"),
                LLMFullResponseEndFrame(),
                SleepFrame(0.05),
                InterruptionFrame(),
            ],
        )
        await ledger.drain()
        assert recorded == [False]

    async def test_the_thread_row_marks_an_interruption(self, monkeypatch):
        rows: list = []

        async def record(**kw):
            rows.append(kw)

        from api.services.workflow import agent_timeline

        monkeypatch.setattr(agent_timeline, "record", record)
        ledger = _ledger([])
        turn = Turn(
            index=0,
            text="q",
            started_at=0.0,
            heard=["It is", "sunny"],
            interrupted=True,
        )
        await brain.record_heard(ledger, turn)
        silent = Turn(index=1, text="q", started_at=0.0, interrupted=True)
        await brain.record_heard(ledger, silent)
        whole = Turn(index=2, text="q", started_at=0.0, heard=["Done."])
        await brain.record_heard(ledger, whole)
        bodies = [r["payload"]["body"] for r in rows]
        assert bodies == [
            "It is sunny … (interrupted)",
            "(interrupted before speaking)",
            "Done.",
        ]
        assert all(r["thread_id"] == "t-1" and r["in_channel"] is False for r in rows)
        assert rows[0]["payload"]["via"] == "voice"


class TestStages:
    def test_durations_on_one_clock_and_unknown_left_unknown(self):
        turn = Turn(
            index=0, text="q", started_at=10.0, user_stopped_at=9.6, first_text_at=10.35
        )
        stages = turn.stages()
        assert round(stages["stt_final"]) == 400
        assert round(stages["brain_first_text"]) == 350
        assert stages["tts_first_audio"] is None


@pytest.mark.asyncio
class TestDecibylSpoken:
    async def test_words_go_to_the_voice_and_no_draft_is_written(self, monkeypatch):
        from api.services.agent_builder import client
        from api.services.workflow import decibyl, reply_draft

        systems: list = []

        async def stream(**kw):
            systems.append(kw["system"])
            await kw["on_text"]("Hel")
            await kw["on_text"]("Hello the")
            await kw["on_text"]("Hello there.")
            return client.ModelReply(text="Hello there.")

        monkeypatch.setattr(client, "stream", stream)
        set_draft = AsyncMock()
        monkeypatch.setattr(reply_draft, "set_draft", set_draft)
        pieces: list = []

        async def on_words(piece):
            pieces.append(piece)

        model = SimpleNamespace(
            provider="anthropic", model="m", api_key="k", key_source=""
        )
        spoken = decibyl.VoiceTurn(on_words=on_words)
        with decibyl.voice_turn(spoken):
            await decibyl._speak(model, client.Conversation(), 1)
        assert pieces == ["Hel", "lo the", "re."]
        assert spoken.said == "Hello there."
        assert decibyl.VOICE_RULES in systems[0]
        set_draft.assert_not_awaited()

    async def test_typed_turns_are_unchanged(self, monkeypatch):
        from api.services.agent_builder import client
        from api.services.workflow import decibyl

        systems: list = []

        async def stream(**kw):
            systems.append(kw["system"])
            return client.ModelReply(text="ok")

        monkeypatch.setattr(client, "stream", stream)
        model = SimpleNamespace(
            provider="anthropic", model="m", api_key="k", key_source=""
        )
        await decibyl._speak(model, client.Conversation(), 1)
        assert decibyl.VOICE_RULES not in systems[0]
        assert decibyl.current_voice_turn() is None

    async def test_a_spoken_turn_goes_through_decibyls_answer(self, monkeypatch):
        from api.services.workflow import decibyl

        lines: list = []

        async def record_line(ledger, text):
            lines.append(text)

        async def answer(org, text, **kw):
            turn = decibyl.current_voice_turn()
            turn.tool_turn = True
            await turn.on_words("Done")
            return "Done"

        monkeypatch.setattr(brain, "record_line", record_line)
        monkeypatch.setattr(decibyl, "answer", answer)
        ledger = _ledger([])
        turn = ledger.new_turn("Draft a reply to Ravi")
        words: list = []

        async def on_words(piece):
            words.append(piece)

        await brain.answer_with_decibyl(ledger, turn, on_words)
        assert lines == ["Draft a reply to Ravi"]
        assert words == ["Done"] and turn.tool_turn is True


class TestSessionSpeech:
    def _effective(self, tts_model="bulbul:v3"):
        from pydantic import BaseModel, ConfigDict

        from api.schemas.ai_model_configuration import (
            EffectiveAIModelConfiguration,  # noqa: F401
        )

        class Section(BaseModel):
            model_config = ConfigDict(extra="allow")
            provider: str
            model: str
            api_key: str = "k"

        class Effective(BaseModel):
            stt: Section
            tts: Section

        return Effective(
            stt=Section(provider="sarvam", model="saaras:v3"),
            tts=Section(provider="sarvam", model=tts_model),
        )

    def test_a_reconnect_must_run_what_the_session_started_on(self):
        effective = self._effective()
        snapshot = pipeline.speech_choice(effective)
        assert pipeline.same_speech(snapshot, effective)
        assert not pipeline.same_speech(
            snapshot, self._effective(tts_model="bulbul:v2")
        )

    def test_the_persons_voice_language_and_speed_apply(self):
        session = {
            "voice": "kavya",
            "config": {"language": "ta-IN", "speed": 1.2},
        }
        applied_config, applied = pipeline.apply_person(self._effective(), session)
        assert applied == {"language": "ta-IN", "voice_applied": True}
        assert applied_config.tts.voice == "kavya"
        assert applied_config.tts.language == "ta-IN"
        assert applied_config.tts.speed == 1.2
        assert applied_config.stt.language == "ta-IN"

    def test_a_voice_for_another_model_is_said_not_swapped(self):
        session = {"voice": "anushka", "config": {"language": "hi-IN"}}
        applied_config, applied = pipeline.apply_person(self._effective(), session)
        assert applied["voice_applied"] is False
        assert getattr(applied_config.tts, "voice", None) is None
