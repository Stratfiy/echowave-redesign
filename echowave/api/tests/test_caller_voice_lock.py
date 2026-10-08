"""Only the caller can interrupt: enrolment, the threshold, and every fallback.

The speaker network is replaced by a fake that knows voices by their pitch, so
each rule is pinned without a model file or a network: a 300 Hz tone is the
caller, anything else is whoever the test says it is, at whatever similarity
the test says.
"""

from __future__ import annotations

import numpy as np
import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    UserStartedSpeakingFrame,
)
from pipecat.turns.user_start import (
    ExternalUserTurnStartStrategy,
    MinWordsUserTurnStartStrategy,
)

from api import constants
from api.services import features
from api.services.pipecat import caller_voice_lock as cvl

RATE = 8000
CALLER_HZ = 300.0
OTHER_HZ = 700.0
#: Somebody across the room: 8 dB under the caller at the handset.
ROOM_DBFS = -32.0


def tone(hz: float, secs: float, dbfs: float | None = None) -> np.ndarray:
    if dbfs is None:
        dbfs = -24.0 if hz == CALLER_HZ else ROOM_DBFS
    t = np.arange(int(secs * RATE)) / RATE
    amp = 10 ** (dbfs / 20) * np.sqrt(2) * 32768
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.int16)


class FakeEmbedder:
    """Knows a voice by its dominant pitch. ``similar`` sets how alike the
    other voice is to the caller, as a cosine."""

    def __init__(self, similar: float = 0.0, available: bool = True):
        self.similar = similar
        self.available = available
        self.model_path = "fake"
        self.calls = 0
        self.raise_on_judge = False

    def embed(self, samples, sample_rate):
        self.calls += 1
        if self.raise_on_judge:
            raise RuntimeError("onnxruntime fell over")
        spectrum = np.abs(np.fft.rfft(samples.astype(np.float64)))
        peak = np.argmax(spectrum) * sample_rate / samples.size
        if abs(peak - CALLER_HZ) < 50:
            return np.array([1.0, 0.0], dtype=np.float32)
        s = float(np.clip(self.similar, -1, 1))
        return np.array([s, np.sqrt(1 - s * s)], dtype=np.float32)


class Rig:
    """A wrapped MinWords strategy and what it told the aggregator."""

    def __init__(self, embedder=None, inner=None, **kwargs):
        self.embedder = embedder or FakeEmbedder()
        self.inner = inner or MinWordsUserTurnStartStrategy(min_words=3)
        self.strategy = cvl.CallerVoiceLockUserTurnStartStrategy(
            self.inner,
            embedder=self.embedder,
            background_inference=False,
            **kwargs,
        )
        self.started = 0
        self.resets = 0

        async def on_started(_s, _params):
            self.started += 1

        async def on_reset(_s):
            self.resets += 1

        self.strategy.add_event_handler("on_user_turn_started", on_started)
        self.strategy.add_event_handler("on_reset_aggregation", on_reset)

    async def audio(self, samples: np.ndarray):
        for i in range(0, samples.size, 160):
            await self.strategy.process_frame(
                InputAudioRawFrame(
                    audio=samples[i : i + 160].tobytes(),
                    sample_rate=RATE,
                    num_channels=1,
                )
            )

    async def say(self, text: str):
        return await self.strategy.process_frame(
            InterimTranscriptionFrame(text=text, user_id="c", timestamp="")
        )

    async def frame(self, frame):
        return await self.strategy.process_frame(frame)

    async def enrol(self, secs: float = 3.0, dbfs: float = -24.0):
        """The caller answers the greeting; then the agent replies."""
        await self.audio(tone(CALLER_HZ, secs, dbfs))
        await self.say("hello my name")
        self.started = 0  # that was an ordinary turn, the agent was silent
        await self.frame(BotStartedSpeakingFrame())


class TestFeatures:
    def test_fbank_matches_kaldi(self):
        """Pinned against kaldi-native-fbank with WeSpeaker's options; the
        speaker network is only as good as the features it is fed."""
        rng = np.random.default_rng(7)
        t = np.arange(8000) / 16000
        x = (
            0.3 * np.sin(2 * np.pi * 440 * t)
            + 0.1 * np.sin(2 * np.pi * 1800 * t)
            + 0.02 * rng.standard_normal(8000)
        ).astype(np.float32)
        feats = cvl.fbank(x)
        assert feats.shape == (48, 80)
        expected = [-0.4095, -0.4033, 2.2627, -0.1539, -0.0112, 0.2111]
        got = feats[10, [0, 5, 10, 20, 40, 79]]
        assert np.allclose(got, expected, atol=2e-3)


