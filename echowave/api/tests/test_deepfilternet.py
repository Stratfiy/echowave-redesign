"""DeepFilterNet3 in RNNoise's place: the DSP constants, the blend, and the
fallbacks. The model file is not in the repository; nothing here needs it."""

from __future__ import annotations

import numpy as np
import pytest

from api.services import features
from api.services.pipecat import deepfilternet as dfn
from api.services.pipecat import noise_suppression


def _libdf_erb_widths(sr=48000, fft=960, nb=32, min_nb=2):
    """libDF's ``erb_fb`` (v0.5.6, float32 arithmetic), to check the table."""
    f32 = np.float32

    def freq2erb(f):
        return f32(9.265) * np.log1p(f32(f) / f32(24.7 * 9.265), dtype=f32)

    def erb2freq(n):
        return f32(24.7 * 9.265) * (np.exp(f32(n) / f32(9.265), dtype=f32) - f32(1))

    lo, hi = freq2erb(0.0), freq2erb(float(sr // 2))
    step = (hi - lo) / f32(nb)
    width = f32(sr) / f32(fft)
    out, prev, over = [], 0, 0
    for i in range(1, nb + 1):
        q = float(f32(erb2freq(lo + f32(i) * step) / width))
        edge = int(np.floor(q + 0.5))
        n = edge - prev - over
        if n < min_nb:
            over, n = min_nb - n, min_nb
        else:
            over = 0
        out.append(n)
        prev = edge
    out[-1] += 1
    extra = sum(out) - (fft // 2 + 1)
    if extra > 0:
        out[-1] -= extra
    return tuple(out)


class TestTheModelsDsp:
    def test_erb_bands_are_libdfs(self):
        assert dfn.ERB_WIDTHS == _libdf_erb_widths()
        assert sum(dfn.ERB_WIDTHS) == dfn.NBINS

    def test_the_window_reconstructs_perfectly(self):
        """Vorbis at 50% overlap: w[n]^2 + w[n+hop]^2 = 1, so analysis and
        synthesis together are transparent."""
        w = dfn._vorbis_window().astype(np.float64)
        assert np.allclose(w[: dfn.HOP] ** 2 + w[dfn.HOP :] ** 2, 1.0, atol=1e-6)

    def test_delay_is_thirty_milliseconds(self):
        assert dfn.DELAY_SAMPLES / dfn.RATE == pytest.approx(0.030)


class _Echo:
    """Stands in for the network: hands back the input delayed by exactly the
    model's delay, a hop at a time, like the real stream."""

    def __init__(self):
        self._held = np.zeros(dfn.DELAY_SAMPLES, np.float32)
        self._pending = np.zeros(0, np.float32)

    def process(self, x):
        buf = np.concatenate([self._pending, x])
        n = buf.size // dfn.HOP * dfn.HOP
        self._pending = buf[n:]
        joined = np.concatenate([self._held, buf[:n]])
        out, self._held = joined[:n], joined[n:]
        return out


class TestTheFilter:
    def _filter(self, level):
        f = dfn.DeepFilterNetFilter("unused", level=level)
        f._sample_rate = dfn.RATE  # no resampling in the test
        f._stream = _Echo()
        return f

    async def test_the_blend_is_sample_aligned(self):
        """At 50% the dry signal is delayed by the network's 30 ms first, so a
        transparent network gives back the input, not a comb filter."""
        f = self._filter(50)
        x = (np.sin(np.arange(4800) * 0.05) * 10000).astype(np.int16)
        out = np.frombuffer(await f.filter(x.tobytes()), dtype=np.int16)
        assert out.size == x.size
        d = dfn.DELAY_SAMPLES
        assert np.abs(out[d:].astype(int) - x[:-d].astype(int)).max() <= 1

    async def test_disabled_is_untouched(self):
        f = self._filter(100)
        f._filtering = False
        x = np.arange(960, dtype=np.int16).tobytes()
        assert await f.filter(x) == x

    async def test_a_model_that_will_not_load_leaves_audio_alone(self):
        f = dfn.DeepFilterNetFilter("/nowhere/dfn.onnx")
        await f.start(8000)
        x = np.arange(160, dtype=np.int16).tobytes()
        assert await f.filter(x) == x


class TestChoosingTheFilter:
    def test_missing_model_is_none(self, tmp_path):
        assert dfn.build_filter(model_path=str(tmp_path / "missing.onnx")) is None

    async def test_off_is_rnnoise(self, monkeypatch):
        monkeypatch.setattr(features, "is_on", lambda name, org=None: False)
        monkeypatch.setattr(dfn, "build_filter", lambda **kw: pytest.fail("built DFN"))

        class Works:
            def __init__(self, resampler_quality="QQ"):
                pass

        monkeypatch.setattr(noise_suppression, "_rnnoise_filter_class", lambda: Works)
        assert isinstance(await noise_suppression.build_audio_in_filter(None, 3), Works)

    async def test_on_is_deepfilternet_at_the_agents_level(self, monkeypatch):
        monkeypatch.setattr(
            features, "is_on", lambda name, org=None: name == dfn.FEATURE and org == 3
        )
        built = {}

        def fake_build(**kw):
            built.update(kw)
            return "dfn"

        monkeypatch.setattr(dfn, "build_filter", fake_build)
        got = await noise_suppression.build_audio_in_filter({"level": 60}, 3)
        assert got == "dfn" and built["level"] == 60

    async def test_on_without_the_model_falls_back_to_rnnoise(self, monkeypatch):
        monkeypatch.setattr(features, "is_on", lambda name, org=None: True)
        monkeypatch.setattr(dfn, "build_filter", lambda **kw: None)

        class Works:
            def __init__(self, resampler_quality="QQ"):
                pass

        monkeypatch.setattr(noise_suppression, "_rnnoise_filter_class", lambda: Works)
        assert isinstance(await noise_suppression.build_audio_in_filter(None, 3), Works)

    async def test_suppression_off_stays_off(self, monkeypatch):
        monkeypatch.setattr(features, "is_on", lambda name, org=None: True)
        assert (
            await noise_suppression.build_audio_in_filter({"enabled": False}, 3) is None
        )

    async def test_the_organisation_comes_from_the_run_context(self, monkeypatch):
        from pipecat.utils.run_context import set_current_org_id

        seen = []
        monkeypatch.setattr(
            features, "is_on", lambda name, org=None: seen.append(org) or False
        )

        class Works:
            def __init__(self, resampler_quality="QQ"):
                pass

        monkeypatch.setattr(noise_suppression, "_rnnoise_filter_class", lambda: Works)
        set_current_org_id(42)
        try:
            await noise_suppression.build_audio_in_filter(None)
        finally:
            set_current_org_id(None)
        assert seen == [42]
