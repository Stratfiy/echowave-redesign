"""Shared helpers for tuning pipecat ``TransportParams`` per run mode.

These live outside ``transport_setup.py`` (which is non-telephony only) so
that both the WebRTC factory there and the telephony provider factories
under ``api.services.telephony.providers/<name>/transport.py`` can call
into the same place.

Every telephony transport splats the result of ``transport_param_overrides``
into its params, so this module is the one place a playback or turn-timing
default gets overridden. A setting applied in a single provider's
``transport.py`` instead is a setting the other six silently do not get --
which is exactly what happened to ``audio_out_10ms_chunks`` (see below), and
what ``tests/test_transport_params.py`` now prevents.
"""

# Pipecat buffers 4 x 10ms = 40ms of audio before writing to the socket, which
# adds up to that much latency to the first word and to the stop after a
# barge-in. Halving it costs one extra socket write per 20ms of speech --
# nothing next to a telephony round trip -- and is already what we concluded
# once: cloudonix's transport carried a local ``audio_out_10ms_chunks=2`` that
# never propagated to the other six providers.
AUDIO_OUT_10MS_CHUNKS = 2

# How long after the output queue drains before the bot counts as having
# stopped speaking. It is a *fallback*: when TTS emits ``TTSStoppedFrame`` the
# real signal arrives first and this timer never fires, so it only bites on the
# paths that have no such frame -- and there it gates the assistant aggregator
# closing its turn, i.e. dead air the caller hears.
#
# Pipecat's default is 3.0s. Audio chunks arrive every 20ms (above), so any
# value comfortably above one chunk interval is safe against cutting into the
# bot's own speech; 3.0 is two orders of magnitude more headroom than that
# needs.
BOT_VAD_STOP_SECS = 0.8

# Realtime (speech-to-speech) LLMs don't emit ``TTSStoppedFrame`` at all, so
# for them this fallback fires on *every* turn rather than only on the odd
# path. Kept tighter than the pipeline value for that reason.
REALTIME_BOT_VAD_STOP_SECS = 0.5


def transport_param_overrides(is_realtime: bool) -> dict:
    """Return kwargs to splat into ``TransportParams`` for the given run mode.

    A transport that needs to differ should be given a reason here rather than
    a local keyword argument: passing one that this dict also carries raises
    ``TypeError: got multiple values for keyword argument`` and takes the call
    down at connect.
    """
    return {
        "audio_out_10ms_chunks": AUDIO_OUT_10MS_CHUNKS,
        "bot_vad_stop_secs": (
            REALTIME_BOT_VAD_STOP_SECS if is_realtime else BOT_VAD_STOP_SECS
        ),
    }