class TestEnrolment:
    async def test_learns_the_caller_from_their_answer(self):
        rig = Rig()
        await rig.enrol()
        lock = rig.strategy.lock
        assert lock.enrolled
        assert lock.profile_secs >= cvl.DEFAULT_ENROL_SECS
        assert lock.caller_level_db == pytest.approx(-24.0, abs=1.0)

    async def test_a_silent_stretch_with_no_words_teaches_nothing(self):
        """The line before anybody speaks is the room, not the caller."""
        rig = Rig()
        await rig.audio(tone(OTHER_HZ, 3.0))
        await rig.frame(BotStartedSpeakingFrame())
        assert not rig.strategy.lock.enrolled

    async def test_too_little_speech_waits_for_more(self):
        rig = Rig()
        await rig.enrol(secs=1.0)
        assert not rig.strategy.lock.enrolled
        await rig.frame(BotStoppedSpeakingFrame())
        await rig.enrol(secs=1.5)
        assert rig.strategy.lock.enrolled

    async def test_a_piece_that_disagrees_is_left_out(self):
        """Someone else answered part of the greeting, as loud as the caller:
        two pieces against one, and the one is dropped."""
        rig = Rig()
        stretch = np.concatenate([tone(CALLER_HZ, 3.0), tone(OTHER_HZ, 1.5)])
        await rig.audio(stretch)
        await rig.say("hello hello")
        await rig.frame(BotStartedSpeakingFrame())
        lock = rig.strategy.lock
        assert lock.enrolled
        assert cvl.cosine(lock.profile, np.array([1.0, 0.0])) > 0.99

    async def test_the_profile_never_drifts_towards_the_room(self):
        rig = Rig()
        await rig.enrol()
        await rig.frame(BotStoppedSpeakingFrame())
        await rig.audio(tone(OTHER_HZ, 4.0))
        await rig.say("somebody else talking")
        await rig.frame(BotStartedSpeakingFrame())
        assert cvl.cosine(rig.strategy.lock.profile, np.array([1.0, 0.0])) > 0.99


class TestWhileTheAgentSpeaks:
    async def test_another_voice_does_not_interrupt_and_its_words_are_dropped(self):
        rig = Rig()
        await rig.enrol()
        await rig.audio(tone(OTHER_HZ, 1.5))
        resets = rig.resets
        await rig.say("pass me the salt")
        assert rig.started == 0
        assert rig.resets > resets
        assert rig.strategy.decisions[-1].reason == "other_voice"

    async def test_the_caller_still_interrupts(self):
        rig = Rig()
        await rig.enrol()
        await rig.audio(tone(CALLER_HZ, 1.5))
        await rig.say("wait wait stop")
        assert rig.started == 1
        assert rig.strategy.decisions[-1].reason == "match"

    async def test_the_caller_talking_over_a_refused_stranger_gets_through(self):
        rig = Rig()
        await rig.enrol()
        await rig.audio(tone(OTHER_HZ, 1.5))
        await rig.say("pass me the salt")
        assert rig.started == 0
        await rig.audio(tone(CALLER_HZ, 1.5))
        await rig.say("pass me the salt wait stop")
        assert rig.started == 1

    @pytest.mark.parametrize(
        "similar,counts", [(0.29, False), (0.30, True), (0.31, True), (0.9, True)]
    )
    async def test_the_threshold(self, similar, counts):
        rig = Rig(FakeEmbedder(similar=similar), threshold=0.30)
        await rig.enrol()
        await rig.audio(tone(OTHER_HZ, 1.5))
        await rig.say("one two three")
        assert rig.started == (1 if counts else 0)

    async def test_fewer_words_than_min_words_is_unchanged(self):
        """The lock only ever removes interruptions MinWords would make."""
        rig = Rig()
        await rig.enrol()
        await rig.audio(tone(CALLER_HZ, 1.5))
        await rig.say("wait")
        assert rig.started == 0
        assert rig.strategy.decisions == []


