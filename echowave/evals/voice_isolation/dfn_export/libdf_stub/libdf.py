"""Minimal numpy re-implementation of the `libdf` Python extension (DeepFilterLib 0.5.6).

Only what `df` (deepfilternet 0.5.6) needs for DeepFilterNet3 inference is provided.
Every function follows the Rust source of libDF v0.5.6:
  libDF/src/lib.rs        (erb_fb, DFState::new window/wnorm, frame_analysis, frame_synthesis,
                           compute_band_corr, band_mean_norm_erb, band_unit_norm,
                           MEAN_NORM_INIT, UNIT_NORM_INIT)
  libDF/src/transforms.rs (erb, erb_norm, unit_norm)
  pyDF/src/lib.rs         (Python bindings: DF class, erb, erb_inv, erb_norm, unit_norm,
                           unit_norm_init)
All arithmetic is float32 like the Rust code (state recursions are sequential over time).
"""
from __future__ import annotations

import numpy as np

MEAN_NORM_INIT = (-60.0, -90.0)
UNIT_NORM_INIT = (0.001, 0.0001)


def _freq2erb(f):
    f = np.float32(f)
    return np.float32(9.265) * np.log1p(f / np.float32(24.7 * 9.265), dtype=np.float32)


def _erb2freq(n):
    n = np.float32(n)
    return np.float32(24.7 * 9.265) * (np.exp(n / np.float32(9.265), dtype=np.float32) - np.float32(1.0))


