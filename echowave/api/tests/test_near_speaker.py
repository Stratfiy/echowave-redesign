"""Turning down the people who are not holding the phone.

A phone gives one microphone channel, and two voices on one channel cannot be
separated by classical means -- blind source separation needs at least as many
microphones as speakers. So this is not separation. It leans on the one thing
that is true on a single channel: the caller holds the phone and nobody else
does, which leaves distant speech both quieter and duller.

**The tests that matter are the ones about not acting.** A gate that clips the
caller's own words is worse than the background noise it removes, so most of
what follows is about the caller getting through untouched.

Frames here are synthetic: a "near" voice is loud and bright, a "far" one is
quiet and dull, which is the distinction the gate is built on and the one worth
pinning.
"""

from __future__ import annotations

import numpy as np
import pytest

from api.services.pipecat import near_speaker
from api.services.pipecat.near_speaker import NearSpeakerGate

RATE = 8000
SAMPLES = int(RATE * near_speaker.FRAME_MS / 1000)


def voice(amplitude: float, brightness: float = 1.0, seed: int = 0) -> np.ndarray:
    """A frame of voice-shaped noise at a level and a brightness.

    Brightness scales the high-frequency half, which is what distance takes
    away -- so ``brightness=0.1`` is the same voice heard across a room.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(SAMPLES) / RATE
    # A low formant plus a high one; distance eats the high one.
    low = np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 440 * t)
    high = brightness * (
        np.sin(2 * np.pi * 2600 * t) + 0.5 * rng.standard_normal(SAMPLES)
    )
    wave = (low + high) / 2.0
    peak = float(np.max(np.abs(wave))) or 1.0
    return (wave / peak * amplitude * 32767).astype(np.int16)


NEAR = voice(0.5, brightness=1.0)
FAR = voice(0.06, brightness=0.08, seed=1)
SILENCE = np.zeros(SAMPLES, dtype=np.int16)


def warm(gate: NearSpeakerGate, frame: np.ndarray = NEAR) -> NearSpeakerGate:
    """Let the gate hear the caller long enough to have a reference."""
    for _ in range(near_speaker.WARMUP_FRAMES + 1):
        gate.gain_for(frame)
    return gate


class TestItIsOffUnlessAskedFor:
    """The opposite default to noise suppression, deliberately: removing a fan
    is safe on every call; turning a voice down is only right when the operator
    knows their callers are somewhere noisy."""

    @pytest.mark.parametrize(
        "config", [None, {}, {"enabled": False}, "on", {"enabled": "yes"}]
    )
    def test_anything_short_of_an_explicit_yes_is_off(self, config):
        assert near_speaker.wants_gate(config) is False

    def test_an_explicit_yes_is_on(self):
        assert near_speaker.wants_gate({"enabled": True}) is True


class TestTheCallerGetsThroughUntouched:
    """Every one of these is a way the caller could have been clipped."""

    def test_the_opening_words_are_never_gated(self):
        """Before it has heard the caller it has nothing to compare against,
        so acting would gate the very first thing anybody says."""
        gate = NearSpeakerGate(sample_rate=RATE)

        assert gate.gain_for(FAR) == 1.0
        assert not gate.warmed_up

    def test_a_steady_caller_is_left_alone(self):
        gate = warm(NearSpeakerGate(sample_rate=RATE))

        assert gate.gain_for(NEAR) == 1.0

    def test_a_caller_who_lowers_their_voice_is_followed_not_gated(self):
        """Quiet but not dull. Requiring both is what separates a softly
        spoken caller from someone across the room."""
        gate = warm(NearSpeakerGate(sample_rate=RATE))

        quieter = voice(0.12, brightness=1.0, seed=2)

        assert gate.gain_for(quieter) == 1.0

    def test_a_muffled_caller_is_left_alone(self):
        """Dull but not quiet -- a hand over the mic, a cheap handset."""
        gate = warm(NearSpeakerGate(sample_rate=RATE))

        muffled = voice(0.5, brightness=0.05, seed=3)

        assert gate.gain_for(muffled) == 1.0

    def test_the_reference_ages_towards_a_quietening_caller(self):
        """It decays about a decibel a second, so a caller who drops their
        voice for a while is caught up with rather than shut out."""
        gate = warm(NearSpeakerGate(sample_rate=RATE))
        started_at = gate.reference_dbfs

        for _ in range(100):  # two seconds of quiet
            gate.gain_for(SILENCE)
            gate.observe(started_at - 30, gate.reference_tilt_db)

        assert gate.reference_dbfs < started_at


class TestItDoesTurnDownTheRoom:
    def test_a_distant_voice_is_attenuated(self):
        gate = warm(NearSpeakerGate(sample_rate=RATE))

        assert gate.gain_for(FAR) < 1.0

    def test_it_attenuates_rather_than_silences(self):
        """A gate that slams to zero sounds broken, and starves the voice
        detector of the signal it uses to decide a turn ended."""
        gate = warm(NearSpeakerGate(sample_rate=RATE))

        assert 0.0 < gate.gain_for(FAR) < 1.0

    def test_the_room_cannot_drag_the_reference_down_to_itself(self):
        """A frame judged far must not teach the gate that far is normal, or a
        crowded minute would end with the caller below the bar."""
        gate = warm(NearSpeakerGate(sample_rate=RATE))
        reference = gate.reference_dbfs

        for _ in range(20):
            gate.gain_for(FAR)

        assert gate.reference_dbfs == reference

    def test_a_louder_setting_turns_it_down_further(self):
        soft = warm(NearSpeakerGate(sample_rate=RATE, attenuation_db=6.0))
        hard = warm(NearSpeakerGate(sample_rate=RATE, attenuation_db=24.0))

        assert hard.gain_for(FAR) < soft.gain_for(FAR)

    def test_a_wider_margin_lets_more_through(self):
        narrow = warm(NearSpeakerGate(sample_rate=RATE, margin_db=3.0))
        wide = warm(NearSpeakerGate(sample_rate=RATE, margin_db=24.0))
        middling = voice(0.1, brightness=0.08, seed=4)

        assert narrow.gain_for(middling) < 1.0
        assert wide.gain_for(middling) == 1.0


class TestSilenceAndOtherRealInputs:
    def test_silence_is_left_alone(self):
        """Gating room tone makes a live line sound dead, and there is nothing
        there to turn down anyway."""
        gate = warm(NearSpeakerGate(sample_rate=RATE))

        assert gate.gain_for(SILENCE) == 1.0

    def test_exact_zeros_do_not_raise(self):
        """A muted caller sends whole seconds of them."""
        assert near_speaker.dbfs(SILENCE) == -np.inf
        assert near_speaker.tilt_db(SILENCE) == pytest.approx(0.0, abs=1.0)

    def test_an_empty_frame_is_not_a_crash(self):
        empty = np.zeros(0, dtype=np.int16)

        assert near_speaker.dbfs(empty) == -np.inf
        assert near_speaker.tilt_db(empty) == 0.0

    def test_silence_does_not_count_towards_warming_up(self):
        gate = NearSpeakerGate(sample_rate=RATE)

        for _ in range(near_speaker.WARMUP_FRAMES * 2):
            gate.gain_for(SILENCE)

        assert not gate.warmed_up


class TestTheArithmeticIsSound:
    def test_attenuation_is_the_decibels_it_claims(self):
        gate = warm(NearSpeakerGate(sample_rate=RATE, attenuation_db=12.0))

        assert gate.gain_for(FAR) == pytest.approx(0.251, abs=0.001)

    def test_applying_gain_does_not_wrap_around(self):
        """int16 arithmetic that overflows turns a loud sound into a loud
        sound of the opposite sign, which is heard as a click."""
        loud = np.full(SAMPLES, 32000, dtype=np.int16)

        assert near_speaker.apply_gain(loud, 1.5).max() <= 32767
        assert near_speaker.apply_gain(loud, 1.0) is loud

    def test_a_near_frame_is_brighter_than_a_far_one(self):
        """The premise the whole gate rests on. If this ever stops being true
        of the fixtures, every test above is testing nothing."""
        assert near_speaker.tilt_db(NEAR) > near_speaker.tilt_db(FAR)
        assert near_speaker.dbfs(NEAR) > near_speaker.dbfs(FAR)


class TestTheSettingsAreBounded:
    @pytest.mark.parametrize("key", ["margin_db", "attenuation_db"])
    def test_nonsense_falls_back_to_the_default(self, key):
        reader = getattr(near_speaker, key)
        assert reader({key: "loud"}) == reader({})
        assert reader({key: True}) == reader({})
        assert reader(None) == reader({})

    def test_an_extreme_value_is_clamped_rather_than_obeyed(self):
        """A 90 dB attenuation is a mute, and a 0 dB margin gates everything
        that is not the loudest frame of the call."""
        assert near_speaker.attenuation_db({"attenuation_db": 90}) <= 30.0
        assert near_speaker.margin_db({"margin_db": 0}) >= 3.0
