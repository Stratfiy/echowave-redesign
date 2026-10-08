"""DeepFilterNet3 as the inbound noise filter, in place of RNNoise.

RNNoise is a 2018 network the size of a thumbnail; DeepFilterNet3 (Schröter et
al., 2023) is the open model that replaced it in most open-source stacks. Both
work at 48 kHz, so this takes the same path RNNoise does on a phone line:
resample 8 kHz up to 48 kHz, filter, come back down.

**What it is not.** Like RNNoise it is trained to keep speech and remove what
is not speech. It removes traffic and fans far better; a person talking behind
the caller is still speech and comes through. For background voices see
``caller_voice_lock``.

**How it runs.** The official package needs torch, numpy below 2 and a Rust
extension with no wheels for Python 3.13, so none of it ships. The network was
exported once to a *streaming* ONNX graph (one 10 ms hop per call, every
convolution history, the deep filter's taps and the three GRU states carried
explicitly) by ``evals/voice_isolation/dfn_export``, which also verifies it:
the stream matches the official offline ``df.enhance.enhance`` to float32
round-off. At run time it is numpy and onnxruntime, both already in the image.

* Algorithmic delay: 30 ms at 48 kHz (one STFT overlap plus two frames of
  lookahead), on top of the resamplers. RNNoise's whole path measures 20 ms.
* CPU: about 1 ms per 10 ms hop on one core, ten times RNNoise; see the
  evaluation for the per-call figure on 8 kHz audio.
* Licence: DeepFilterNet code is MIT or Apache-2.0 at your option; the weights
  ship in the same repository under the same terms (``MODEL_LICENCE``).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from pipecat.audio.filters.base_audio_filter import BaseAudioFilter
from pipecat.frames.frames import FilterControlFrame, FilterEnableFrame

#: The flag in ``services/features.py``.
FEATURE = "deepfilternet_filter"

DEFAULT_MODEL_PATH = "/app/models/voice_isolation/dfn3_streaming.onnx"

MODEL_LICENCE = (
    "DeepFilterNet3 (github.com/Rikorose/DeepFilterNet v0.5.6, checkpoint "
    "model_120.ckpt.best sha256 23b92884...e6003): code and weights MIT OR "
    "Apache-2.0. Streaming ONNX export by evals/voice_isolation/dfn_export."
)

RATE = 48000
FFT = 960
HOP = 480
NB_ERB = 32
NB_DF = 96
NBINS = FFT // 2 + 1
LOOKAHEAD = 2
#: Output sample n is input sample n - DELAY_SAMPLES, at 48 kHz.
DELAY_SAMPLES = (FFT - HOP) + LOOKAHEAD * HOP

#: ``df.utils.get_norm_alpha``: round(exp(-hop / sr / tau), 3) with tau = 1 s.
ALPHA = np.float32(0.99)

#: libDF's ``erb_fb`` for 48 kHz, a 960-point FFT, 32 bands, at least 2 bins
#: each. Fixed by the model.
ERB_WIDTHS = (
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 5, 5, 7, 7, 8, 10, 12, 13, 15, 18, 20,
    24, 28, 31, 37, 42, 50, 56, 67,
)  # fmt: skip

STATE_SHAPES = {
    "erb_hist": (1, 1, 2, NB_ERB),
    "spec_feat_hist": (1, 2, 2, NB_DF),
    "c0_hist": (1, 64, 4, NB_DF),
    "spec_hist": (1, 1, 4, NBINS, 2),
    "h_enc": (1, 1, 256),
    "h_erb": (2, 1, 256),
    "h_df": (2, 1, 256),
}


def resolve_model_path() -> str:
    from api import constants

    return constants.DEEPFILTERNET_MODEL_PATH or DEFAULT_MODEL_PATH


def _vorbis_window() -> np.ndarray:
    i = np.arange(FFT, dtype=np.float64)
    s = np.sin(0.5 * np.pi * (i + 0.5) / (FFT // 2))
    return np.sin(0.5 * np.pi * s * s).astype(np.float32)


_sessions: dict[str, Any] = {}
_sessions_lock = threading.Lock()


def _session(model_path: str):
    """One onnxruntime session per process, one thread per run."""
    with _sessions_lock:
        session = _sessions.get(model_path)
        if session is None:
            import onnxruntime as ort

            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            session = ort.InferenceSession(
                model_path, sess_options=options, providers=["CPUExecutionProvider"]
            )
            _sessions[model_path] = session
        return session


class DeepFilterNetStream:
    """One call's denoiser state: 48 kHz float in, 48 kHz float out.

    ``process`` takes any number of samples and returns every completed
    480-sample hop; with 20 ms chunks nothing waits in the buffer. The signal
    chain per hop is libDF's: analysis STFT, ERB and complex features with
    exponential normalisation, the ONNX hop, synthesis overlap-add.
    """

    def __init__(self, model_path: str) -> None:
        self._session = _session(model_path)
        self._outputs = [o.name for o in self._session.get_outputs()]
        self._window = _vorbis_window()
        self._analysis_norm = np.float32(1.0 / (FFT * FFT / (2 * HOP)))
        self._synthesis_scale = self._window * np.float32(FFT)
        widths = np.asarray(ERB_WIDTHS)
        erb = np.zeros((NBINS, NB_ERB), np.float32)
        erb[np.arange(NBINS), np.repeat(np.arange(NB_ERB), widths)] = 1.0 / np.repeat(
            widths, widths
        ).astype(np.float32)
        self._erb = erb
        self.reset()

    def reset(self) -> None:
        self._pending = np.zeros(0, np.float32)
        self._analysis_mem = np.zeros(FFT - HOP, np.float32)
        self._synthesis_mem = np.zeros(FFT - HOP, np.float32)
        self._erb_norm = np.linspace(-60.0, -90.0, NB_ERB, dtype=np.float32)
        self._unit_norm = np.linspace(0.001, 0.0001, NB_DF, dtype=np.float32)
        self._states = {n: np.zeros(s, np.float32) for n, s in STATE_SHAPES.items()}
        self._hops = 0

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        buf = np.concatenate((self._pending, x)) if self._pending.size else x
        n = buf.size // HOP
        out = np.empty(n * HOP, np.float32)
        for i in range(n):
            out[i * HOP : (i + 1) * HOP] = self._hop(buf[i * HOP : (i + 1) * HOP])
        self._pending = buf[n * HOP :].copy()
        return out

    def _hop(self, frame: np.ndarray) -> np.ndarray:
        buf = np.concatenate((self._analysis_mem, frame))
        self._analysis_mem = frame.copy()
        spec = np.fft.rfft(buf * self._window) * self._analysis_norm
        re = spec.real.astype(np.float32)
        im = spec.imag.astype(np.float32)
        spec_ri = np.stack((re, im), -1)

        one = np.float32(1)
        erb = np.log10(
            (re * re + im * im) @ self._erb + np.float32(1e-10)
        ) * np.float32(10)
        self._erb_norm = erb * (one - ALPHA) + self._erb_norm * ALPHA
        feat_erb = (erb - self._erb_norm) / np.float32(40)

        r96, i96 = re[:NB_DF], im[:NB_DF]
        self._unit_norm = (
            np.sqrt(r96 * r96 + i96 * i96) * (one - ALPHA) + self._unit_norm * ALPHA
        )
        inv = one / np.sqrt(self._unit_norm)

        states = self._states
        hop = self._hops
        self._hops += 1
        if hop < LOOKAHEAD:
            # The first frames only fill the lookahead, as the offline model
            # drops them; only the spectrum history moves.
            history = states["spec_hist"]
            history[:, :, :-1] = history[:, :, 1:]
            history[0, 0, -1] = spec_ri
            y = np.zeros(FFT, np.float32)
        else:
            feeds = {
                "feat_erb": feat_erb.reshape(1, 1, 1, NB_ERB),
                "feat_spec": np.stack((r96 * inv, i96 * inv)).reshape(1, 2, 1, NB_DF),
                "spec": spec_ri.reshape(1, 1, 1, NBINS, 2),
                **states,
            }
            result = self._session.run(self._outputs, feeds)
            enhanced = result[0].reshape(NBINS, 2)
            for name, value in zip(STATE_SHAPES, result[2:], strict=True):
                states[name] = value
            y = (
                np.fft.irfft(enhanced[:, 0] + 1j * enhanced[:, 1], n=FFT).astype(
                    np.float32
                )
                * self._synthesis_scale
            )
        out = y[:HOP] + self._synthesis_mem
        self._synthesis_mem = y[HOP:].copy()
        return out


class DeepFilterNetFilter(BaseAudioFilter):
    """A pipecat input filter: the transport's rate in, DeepFilterNet3 at
    48 kHz, the transport's rate out.

    ``level`` blends the denoised signal with the original, as the RNNoise
    filter's does; the original is delayed by the network's 30 ms first so
    the two add sample for sample.
    """

    def __init__(
        self, model_path: str, *, level: int = 100, resampler_quality="QQ"
    ) -> None:
        self._model_path = model_path
        self._level = level
        self._resampler_quality = resampler_quality
        self._filtering = True
        self._sample_rate = 0
        self._stream: DeepFilterNetStream | None = None
        self._resampler_in = None
        self._resampler_out = None
        self._dry = np.zeros(DELAY_SAMPLES, np.float32)

    async def start(self, sample_rate: int):
        self._sample_rate = sample_rate
        try:
            self._stream = DeepFilterNetStream(self._model_path)
        except Exception as error:  # noqa: BLE001 - the call goes on unfiltered
            logger.error(
                f"DeepFilterNet could not start, audio passes unfiltered: {error}"
            )
            self._stream = None
            return
        if sample_rate != RATE:
            from pipecat.audio.resamplers.soxr_stream_resampler import (
                SOXRStreamAudioResampler,
            )

            self._resampler_in = SOXRStreamAudioResampler(
                quality=self._resampler_quality
            )
            self._resampler_out = SOXRStreamAudioResampler(
                quality=self._resampler_quality
            )

    async def stop(self):
        self._stream = None
        self._resampler_in = None
        self._resampler_out = None
        self._dry = np.zeros(DELAY_SAMPLES, np.float32)

    async def process_frame(self, frame: FilterControlFrame):
        if isinstance(frame, FilterEnableFrame):
            self._filtering = frame.enable

    async def filter(self, audio: bytes) -> bytes:
        if self._stream is None or not self._filtering:
            return audio
        data = audio
        if self._resampler_in is not None:
            data = await self._resampler_in.resample(audio, self._sample_rate, RATE)
        if not data:
            return b""
        samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        wet = self._stream.process(samples)
        if self._level < 100:
            self._dry = np.concatenate((self._dry, samples))
            dry, self._dry = self._dry[: wet.size], self._dry[wet.size :]
            mix = self._level / 100.0
            wet = wet * mix + dry * (1.0 - mix)
        if not wet.size:
            return b""
        out = (np.clip(wet, -1.0, 32767 / 32768) * 32768.0).astype(np.int16).tobytes()
        if self._resampler_out is not None:
            return await self._resampler_out.resample(out, RATE, self._sample_rate)
        return out


def build_filter(*, level: int = 100, model_path: str | None = None):
    """The DeepFilterNet filter, or None when its model is not on this box.

    None, not an exception: the caller falls back to RNNoise, and a worker
    without the model is still a worker that can take the call.
    """
    path = model_path or resolve_model_path()
    if not Path(path).is_file():
        logger.warning(
            f"deepfilternet_filter is on but the model is missing at {path}: "
            "using RNNoise"
        )
        return None
    return DeepFilterNetFilter(path, level=level, resampler_quality="QQ")
