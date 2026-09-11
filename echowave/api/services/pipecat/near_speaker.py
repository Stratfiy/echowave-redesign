"""Turn down the people who are not holding the phone.

A tester rang from a crowded place and the agent kept answering the next table.
The noise suppression already shipped cannot help — RNNoise attenuates noise by
~12 dB and *speech* by ~2 dB, so it is built to preserve exactly what needs
removing. And nothing else in the open can either: a phone gives us **one**
microphone channel, and separating two voices from one channel is impossible by
classical means. Blind source separation needs at least as many microphones as
there are speakers. With one channel it is only doable with a learned prior,
which is why every product that does it is a neural network behind a licence.

So this does not separate voices. It exploits the one thing that *is* true on a
single channel: **the caller is holding the phone and nobody else is.**

Distance leaves two marks on speech, and both survive an 8 kHz telephone codec:

* **Level.** Sound falls off with distance, and a phone held at the cheek adds
  proximity gain on top. Someone two metres away arrives far quieter.
* **Spectral tilt.** Air and room reflections eat high frequencies with
  distance, so distant speech is duller than near speech. Measured as the
  energy of the frame's first difference over the energy of the frame — a
  one-tap high-pass, one vectorised subtraction, no FFT and no state.

A frame is turned down only when it is *both* well below the caller's own
established level *and* duller than the caller's own established tilt. One
signal alone is not enough: a caller who lowers their voice is quiet but not
dull, and a muffled near-field caller is dull but not quiet. Requiring both is
what keeps the caller's own words out of it.

**Everything here is biased towards doing nothing.**

* It is off unless switched on, per agent.
* It attenuates rather than silences. A gate that slams to zero sounds broken
  and, worse, starves the voice detector of the very signal it uses to decide a
  turn ended.
* It does nothing at all until it has heard the caller speak for long enough to
  have a level worth comparing against.
* The reference level rises quickly and falls very slowly, so a caller who goes
  quiet is not progressively gated out — the estimate simply ages out towards
  them.

What it will not do: a person talking loudly right beside the phone is near by
both measures and passes through untouched. That is the honest limit of one
microphone, and it is why this narrows the problem rather than solving it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: The per-agent key in ``workflow_configurations``.
CONFIG_KEY = "near_speaker_configuration"

#: Frames are judged in 20 ms blocks, the same grain the transports already
#: move audio in. Short enough to follow a syllable, long enough that one
#: plosive does not swing the estimate.
FRAME_MS = 20

#: How far below the caller's established level a frame must sit before it is
#: even a candidate. 9 dB is about a factor of three in amplitude -- roughly a
#: voice across a table rather than one at the handset.
DEFAULT_MARGIN_DB = 9.0

#: How much a frame judged far is turned down. Not silence: see the module
#: docstring. 12 dB takes background speech below the transcriber's floor
#: while leaving the room tone that tells a caller the line is still open.
DEFAULT_ATTENUATION_DB = 12.0

#: A frame must also be this many dB duller than the caller's own tilt.
#: Distance costs high frequencies; a caller merely speaking softly does not.
DEFAULT_TILT_MARGIN_DB = 3.0

#: Frames of speech before the reference means anything. At 20 ms a second of
#: the caller's own voice is fifty, and until then nothing is touched.
WARMUP_FRAMES = 50

#: The reference tracks upward within a few frames and decays about 1 dB per
#: second, so a loud interruption cannot pin it high for long and a caller
#: dropping their voice is followed rather than gated.
ATTACK = 0.25
DECAY_DB_PER_SECOND = 1.0

#: Below this a frame is silence or line noise, not a voice, and it neither
#: updates the reference nor gets attenuated -- there is nothing there to turn
#: down, and gating the room tone makes a live line sound dead.
SILENCE_DBFS = -55.0


def wants_gate(config: Any) -> bool:
    """Is the near-speaker gate on for this agent? Off unless switched on.

    The opposite default to noise suppression, and deliberately: RNNoise
    removing a fan is safe on every call, while turning a voice down is only
    right when the operator knows their callers are somewhere noisy. A wrong
    guess here clips the caller.
    """
    if not isinstance(config, dict):
        return False
    return config.get("enabled") is True


def _setting(config: Any, key: str, default: float, low: float, high: float) -> float:
    if not isinstance(config, dict):
        return default
    raw = config.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return default
    return float(min(high, max(low, raw)))


def margin_db(config: Any) -> float:
    """How far below the caller a frame must be, 3 to 24 dB."""
    return _setting(config, "margin_db", DEFAULT_MARGIN_DB, 3.0, 24.0)


def attenuation_db(config: Any) -> float:
    """How far a far frame is turned down, 3 to 30 dB."""
    return _setting(config, "attenuation_db", DEFAULT_ATTENUATION_DB, 3.0, 30.0)


def dbfs(frame: np.ndarray) -> float:
    """The frame's level, in dB below full scale.

    Silence is a real input, not an edge case to crash on: a muted caller sends
    whole seconds of exact zeros.
    """
    if frame.size == 0:
        return -math.inf
    rms = float(np.sqrt(np.mean(np.square(frame.astype(np.float64)))))
    if rms <= 0.0:
        return -math.inf
    return 20.0 * math.log10(rms / 32768.0)


def tilt_db(frame: np.ndarray, _sample_rate: int = 0) -> float:
    """How bright the frame is: high-frequency energy over total, in dB.

    The high-frequency part is the first difference of the samples. That is a
    textbook one-tap high-pass, and here it is the right one for three reasons:
    it is exact rather than an approximation of some other filter, it is a
    single vectorised subtraction, and it needs no state between frames.

    The first version of this ran a one-pole filter in a Python loop -- eight
    thousand iterations a second **per concurrent call**, in the audio path.
    An IIR is sequential by nature, so the fix is not to vectorise that filter
    but to choose a measure that does not need one.

    The absolute value is meaningless; only the comparison with the caller's
    own running figure matters, so any monotone measure of brightness does.
    """
    if frame.size < 2:
        return 0.0
    samples = frame.astype(np.float64)
    high = np.diff(samples)
    total_energy = float(np.mean(np.square(samples))) + 1e-9
    high_energy = float(np.mean(np.square(high))) + 1e-9
    return 10.0 * math.log10(high_energy / total_energy)


@dataclass
class NearSpeakerGate:
    """Decides, frame by frame, whether a voice is the one on the phone.

    Pure state and arithmetic — no audio library, no pipeline — so the
    behaviour that matters can be tested against synthetic frames rather than
    inferred from a live call.
    """

    sample_rate: int = 8000
    margin_db: float = DEFAULT_MARGIN_DB
    attenuation_db: float = DEFAULT_ATTENUATION_DB
    tilt_margin_db: float = DEFAULT_TILT_MARGIN_DB

    #: The caller's established level and brightness. ``None`` until heard.
    reference_dbfs: float | None = field(default=None, init=False)
    reference_tilt_db: float | None = field(default=None, init=False)
    speech_frames: int = field(default=0, init=False)

    @property
    def warmed_up(self) -> bool:
        """Has the caller been heard for long enough to compare against?"""
        return self.speech_frames >= WARMUP_FRAMES

    def _decay_per_frame(self) -> float:
        return DECAY_DB_PER_SECOND * (FRAME_MS / 1000.0)

    def observe(self, level: float, tilt: float) -> None:
        """Fold a speech frame into the running reference."""
        if self.reference_dbfs is None:
            self.reference_dbfs, self.reference_tilt_db = level, tilt
            self.speech_frames = 1
            return

        self.speech_frames += 1
        if level > self.reference_dbfs:
            # Rise towards a louder voice quickly: that is the caller arriving.
            self.reference_dbfs += ATTACK * (level - self.reference_dbfs)
            self.reference_tilt_db += ATTACK * (tilt - self.reference_tilt_db)
        else:
            # Fall slowly, so one loud frame cannot hold the bar high and a
            # caller who drops their voice is followed rather than gated.
            self.reference_dbfs -= self._decay_per_frame()

    def gain_for(self, frame: np.ndarray) -> float:
        """The linear gain to apply to this frame. ``1.0`` leaves it alone."""
        level = dbfs(frame)
        if level <= SILENCE_DBFS:
            # Nothing there to turn down, and gating room tone makes a live
            # line sound dead.
            return 1.0

        tilt = tilt_db(frame)

        if not self.warmed_up:
            # Learn first, act later. Acting on the first frames would gate the
            # caller's own opening words, which is the failure this must not
            # have.
            self.observe(level, tilt)
            return 1.0

        assert self.reference_dbfs is not None and self.reference_tilt_db is not None
        quiet = level < self.reference_dbfs - self.margin_db
        dull = tilt < self.reference_tilt_db - self.tilt_margin_db

        if quiet and dull:
            # Far on both counts. Do not let it move the reference: a room full
            # of distant voices would otherwise drag the bar down to itself.
            return 10.0 ** (-self.attenuation_db / 20.0)

        self.observe(level, tilt)
        return 1.0


def apply_gain(frame: np.ndarray, gain: float) -> np.ndarray:
    """Scale a frame, without wrapping around at the ends of the range."""
    if gain >= 1.0:
        return frame
    scaled = frame.astype(np.float32) * gain
    return np.clip(scaled, -32768, 32767).astype(np.int16)