class TestFallbacksBehaveAsToday:
    async def test_before_enrolment_every_interruption_counts(self):
        rig = Rig()
        await rig.frame(BotStartedSpeakingFrame())
        await rig.audio(tone(OTHER_HZ, 1.5))
        await rig.say("one two three")
        assert rig.started == 1

    async def test_while_the_agent_is_silent_nothing_is_judged(self):
        rig = Rig()
        await rig.enrol()
        await rig.frame(BotStoppedSpeakingFrame())
        await rig.audio(tone(OTHER_HZ, 1.5))
        calls = rig.embedder.calls
        await rig.say("hello")
        assert rig.started == 1
        assert rig.embedder.calls == calls

    async def test_a_loud_near_field_voice_counts_whatever_the_score(self):
        """A caller who is clearly, loudly on the line is never ignored, even
        when the network does not recognise them (a cold, a shout)."""
        rig = Rig()
        await rig.enrol(dbfs=-24.0)
        await rig.audio(tone(OTHER_HZ, 1.5, dbfs=-18.0))
        await rig.say("can you hear me")
        assert rig.started == 1
        assert rig.strategy.decisions[-1].reason == "loud"

    async def test_too_little_to_judge_waits_then_counts(self):
        rig = Rig()
        await rig.enrol()
        await rig.audio(np.zeros(int(1.5 * RATE), dtype=np.int16))
        await rig.audio(tone(OTHER_HZ, 0.3))
        await rig.say("one two three")
        assert rig.started == 0  # waiting for more speech
        await rig.audio(np.zeros(int(cvl.MAX_DEFER_SECS * RATE) + 160, dtype=np.int16))
        assert rig.started == 1
        assert rig.strategy.decisions[-1].reason == "too_short"

    async def test_the_agent_stopping_while_waiting_starts_the_turn(self):
        rig = Rig()
        await rig.enrol()
        await rig.audio(np.zeros(int(1.5 * RATE), dtype=np.int16))
        await rig.audio(tone(OTHER_HZ, 0.3))
        await rig.say("one two three")
        await rig.frame(BotStoppedSpeakingFrame())
        assert rig.started == 1

    async def test_a_missing_model_is_today(self):
        rig = Rig(FakeEmbedder(available=False))
        await rig.enrol()
        assert not rig.strategy.lock.enrolled
        await rig.audio(tone(OTHER_HZ, 1.5))
        await rig.say("one two three")
        assert rig.started == 1

    async def test_a_network_that_raises_counts_the_interruption(self):
        rig = Rig()
        await rig.enrol()
        rig.embedder.raise_on_judge = True
        await rig.audio(tone(OTHER_HZ, 1.5))
        await rig.say("one two three")
        assert rig.started == 1
        assert rig.strategy.decisions[-1].reason == "unavailable"


class TestTranscriberCalledTurns:
    async def test_flux_start_of_turn_waits_for_enough_speech(self):
        """Flux calls a turn on its first syllable; the lock waits for enough
        audio, then judges."""
        rig = Rig(inner=ExternalUserTurnStartStrategy(enable_interruptions=True))
        await rig.enrol()
        await rig.audio(np.zeros(int(1.5 * RATE), dtype=np.int16))
        await rig.audio(tone(CALLER_HZ, 0.2))
        await rig.frame(UserStartedSpeakingFrame())
        assert rig.started == 0
        await rig.audio(tone(CALLER_HZ, 0.5))
        assert rig.started == 1
        assert rig.strategy.decisions[-1].reason == "match"

    async def test_flux_turn_of_a_stranger_is_refused(self):
        rig = Rig(inner=ExternalUserTurnStartStrategy(enable_interruptions=True))
        await rig.enrol()
        await rig.audio(tone(OTHER_HZ, 1.5))
        await rig.frame(UserStartedSpeakingFrame())
        assert rig.started == 0
        await rig.say("more words from the stranger")
        assert rig.started == 0


class TestTheSwitch:
    def test_both_flags_are_registered_described_and_off(self):
        from pathlib import Path

        ui = (
            Path(constants.APP_ROOT_DIR).parent / "ui/src/lib/features.ts"
        ).read_text()
        for name in ("caller_voice_lock", "deepfilternet_filter"):
            assert name in features.FLAGS
            assert features.DESCRIPTIONS.get(name)
            assert getattr(constants, features.FLAGS[name]) is False
            assert f'"{name}"' in ui

    def test_off_leaves_the_strategies_alone(self, monkeypatch):
        from api.services.pipecat.run_pipeline import _lock_interruptions_to_caller

        monkeypatch.setattr(features, "is_on", lambda name, org=None: False)
        strategies = [MinWordsUserTurnStartStrategy(min_words=3)]
        assert _lock_interruptions_to_caller(strategies, 1) == strategies

    def test_on_wraps_word_count_and_transcriber_turns_only(self, monkeypatch):
        from pipecat.turns.user_start.vad_user_turn_start_strategy import (
            VADUserTurnStartStrategy,
        )

        from api.services.pipecat.run_pipeline import _lock_interruptions_to_caller

        monkeypatch.setattr(
            features, "is_on", lambda name, org=None: name == "caller_voice_lock"
        )
        vad = VADUserTurnStartStrategy()
        wrapped = _lock_interruptions_to_caller(
            [
                MinWordsUserTurnStartStrategy(min_words=3),
                ExternalUserTurnStartStrategy(enable_interruptions=True),
                vad,
            ],
            7,
        )
        assert isinstance(wrapped[0], cvl.CallerVoiceLockUserTurnStartStrategy)
        assert isinstance(wrapped[0].inner, MinWordsUserTurnStartStrategy)
        assert isinstance(wrapped[1], cvl.CallerVoiceLockUserTurnStartStrategy)
        assert wrapped[2] is vad
