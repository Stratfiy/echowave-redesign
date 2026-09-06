"""What decides whether a call gets a noise filter, and what happens when it can't.

The behaviour worth pinning here is the refusal to fail: ``pyrnnoise`` is an
optional extra, and if it cannot be imported or constructed the call must still
connect. A customer would rather have a noisy conversation than no conversation,
and the dependency chain behind that filter has already been shown to break on
an unpinned transitive upgrade.
"""

import sys
from types import ModuleType

import pytest

from api.services.pipecat.noise_suppression import (
    build_audio_in_filter,
    wants_suppression,
)


class TestConsent:
    @pytest.mark.parametrize(
        "config",
        [
            None,
            {},
            {"enabled": False},
            {"enabled": "true"},
            {"enabled": 1},
            {"enabled": None},
            "not-a-dict",
            [],
        ],
    )
    def test_anything_but_exactly_true_is_off(self, config):
        """A truthy string is not consent.

        These values arrive from ``workflow_configurations``, a free-form JSON
        blob written by several versions of the client. Turning a filter on
        because somebody stored the string "false" would add 20 ms to every
        call on that agent and nothing would say why.
        """
        assert wants_suppression(config) is False

    def test_exactly_true_is_on(self):
        assert wants_suppression({"enabled": True}) is True


class TestBuildingTheFilter:
    async def test_returns_none_when_not_asked_for(self):
        assert await build_audio_in_filter(None) is None
        assert await build_audio_in_filter({"enabled": False}) is None

    async def test_a_missing_dependency_does_not_fail_the_call(self, monkeypatch):
        """The whole point of the try/except around the import.

        ``pyrnnoise`` is an extra, not a base requirement. If it is absent the
        filter is skipped and the call proceeds — raising here would mean an
        agent with a checkbox ticked cannot take calls at all.
        """
        monkeypatch.setitem(sys.modules, "pipecat.audio.filters.rnnoise_filter", None)
        assert await build_audio_in_filter({"enabled": True}) is None

    async def test_a_filter_that_will_not_construct_does_not_fail_the_call(
        self, monkeypatch
    ):
        """Import succeeding is not the same as the native library working.

        pyrnnoise reaches through audiolab into PyAV's filter graphs, and a
        version mismatch there raises on construction rather than on import.
        """

        module = ModuleType("pipecat.audio.filters.rnnoise_filter")

        class ExplodingFilter:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("Graph.__init__() got an unexpected keyword")

        module.RNNoiseFilter = ExplodingFilter
        monkeypatch.setitem(sys.modules, "pipecat.audio.filters.rnnoise_filter", module)
        assert await build_audio_in_filter({"enabled": True}) is None

    async def test_returns_the_filter_when_it_works(self, monkeypatch):
        module = ModuleType("pipecat.audio.filters.rnnoise_filter")
        built = {}

        class FakeFilter:
            def __init__(self, resampler_quality="QQ"):
                built["resampler_quality"] = resampler_quality

        module.RNNoiseFilter = FakeFilter
        monkeypatch.setitem(sys.modules, "pipecat.audio.filters.rnnoise_filter", module)

        result = await build_audio_in_filter({"enabled": True})
        assert isinstance(result, FakeFilter)
        # "QQ" is the lowest-latency resampler setting, and latency is the one
        # thing this feature spends. A change here should be deliberate.
        assert built["resampler_quality"] == "QQ"
