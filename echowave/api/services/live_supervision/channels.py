"""The Redis names a live call is reached by, and the audio wire format.

A call runs on one API worker; the person listening is connected to
whichever worker their browser reached. Redis carries the difference:

- ``live:org:<org>`` -- a sorted set of the organisation's live run ids,
  scored by the last heartbeat. A worker that dies stops beating, and its
  calls fall out of the list within ``STALE_SECONDS``.
- ``live:call:<run>:meta`` -- what the list shows about one call (agent,
  direction, current step), refreshed with the heartbeat and expiring with
  it. No phone number: that is read from the run, with the organisation
  checked, when somebody asks.
- ``live:call:<run>:ev`` -- pub/sub: transcript lines, whispers, the step,
  and the end of the call, as JSON.
- ``live:call:<run>:au`` -- pub/sub: audio packets (``encode_audio``).
  Separate from the events so a listener who did not ask for sound never
  receives it, and so the call can tell when nobody is listening to audio
  (``PUBLISH`` answers with the number of receivers) and stop sending it.
- ``live:call:<run>:ctl`` -- pub/sub: whispers on their way *to* the call.
  Only the worker running the call subscribes; a publish that reaches nobody
  means the call has ended.
- ``live:call:<run>:backlog`` -- the final transcript lines and whispers so
  far, so somebody who opens the panel mid-call sees the conversation from
  the start rather than from the moment they arrived. Deleted when the call
  ends; the call's own record keeps the transcript.
"""

from __future__ import annotations

import struct

import redis.asyncio as aioredis

from api import constants

#: A call that has not beaten for this long is not shown as live.
STALE_SECONDS = 20
#: How often a live call refreshes its entry.
HEARTBEAT_SECONDS = 5
#: Lines kept for a late listener.
BACKLOG_LINES = 300
BACKLOG_TTL_SECONDS = 60 * 60

#: Who an audio packet is from.
SIDE_CALLER = b"c"
SIDE_AGENT = b"a"
_HEADER = struct.Struct("<cIB")  # side, sample rate, channels

_client: aioredis.Redis | None = None


def redis() -> aioredis.Redis:
    """One binary client per process (audio is bytes; events are encoded)."""
    global _client
    if _client is None:
        _client = aioredis.from_url(constants.REDIS_URL, decode_responses=False)
    return _client


def reset() -> None:
    """Forget the client (a test's event loop ended)."""
    global _client
    _client = None


def org_key(organization_id: int) -> str:
    return f"live:org:{organization_id}"


def meta_key(run_id: int) -> str:
    return f"live:call:{run_id}:meta"


def events_channel(run_id: int) -> str:
    return f"live:call:{run_id}:ev"


def audio_channel(run_id: int) -> str:
    return f"live:call:{run_id}:au"


def control_channel(run_id: int) -> str:
    return f"live:call:{run_id}:ctl"


def backlog_key(run_id: int) -> str:
    return f"live:call:{run_id}:backlog"


def encode_audio(side: bytes, sample_rate: int, channels: int, pcm: bytes) -> bytes:
    """``side (1 byte) | sample rate (u32 LE) | channels (u8) | PCM s16le``.

    The same bytes go over Redis and out to the browser as one binary
    WebSocket message, which plays each side at its own rate and mixes them.
    """
    return _HEADER.pack(side, int(sample_rate), int(channels)) + pcm


def decode_audio(packet: bytes) -> tuple[bytes, int, int, bytes]:
    side, rate, channels = _HEADER.unpack_from(packet)
    return side, rate, channels, packet[_HEADER.size :]
