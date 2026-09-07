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
against the clean reference at the best lag stays above 0.93. What it does cost
is a consistent **20 ms**, which is why this is off unless asked for — against a
2,564 ms measured median that is under one percent, but it is not free and it
buys nothing on a call that was already quiet.

**Why there is no level control.** Gnani exposes ``suppressionLevel`` from 20 to
100. RNNoise has no such parameter — it is a trained network, not a gate with a
threshold. A slider here would move nothing, and a setting that lies is worse
than a setting that is missing.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def wants_suppression(config: dict[str, Any] | None) -> bool:
    """Did the account actually ask for this?

    ``enabled`` must be exactly ``True``. The value arrives from a JSON blob
    that several versions of the client have written, and a truthy string is
    not consent.
    """
    if not isinstance(config, dict):
        return False
    return config.get("enabled") is True


async def build_audio_in_filter(config: dict[str, Any] | None):
    """The inbound audio filter for this call, or None to leave audio alone.

    Returns None rather than raising when the optional dependency is missing.
    ``pyrnnoise`` is an extra rather than a base requirement, and a filter that
    cannot load must not be the reason a customer's call fails to connect —
    the call is worth more than the noise reduction. The log line says which
    happened, because a silently absent filter is indistinguishable from one
    that ran and did nothing.
    """
    if not wants_suppression(config):
        return None

    try:
        from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter
    except Exception as error:  # noqa: BLE001 - any import failure means no filter
        logger.warning(
            "Noise suppression is enabled for this agent but the RNNoise filter "
            f"could not be loaded, so the call runs without it: {error}"
        )
        return None

    try:
        # "QQ" is the filter's own default and the lowest-latency resampler
        # setting. The 20 ms measured above is with this; a higher quality
        # buys inaudible fidelity on a phone call and spends the one thing we
        # compete on.
        return RNNoiseFilter(resampler_quality="QQ")
    except Exception as error:  # noqa: BLE001 - see above
        logger.warning(
            f"Could not construct the RNNoise filter, running without it: {error}"
        )
        return None