def erb_fb_widths(sr: int, fft_size: int, nb_bands: int, min_nb_freqs: int) -> np.ndarray:
    nyq = sr // 2
    freq_width = np.float32(sr) / np.float32(fft_size)
    erb_low = _freq2erb(0.0)
    erb_high = _freq2erb(float(nyq))
    erb = [0] * nb_bands
    step = (erb_high - erb_low) / np.float32(nb_bands)
    prev_freq = 0
    freq_over = 0
    for i in range(1, nb_bands + 1):
        f = _erb2freq(erb_low + np.float32(i) * step)
        fb = int(np.round(np.float32(f / freq_width)))  # Rust f32::round (half away from 0)
        # np.round is half-to-even; guard the exact .5 case explicitly
        q = float(np.float32(f / freq_width))
        if q - np.floor(q) == 0.5:
            fb = int(np.floor(q) + 1)
        nb_freqs = fb - prev_freq - freq_over
        if nb_freqs < min_nb_freqs:
            freq_over = min_nb_freqs - nb_freqs
            nb_freqs = min_nb_freqs
        else:
            freq_over = 0
        erb[i - 1] = nb_freqs
        prev_freq = fb
    erb[nb_bands - 1] += 1
    too_large = sum(erb) - (fft_size // 2 + 1)
    if too_large > 0:
        erb[nb_bands - 1] -= too_large
    assert sum(erb) == fft_size // 2 + 1
    return np.asarray(erb, dtype=np.uint64)


def vorbis_window(fft_size: int) -> np.ndarray:
    n = np.arange(fft_size, dtype=np.float64)
    s = np.sin(0.5 * np.pi * (n + 0.5) / (fft_size // 2))
    return np.sin(0.5 * np.pi * s * s).astype(np.float32)


class DF:
    def __init__(self, sr, fft_size, hop_size, nb_bands=32, min_nb_erb_freqs=1):
        assert hop_size * 2 <= fft_size
        self._sr = int(sr)
        self._fft = int(fft_size)
        self._hop = int(hop_size)
        self._erb = erb_fb_widths(sr, fft_size, nb_bands, min_nb_erb_freqs)
        self._win = vorbis_window(fft_size)
        self._wnorm = np.float32(1.0 / (fft_size**2 / (2 * hop_size)))
        self.reset()

    # --- python binding API ---
    def reset(self):
        self._amem = np.zeros(self._fft - self._hop, np.float32)
        self._smem = np.zeros(self._fft - self._hop, np.float32)

    def erb_widths(self):
        return self._erb.copy()

    def fft_window(self):
        return self._win.copy()

    def sr(self):
        return self._sr

    def fft_size(self):
        return self._fft

    def hop_size(self):
        return self._hop

    def nb_erb(self):
        return len(self._erb)

    def analysis(self, x: np.ndarray, reset: bool = True) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        C, N = x.shape
        T = N // self._hop
        out = np.zeros((C, T, self._fft // 2 + 1), np.complex64)
        for c in range(C):
            if reset:
                self.reset()
            for t in range(T):
                frame = x[c, t * self._hop:(t + 1) * self._hop]
                buf = np.concatenate([self._amem, frame]) * self._win
                self._amem = np.concatenate([self._amem[self._hop:], frame]) if len(self._amem) > self._hop else frame.copy()
                out[c, t] = (np.fft.rfft(buf.astype(np.float32)) * self._wnorm).astype(np.complex64)
        return out

    def synthesis(self, spec: np.ndarray, reset: bool = True) -> np.ndarray:
        spec = np.asarray(spec, dtype=np.complex64)
        C, T, F = spec.shape
        h = self._hop
        out = np.zeros((C, T * h), np.float32)
        for c in range(C):
            if reset:
                self.reset()
            for t in range(T):
                # realfft inverse is unnormalised; numpy irfft divides by n -> multiply back.
                # Both ignore the imaginary part of DC and Nyquist bins.
                x = (np.fft.irfft(spec[c, t], n=self._fft) * self._fft).astype(np.float32) * self._win
                out[c, t * h:(t + 1) * h] = x[:h] + self._smem[:h]
                split = len(self._smem) - h
                mem = np.roll(self._smem, -h) if split > 0 else self._smem
                mem = mem.copy()
                mem[:split] += x[h:h + split]
                mem[split:] = x[h + split:]
                self._smem = mem
        return out


def _band_mats(widths):
    widths = np.asarray(widths, dtype=np.int64)
    F = int(widths.sum())
    M = np.zeros((F, len(widths)), np.float32)
    o = 0
    for i, w in enumerate(widths):
        M[o:o + w, i] = 1.0 / np.float32(w)
        o += w
    return M


def erb(spec, erb_fb, db: bool = True):
    spec = np.asarray(spec)
    p = (spec.real.astype(np.float32) ** 2 + spec.imag.astype(np.float32) ** 2).astype(np.float32)
    out = p @ _band_mats(erb_fb)
    if db:
        out = (np.log10(out + np.float32(1e-10)) * np.float32(10.0)).astype(np.float32)
    return out.astype(np.float32)


def erb_inv(gains, erb_fb):
    widths = np.asarray(erb_fb, dtype=np.int64)
    return np.repeat(np.asarray(gains, np.float32), widths, axis=-1)


def erb_norm(erb_feat, alpha, state=None):
    x = np.array(erb_feat, dtype=np.float32, copy=True)  # [C, T, E]
    C, T, E = x.shape
    a = np.float32(alpha)
    if state is None:
        state = np.tile(np.linspace(MEAN_NORM_INIT[0], MEAN_NORM_INIT[1], E, dtype=np.float32), (C, 1))
    s = np.array(state, np.float32)
    for t in range(T):
        s = x[:, t] * (np.float32(1) - a) + s * a
        x[:, t] = (x[:, t] - s) / np.float32(40.0)
    return x


def unit_norm(spec, alpha, state=None):
    x = np.array(spec, dtype=np.complex64, copy=True)  # [C, T, F]
    C, T, F = x.shape
    a = np.float32(alpha)
    if state is None:
        state = np.tile(np.linspace(UNIT_NORM_INIT[0], UNIT_NORM_INIT[1], F, dtype=np.float32), (C, 1))
    s = np.array(state, np.float32)
    for t in range(T):
        s = np.abs(x[:, t]).astype(np.float32) * (np.float32(1) - a) + s * a
        x[:, t] = x[:, t] / np.sqrt(s)
    return x


def unit_norm_init(num_freq_bins):
    return np.linspace(UNIT_NORM_INIT[0], UNIT_NORM_INIT[1], num_freq_bins, dtype=np.float32).reshape(1, -1)
