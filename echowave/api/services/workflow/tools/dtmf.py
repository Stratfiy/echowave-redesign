"""Pure DTMF tone generation as PCM-16 audio.

No pipecat or telephony-provider dependency here — the generated audio is
injected into the live call via api/services/pipecat/audio_playback.py's
play_audio(), the same mechanism already used for pre-recorded audio
playback, so it reaches the caller as real audio without passing through STT.
"""

from __future__ import annotations

import numpy as np

# Standard DTMF dual-tone frequency pairs (low, high) in Hz.
DTMF_FREQUENCIES: dict[str, tuple[int, int]] = {
    "1": (697, 1209), "2": (697, 1336), "3": (697, 1477), "A": (697, 1633),
    "4": (770, 1209), "5": (770, 1336), "6": (770, 1477), "B": (770, 1633),
    "7": (852, 1209), "8": (852, 1336), "9": (852, 1477), "C": (852, 1633),
    "*": (941, 1209), "0": (941, 1336), "#": (941, 1477), "D": (941, 1633),
}  # fmt: skip


class InvalidDtmfDigits(ValueError):
    """Raised when a digit string contains anything outside 0-9 * # A-D."""


def generate_dtmf_tone_pcm16(
    digits: str,
    sample_rate: int,
    *,
    tone_duration_ms: int = 200,
    pause_duration_ms: int = 100,
    amplitude: float = 0.5,
) -> bytes:
    """Generate mono 16-bit PCM dual-tone audio for a digit string.

    Raises InvalidDtmfDigits if `digits` is empty or contains unsupported
    characters.
    """
    cleaned = digits.strip().upper()
    if not cleaned:
        raise InvalidDtmfDigits("No digits provided")

    invalid = sorted({d for d in cleaned if d not in DTMF_FREQUENCIES})
    if invalid:
        raise InvalidDtmfDigits(f"Unsupported DTMF digit(s): {', '.join(invalid)}")

    tone_samples = int(sample_rate * tone_duration_ms / 1000)
    pause_samples = int(sample_rate * pause_duration_ms / 1000)
    t = np.arange(tone_samples) / sample_rate

    chunks: list[np.ndarray] = []
    silence = np.zeros(pause_samples)
    for digit in cleaned:
        low_freq, high_freq = DTMF_FREQUENCIES[digit]
        tone = (
            amplitude
            * (np.sin(2 * np.pi * low_freq * t) + np.sin(2 * np.pi * high_freq * t))
            / 2
        )
        chunks.append(tone)
        chunks.append(silence)

    audio = np.concatenate(chunks)
    pcm16 = (audio * 32767).astype(np.int16)
    return pcm16.tobytes()
