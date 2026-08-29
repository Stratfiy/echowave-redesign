"""The transport settings live in one place, and stay there.

``audio_out_10ms_chunks`` was set in cloudonix's ``transport.py`` and nowhere
else, so six providers shipped 40ms output chunking after we had decided we
wanted 20ms. Nothing failed -- that is the problem with a per-provider
setting, and the reason these tests read the transports' source text rather
than only asserting on the helper's return value.
"""

import re
from pathlib import Path

from api.services.pipecat import transport_params
from api.services.pipecat.transport_params import (
    AUDIO_OUT_10MS_CHUNKS,
    BOT_VAD_STOP_SECS,
    REALTIME_BOT_VAD_STOP_SECS,
    transport_param_overrides,
)

_API = Path(transport_params.__file__).parents[2]
#: Every module that builds pipecat ``TransportParams``: the seven telephony
#: providers plus the WebRTC factory.
_TRANSPORTS = sorted(
    _API.glob("services/telephony/providers/*/transport.py")
) + [_API / "services/pipecat/transport_setup.py"]


def test_the_transports_are_all_discovered():
    """A provider added without a transport.py would silently pass the rest."""
    assert len(_TRANSPORTS) == 8, [p.name for p in _TRANSPORTS]


def test_every_transport_uses_the_shared_overrides():
    missing = [
        p.parent.name for p in _TRANSPORTS
        if "**transport_param_overrides(" not in p.read_text()
    ]
    assert not missing, (
        f"Transports building params by hand: {missing}. Splat "
        "transport_param_overrides(is_realtime) instead, so a playback or "
        "turn-timing default cannot apply to some providers and not others."
    )


def test_no_transport_overrides_a_shared_setting_locally():
    """A local copy is both drift and a TypeError at connect."""
    shared = set(transport_param_overrides(is_realtime=False))
    for path in _TRANSPORTS:
        passed = set(re.findall(r"^\s+(\w+)=", path.read_text(), re.MULTILINE))
        clash = passed & shared
        assert not clash, (
            f"{path.parent.name}/{path.name} passes {sorted(clash)}, which "
            "transport_param_overrides also returns. Python raises "
            "'got multiple values for keyword argument' and the call dies at "
            "connect. Change the value in transport_params.py instead."
        )


def test_output_chunks_are_halved_from_the_pipecat_default():
    """4 chunks = 40ms buffered before every socket write; 2 = 20ms."""
    assert AUDIO_OUT_10MS_CHUNKS == 2
    for is_realtime in (True, False):
        assert transport_param_overrides(is_realtime)["audio_out_10ms_chunks"] == 2


def test_bot_stop_fallback_is_shorter_than_the_pipecat_default():
    """Pipecat defaults to 3.0s of dead air on paths with no TTSStoppedFrame."""
    assert BOT_VAD_STOP_SECS < 3.0
    assert transport_param_overrides(is_realtime=False)["bot_vad_stop_secs"] == (
        BOT_VAD_STOP_SECS
    )


def test_realtime_stays_tighter_than_the_pipeline_default():
    """Realtime has no TTSStoppedFrame at all, so the fallback fires every turn."""
    assert REALTIME_BOT_VAD_STOP_SECS < BOT_VAD_STOP_SECS
    assert transport_param_overrides(is_realtime=True)["bot_vad_stop_secs"] == (
        REALTIME_BOT_VAD_STOP_SECS
    )


def test_the_fallback_cannot_cut_into_the_bots_own_audio():
    """It must exceed the interval at which output chunks arrive."""
    chunk_interval_secs = AUDIO_OUT_10MS_CHUNKS * 0.01
    assert REALTIME_BOT_VAD_STOP_SECS > chunk_interval_secs
