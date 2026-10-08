"""True-streaming DeepFilterNet3 denoiser: numpy + onnxruntime only.

    from dfn_stream import DeepFilterNetStream
    dn = DeepFilterNetStream("dfn3_streaming.onnx")
    out = dn.process(chunk_48k_float32)   # any chunk length; returns len == 480 * (#hops completed)
    ...
    dn.reset()                            # new call / stream

Signal chain per 10 ms hop (480 samples @ 48 kHz), identical to libDF 0.5.6 + deepfilternet 0.5.6:
  analysis STFT (960-pt rFFT, Vorbis window, x 1/960)  ->  ERB features (32 bands, dB,
  exponential mean-norm alpha=0.99, /40) + complex features (bins 0..95, exponential unit-norm
  alpha=0.99)  ->  ONNX hop (encoder, ERB-mask decoder, deep-filter decoder, mask + 5-tap deep
  filter, all temporal context carried as explicit state)  ->  synthesis (irFFT x 960, Vorbis
  window, overlap-add).

Algorithmic latency: 1440 samples = 30 ms at 48 kHz
  = 480 (STFT overlap-add, n_fft - hop) + 2 x 480 (model lookahead: conv_lookahead = df_lookahead = 2).
I.e. output sample n of this stream corresponds to input sample n - 1440, and it equals the
official offline `df.enhance.enhance()` output sample n - 1440 (see verify.py / NOTES.md).
Additionally a caller sees up to 479 samples of buffering latency if it feeds chunks that are not
multiples of 480 (e.g. 20 ms = 960 samples -> no extra buffering).
"""

from __future__ import annotations

import numpy as np
import onnxruntime as ort

SR = 48000
FFT = 960
HOP = 480
NB_ERB = 32
NB_DF = 96
NBINS = FFT // 2 + 1
ALPHA = np.float32(
    0.99
)  # df.utils.get_norm_alpha(): round(exp(-hop/sr/tau), 3) with tau=1 s
LOOKAHEAD = 2
DELAY_SAMPLES = (FFT - HOP) + LOOKAHEAD * HOP  # 1440

# ERB band widths for sr=48000, fft=960, nb_bands=32, min_nb_erb_freqs=2 (libDF erb_fb()).
# Recomputed below by _erb_widths() and asserted to match.
ERB_WIDTHS = (
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    2,
    5,
    5,
    7,
    7,
    8,
    10,
    12,
    13,
    15,
    18,
    20,
    24,
    28,
    31,
    37,
    42,
    50,
    56,
    67,
)

STATE_NAMES = (
    "erb_hist",
    "spec_feat_hist",
    "c0_hist",
    "spec_hist",
    "h_enc",
    "h_erb",
    "h_df",
)
STATE_SHAPES = {
    "erb_hist": (1, 1, 2, 32),
    "spec_feat_hist": (1, 2, 2, 96),
    "c0_hist": (1, 64, 4, 96),
    "spec_hist": (1, 1, 4, 481, 2),
    "h_enc": (1, 1, 256),
    "h_erb": (2, 1, 256),
    "h_df": (2, 1, 256),
}


