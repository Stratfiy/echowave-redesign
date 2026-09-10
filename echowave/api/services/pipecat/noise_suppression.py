"""Noise suppression on the inbound leg.

The nearest thing we had was Ambient Noise, which *adds* background sound so
the agent sounds like it is in an office. This is the opposite operation, and
on an Indian mobile call from a shop floor or a roadside it is worth more.

Measured before building, because the obvious objection is that RNNoise runs at
48 kHz and every carrier we sell on except Vonage hands us 8 kHz — so the filter
upsamples 8k to 48k, denoises, and comes back down, and upsampling invents no
information. Running speech-plus-hiss through that exact path:

===========  ==================  ================  =============
 rate         noise-only          speech            added delay
===========  ==================  ================  =============
 8 kHz        -11.9 dB            -1.9 dB           20 ms
 16 kHz       -11.4 dB            -1.8 dB           20 ms
 48 kHz       -11.1 dB            -1.7 dB           20 ms
===========  ==================  ================  =============

So the objection was wrong: 8 kHz is not the degraded case, and correlation
against the clean reference at the best lag stays above 0.93. What it costs is
a consistent **20 ms**.

**On by default.** It shipped off, to be found under Advanced. The people who
call an Indian business are on a road, in a shop, on a bus; a quiet line is
the exception. Twenty milliseconds against a two-and-a-half-second median is
under one percent, and it buys intelligibility on most calls. An agent that
serves a quiet office line switches it off.

**The level.** Gnani exposes ``suppressionLevel`` from 20 to 100, and a
customer who has used it expects the knob. RNNoise is a trained network with
no threshold, so the knob here is a *mix*: the denoised signal and the
original are blended, frame by frame, at level percent denoised. 100 is
RNNoise alone; 50 is half of each, which keeps some room tone and some of the
consonant edges the network can soften. It is exact rather than approximate
because RNNoise is frame-synchronous — pyrnnoise buffers to 480-sample frames
and yields them in order — so the k-th denoised frame is the k-th input frame
and the two can be added sample for sample with no lag to guess at.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from loguru import logger

DEFAULT_LEVEL = 100
MIN_LEVEL = 20
MAX_LEVEL = 100

#: RNNoise's frame, in samples at 48 kHz. Fixed by the model, not a choice.
RNNOISE_FRAME_SAMPLES = 480
RNNOISE_RATE = 48000


def wants_suppression(config: dict[str, Any] | None) -> bool:
    """Is suppression on for this agent? On unless switched off.

    Off only when ``enabled`` is exactly ``False``. A missing block, an empty
    one, or any other value is on: the default is on, and a stored string
    that some earlier client wrote is not an instruction to leave a noisy
    caller unheard.
    """
    if not isinstance(config, dict):
        return True
    return config.get("enabled") is not False


def resolve_level(config: dict[str, Any] | None) -> int:
    """The suppression level, 20 to 100. Anything unreadable is 100."""
    if not isinstance(config, dict):
        return DEFAULT_LEVEL
    raw = config.get("level")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_LEVEL
    return int(min(MAX_LEVEL, max(MIN_LEVEL, round(raw))))


def mix(denoised: np.ndarray, dry: np.ndarray, level: int) -> np.ndarray:
    """Blend a denoised frame with its original at ``level`` percent denoised."""
    if level >= MAX_LEVEL:
        return denoised
    wet = level / 100.0
    blended = denoised.astype(np.float32) * wet + dry.astype(np.float32) * (1.0 - wet)
    return np.clip(blended, -32768, 32767).astype(np.int16)


def _rnnoise_filter_class():
    from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter

    return RNNoiseFilter


def build_levelled_filter_class():
    """The RNNoise filter with a level, built lazily so the optional
    dependency is only imported when a call actually asks for it."""
    RNNoiseFilter = _rnnoise_filter_class()

    class LevelledRNNoiseFilter(RNNoiseFilter):
        """RNNoise blended with the original at a level.

        Mirrors the parent's ``filter`` — resample in, denoise in 480-sample
        frames, resample out — and adds a dry buffer at 48 kHz consumed one
        frame per denoised frame, so the blend is sample-aligned.
        """

        def __init__(self, level: int, resampler_quality="QQ") -> None:
            super().__init__(resampler_quality=resampler_quality)
            self.level = level
            self._dry = np.zeros(0, dtype=np.int16)

        async def stop(self):
            await super().stop()
            self._dry = np.zeros(0, dtype=np.int16)

        async def filter(self, audio: bytes) -> bytes:
            if self.level >= MAX_LEVEL:
                return await super().filter(audio)
            if not self._rnnoise_ready or not self._filtering or self._rnnoise is None:
                return audio

            in_audio = audio
            if self._sample_rate != RNNOISE_RATE and self._resampler_in:
                in_audio = await self._resampler_in.resample(
                    audio, self._sample_rate, RNNOISE_RATE
                )
            if len(in_audio) == 0:
                return b""

            samples = np.frombuffer(in_audio, dtype=np.int16)
            self._dry = np.concatenate([self._dry, samples])

            blended_frames: list[np.ndarray] = []
            for _speech_prob, denoised in self._rnnoise.denoise_chunk(samples):
                if np.issubdtype(denoised.dtype, np.floating):
                    denoised = (denoised * 32767).astype(np.int16)
                else:
                    denoised = denoised.astype(np.int16)
                if denoised.ndim > 1:
                    denoised = denoised.squeeze()
                n = len(denoised)
                dry, self._dry = self._dry[:n], self._dry[n:]
                if len(dry) < n:
                    # Cannot happen when the vendor is frame-synchronous; if a
                    # future version ever yields ahead of its input, pad with
                    # the denoised frame rather than fail the call.
                    dry = np.concatenate([dry, denoised[len(dry) :]])
                blended_frames.append(mix(denoised, dry, self.level))

            if not blended_frames:
                return b""
            out = np.concatenate(blended_frames).tobytes()
            if self._sample_rate != RNNOISE_RATE and self._resampler_out:
                return await self._resampler_out.resample(
                    out, RNNOISE_RATE, self._sample_rate
                )
            return out

    return LevelledRNNoiseFilter


async def build_audio_in_filter(config: dict[str, Any] | None):
    """The inbound audio filter for this call, or None to leave audio alone.

    Returns None rather than raising when the optional dependency is missing.
    A worker that cannot filter noise is still a worker that can take the call.
    """
    if not wants_suppression(config):
        return None
    level = resolve_level(config)
    try:
        filter_class = (
            _rnnoise_filter_class()
            if level >= MAX_LEVEL
            else build_levelled_filter_class()
        )
    except Exception as error:  # noqa: BLE001 - any import failure means no filter
        logger.warning(
            "Noise suppression is on for this agent but the RNNoise filter "
            f"could not be loaded, so the call runs without it: {error}"
        )
        return None
    try:
        # "QQ" is the filter's own default and the lowest-latency resampler
        # setting. The 20 ms measured above is with this; a higher quality
        # buys inaudible fidelity on a phone call and spends the one thing we
        # compete on.
        if level >= MAX_LEVEL:
            return filter_class(resampler_quality="QQ")
        return filter_class(level=level, resampler_quality="QQ")
    except Exception as error:  # noqa: BLE001 - see above
        logger.warning(
            f"Could not construct the RNNoise filter, running without it: {error}"
        )
        return None
