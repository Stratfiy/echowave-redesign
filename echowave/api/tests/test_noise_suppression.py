"""Noise off the caller's audio: on by default, with a level that is a real mix."""

from __future__ import annotations

import numpy as np
import pytest

from api.services.pipecat.noise_suppression import (
    DEFAULT_LEVEL,
    MAX_LEVEL,
    MIN_LEVEL,
    RNNOISE_FRAME_SAMPLES,
    build_audio_in_filter,
    build_levelled_filter_class,
    mix,
    resolve_level,
    wants_suppression,
)


class TestOnByDefault:
    @pytest.mark.parametrize(
        "config", [None, {}, {"enabled": True}, {"enabled": "false"}, {"level": 60}]
    )
    def test_anything_but_an_explicit_off_is_on(self, config):
        """Callers are on roads and in shops; a quiet line is the exception.
        A stored string from an old client is not an instruction to leave a
        noisy caller unheard."""
        assert wants_suppression(config) is True

    def test_exactly_false_is_off(self):
        assert wants_suppression({"enabled": False}) is False


class TestTheLevel:
    def test_defaults_to_full(self):
        assert resolve_level(None) == DEFAULT_LEVEL == MAX_LEVEL
        assert resolve_level({}) == MAX_LEVEL
        assert resolve_level({"level": "loud"}) == MAX_LEVEL
        assert resolve_level({"level": True}) == MAX_LEVEL

    def test_is_clamped_to_gnanis_range(self):
        assert resolve_level({"level": 5}) == MIN_LEVEL == 20
        assert resolve_level({"level": 250}) == MAX_LEVEL
        assert resolve_level({"level": 62.6}) == 63

    def test_full_level_is_the_denoised_frame_untouched(self):
        wet = np.array([100, -100, 3000], dtype=np.int16)
        dry = np.array([1000, 1000, 1000], dtype=np.int16)
        assert mix(wet, dry, 100) is wet

    def test_half_level_is_half_of_each(self):
        wet = np.array([0, 0, 0], dtype=np.int16)
        dry = np.array([1000, -1000, 2000], dtype=np.int16)
        assert mix(wet, dry, 50).tolist() == [500, -500, 1000]

    def test_the_blend_never_overflows(self):
        wet = np.array([32000], dtype=np.int16)
        dry = np.array([32000], dtype=np.int16)
        assert mix(wet, dry, 50).tolist() == [32000]


class _FakeRNNoise:
    """Frame-synchronous like the real one: buffers to 480 samples, yields
    each frame in order, and 'denoises' by zeroing it so the blend is
    visible in the numbers."""

    def __init__(self):
        self._buffer = np.zeros(0, dtype=np.int16)

    def denoise_chunk(self, samples):
        self._buffer = np.concatenate([self._buffer, samples])
        while len(self._buffer) >= RNNOISE_FRAME_SAMPLES:
            frame, self._buffer = (
                self._buffer[:RNNOISE_FRAME_SAMPLES],
                self._buffer[RNNOISE_FRAME_SAMPLES:],
            )
            yield 0.9, np.zeros_like(frame)


class TestTheBlendIsSampleAligned:
    async def _filter(self, level):
        pytest.importorskip("pipecat.audio.filters.rnnoise_filter")
        cls = build_levelled_filter_class()
        f = cls(level=level)
        f._sample_rate = 48000  # no resampling in the test
        f._rnnoise = _FakeRNNoise()
        f._rnnoise_ready = True
        f._filtering = True
        return f

    async def test_half_level_returns_half_of_the_original_frame_for_frame(self):
        f = await self._filter(50)
        # 1.5 frames in the first push: one frame comes back, half a frame waits.
        first = np.arange(1, 721, dtype=np.int16)
        out = np.frombuffer(await f.filter(first.tobytes()), dtype=np.int16)
        assert len(out) == RNNOISE_FRAME_SAMPLES
        assert out.tolist() == (first[:RNNOISE_FRAME_SAMPLES] // 2).tolist()
        # The rest of the first push and a new push complete the second frame,
        # and it is blended with *those* samples — nothing slid.
        second = np.arange(1001, 1241, dtype=np.int16)
        out2 = np.frombuffer(await f.filter(second.tobytes()), dtype=np.int16)
        expected = np.concatenate([first[RNNOISE_FRAME_SAMPLES:], second]) // 2
        assert out2.tolist() == expected.tolist()

    async def test_full_level_is_rnnoise_alone(self):
        f = await self._filter(100)
        out = np.frombuffer(
            await f.filter(np.ones(480, dtype=np.int16).tobytes()), dtype=np.int16
        )
        assert out.tolist() == [0] * 480


class TestBuildingTheFilter:
    async def test_returns_none_when_switched_off(self):
        assert await build_audio_in_filter({"enabled": False}) is None

    async def test_a_missing_dependency_does_not_fail_the_call(self, monkeypatch):
        import api.services.pipecat.noise_suppression as module

        def boom():
            raise ImportError("no rnnoise here")

        monkeypatch.setattr(module, "_rnnoise_filter_class", boom)
        assert await build_audio_in_filter({"enabled": True}) is None
        assert await build_audio_in_filter({"enabled": True, "level": 40}) is None

    async def test_a_filter_that_will_not_construct_does_not_fail_the_call(
        self, monkeypatch
    ):
        import api.services.pipecat.noise_suppression as module

        class Broken:
            def __init__(self, **kwargs):
                raise TypeError("Graph(rate=...) got an unexpected keyword")

        monkeypatch.setattr(module, "_rnnoise_filter_class", lambda: Broken)
        assert await build_audio_in_filter(None) is None

    async def test_returns_the_filter_when_it_works(self, monkeypatch):
        import api.services.pipecat.noise_suppression as module

        class Works:
            def __init__(self, resampler_quality="QQ"):
                self.resampler_quality = resampler_quality

        monkeypatch.setattr(module, "_rnnoise_filter_class", lambda: Works)
        built = await build_audio_in_filter(None)
        assert isinstance(built, Works)
        assert built.resampler_quality == "QQ"

    async def test_a_level_below_full_builds_the_levelled_filter(self, monkeypatch):
        import api.services.pipecat.noise_suppression as module

        class Levelled:
            def __init__(self, level, resampler_quality="QQ"):
                self.level = level

        monkeypatch.setattr(module, "build_levelled_filter_class", lambda: Levelled)
        built = await build_audio_in_filter({"level": 60})
        assert isinstance(built, Levelled) and built.level == 60