def _erb_widths(sr=SR, fft=FFT, nb=NB_ERB, min_nb=2):
    """libDF erb_fb() in float32 arithmetic."""
    f32 = np.float32
    freq2erb = lambda f: f32(9.265) * np.log1p(f32(f) / f32(24.7 * 9.265), dtype=f32)  # noqa: E731
    erb2freq = lambda n: (
        f32(24.7 * 9.265) * (np.exp(f32(n) / f32(9.265), dtype=f32) - f32(1))
    )  # noqa: E731
    lo, hi = freq2erb(0.0), freq2erb(float(sr // 2))
    step = (hi - lo) / f32(nb)
    fw = f32(sr) / f32(fft)
    out, prev, over = [], 0, 0
    for i in range(1, nb + 1):
        q = float(f32(erb2freq(lo + f32(i) * step) / fw))
        fb = int(np.floor(q + 0.5))  # Rust round(): half away from zero (q >= 0)
        n = fb - prev - over
        if n < min_nb:
            over, n = min_nb - n, min_nb
        else:
            over = 0
        out.append(n)
        prev = fb
    out[-1] += 1
    too = sum(out) - (fft // 2 + 1)
    if too > 0:
        out[-1] -= too
    return tuple(out)


def vorbis_window(n=FFT):
    i = np.arange(n, dtype=np.float64)
    s = np.sin(0.5 * np.pi * (i + 0.5) / (n // 2))
    return np.sin(0.5 * np.pi * s * s).astype(np.float32)


class DeepFilterNetStream:
    def __init__(self, model_path: str = "dfn3_streaming.onnx", intra_threads: int = 1):
        assert _erb_widths() == ERB_WIDTHS
        so = ort.SessionOptions()
        so.intra_op_num_threads = intra_threads
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sess = ort.InferenceSession(
            model_path, so, providers=["CPUExecutionProvider"]
        )
        self._out_names = [o.name for o in self.sess.get_outputs()]
        # precomputed DSP constants
        self.win = vorbis_window()
        self.wnorm = np.float32(1.0 / (FFT * FFT / (2 * HOP)))  # 1/960, analysis only
        w = np.asarray(ERB_WIDTHS)
        band = np.zeros((NBINS, NB_ERB), np.float32)
        band[np.arange(NBINS), np.repeat(np.arange(NB_ERB), w)] = 1.0 / np.repeat(
            w, w
        ).astype(np.float32)
        self.erb_mat = band
        self.syn_scale = self.win * np.float32(
            FFT
        )  # irfft(n) * n (realfft unnormalised) * window
        self.reset()

    # ------------------------------------------------------------------ public API
    def reset(self) -> None:
        self._inbuf = np.zeros(0, np.float32)
        self._amem = np.zeros(FFT - HOP, np.float32)
        self._smem = np.zeros(FFT - HOP, np.float32)
        self._erb_state = np.linspace(-60.0, -90.0, NB_ERB, dtype=np.float32)
        self._unit_state = np.linspace(0.001, 0.0001, NB_DF, dtype=np.float32)
        self._states = {n: np.zeros(STATE_SHAPES[n], np.float32) for n in STATE_NAMES}
        self._hop_idx = 0
        self.last_lsnr = 0.0

    @property
    def delay_samples(self) -> int:
        return DELAY_SAMPLES

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Feed any number of 48 kHz mono float32 samples; returns all completed 480-sample hops."""
        x = np.asarray(samples, dtype=np.float32).reshape(-1)
        buf = np.concatenate((self._inbuf, x)) if self._inbuf.size else x
        n_hops = buf.size // HOP
        out = np.empty(n_hops * HOP, np.float32)
        for i in range(n_hops):
            out[i * HOP : (i + 1) * HOP] = self._process_hop(
                buf[i * HOP : (i + 1) * HOP]
            )
        self._inbuf = buf[n_hops * HOP :].copy()
        return out

    def flush(self) -> np.ndarray:
        """Drain: pad the partial hop and push DELAY_SAMPLES of zeros through."""
        pad = (-self._inbuf.size) % HOP
        return self.process(np.zeros(pad + DELAY_SAMPLES, np.float32))

    # ------------------------------------------------------------------ internals
    def _process_hop(self, frame: np.ndarray) -> np.ndarray:
        # --- analysis (libDF frame_analysis) ---
        buf = np.concatenate((self._amem, frame))
        self._amem = frame.copy()  # fft - hop == hop
        spec = np.fft.rfft(buf * self.win) * self.wnorm  # complex128 of float32 data
        re = spec.real.astype(np.float32)
        im = spec.imag.astype(np.float32)
        spec_ri = np.stack((re, im), -1)  # [481, 2]

        # --- ERB feature (compute_band_corr, 10*log10(x+1e-10), band_mean_norm_erb) ---
        a = ALPHA
        e = (re * re + im * im) @ self.erb_mat
        e = np.log10(e + np.float32(1e-10)) * np.float32(10.0)
        self._erb_state = e * (np.float32(1) - a) + self._erb_state * a
        feat_erb = (e - self._erb_state) / np.float32(40.0)

        # --- complex feature (band_unit_norm on bins 0..95) ---
        r96, i96 = re[:NB_DF], im[:NB_DF]
        mag = np.sqrt(r96 * r96 + i96 * i96)
        self._unit_state = mag * (np.float32(1) - a) + self._unit_state * a
        inv = np.float32(1) / np.sqrt(self._unit_state)

        st = self._states
        k = self._hop_idx
        self._hop_idx = k + 1
        if k < LOOKAHEAD:
            # The offline model discards the first `lookahead` feature frames (pad_feat) and
            # zero-pads its causal convs; only the spectrum history advances.
            sh = st["spec_hist"]
            sh[:, :, :-1] = sh[:, :, 1:]
            sh[0, 0, -1] = spec_ri
            enh = None
        else:
            feeds = {
                "feat_erb": feat_erb.reshape(1, 1, 1, NB_ERB),
                "feat_spec": np.stack((r96 * inv, i96 * inv)).reshape(1, 2, 1, NB_DF),
                "spec": spec_ri.reshape(1, 1, 1, NBINS, 2),
            }
            feeds.update(st)
            res = self.sess.run(self._out_names, feeds)
            enh = res[0].reshape(NBINS, 2)
            self.last_lsnr = float(res[1].reshape(-1)[0])
            for n, v in zip(STATE_NAMES, res[2:]):
                st[n] = v

        # --- synthesis (libDF frame_synthesis) ---
        if enh is None:
            y = np.zeros(FFT, np.float32)
        else:
            y = (
                np.fft.irfft(enh[:, 0] + 1j * enh[:, 1], n=FFT).astype(np.float32)
                * self.syn_scale
            )
        out = y[:HOP] + self._smem
        self._smem = y[HOP:].copy()
        return out
