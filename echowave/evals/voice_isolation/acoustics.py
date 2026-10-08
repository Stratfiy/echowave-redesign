"""Rooms, distances, mixing and the telephone line.

Deliberately simple and deterministic: a synthetic room impulse response per
distance, level set by signal-to-noise ratio at the microphone, then the path
every Indian mobile call takes to us -- band-limited to 8 kHz and through
G.711 mu-law -- so what the filters and the lock see is what they will see on
a call.
"""

from __future__ import annotations

import numpy as np
import soxr

RATE = 16000
TELEPHONY_RATE = 8000
SPEED_OF_SOUND = 343.0


def room_impulse(distance_m: float, rng: np.random.Generator, *, rt60: float = 0.45) -> np.ndarray:
    """A plausible room response for a source ``distance_m`` from the phone.

    A direct path delayed by the distance, then an exponentially decaying
    diffuse tail with a fixed energy, so the direct-to-reverberant ratio falls
    as the source moves away -- about +10 dB at 0.3 m, 0 dB at 1 m, -10 dB at
    3 m, which is what typical living rooms measure. High frequencies decay
    faster than low ones, so far sources are duller, as they are in a room.
    """
    n = int(rt60 * 1.2 * RATE)
    delay = int(distance_m / SPEED_OF_SOUND * RATE)
    h = np.zeros(n + delay + 1, dtype=np.float32)
    h[delay] = 1.0 / max(distance_m, 0.05)
    t = np.arange(n) / RATE
    tail = rng.standard_normal(n).astype(np.float32) * np.exp(-6.9 * t / rt60)
    # Duller as it decays: a one-pole low-pass whose cutoff falls with time.
    smoothed = np.copy(tail)
    alpha = np.clip(0.2 + 0.7 * t / rt60, 0, 0.95)
    for i in range(1, n):
        smoothed[i] = (1 - alpha[i]) * tail[i] + alpha[i] * smoothed[i - 1]
    tail = smoothed / (np.sqrt(np.sum(smoothed**2)) + 1e-9)
    # A fixed reverberant energy: the critical distance is about 1 m.
    h[delay + int(0.003 * RATE) : delay + int(0.003 * RATE) + n] += tail[
        : len(h) - delay - int(0.003 * RATE)
    ]
    return h


def place(signal: np.ndarray, distance_m: float, rng: np.random.Generator) -> np.ndarray:
    """``signal`` as heard by the phone from ``distance_m`` away."""
    if distance_m <= 0.1:
        # The caller: near field, essentially dry, with the slight bass lift
        # of a handset held to the cheek.
        spectrum = np.fft.rfft(signal)
        freqs = np.fft.rfftfreq(len(signal), 1 / RATE)
        spectrum *= 1 + 0.5 / (1 + (freqs / 150.0) ** 2)
        return np.fft.irfft(spectrum, len(signal)).astype(np.float32)
    h = room_impulse(distance_m, rng)
    out = np.convolve(signal, h)[: len(signal)]
    return out.astype(np.float32)


def active_rms(signal: np.ndarray, frame: int = 320) -> float:
    """RMS over the frames that carry sound (within 40 dB of the loudest)."""
    n = len(signal) // frame
    if n == 0:
        return float(np.sqrt(np.mean(signal**2)) + 1e-12)
    frames = signal[: n * frame].reshape(n, frame)
    energy = np.mean(frames**2, axis=1)
    keep = energy > energy.max() * 1e-4
    return float(np.sqrt(np.mean(energy[keep])) + 1e-12)


def scale_to_snr(target: np.ndarray, interferer: np.ndarray, snr_db: float) -> np.ndarray:
    """Scale ``interferer`` so target-active : interferer-active is ``snr_db``."""
    gain = active_rms(target) / (active_rms(interferer) * 10 ** (snr_db / 20.0))
    return (interferer * gain).astype(np.float32)


def mu_law(signal: np.ndarray) -> np.ndarray:
    """G.711 mu-law encode then decode, 8-bit."""
    mu = 255.0
    x = np.clip(signal, -1.0, 1.0)
    q = np.sign(x) * np.log1p(mu * np.abs(x)) / np.log1p(mu)
    q = np.round(q * 127.0) / 127.0
    return (np.sign(q) * np.expm1(np.abs(q) * np.log1p(mu)) / mu).astype(np.float32)


def telephone(signal_16k: np.ndarray) -> np.ndarray:
    """The carrier's leg: 16 kHz float to 8 kHz int16 through mu-law."""
    narrow = soxr.resample(signal_16k, RATE, TELEPHONY_RATE, quality="HQ")
    return (mu_law(narrow) * 32767.0).astype(np.int16)
