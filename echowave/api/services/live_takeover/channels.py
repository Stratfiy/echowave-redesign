"""The Redis names a take-over is reached by, and the microphone's format.

Alongside live supervision's names (``live_supervision.channels``), on the
same client:

- ``live:call:<run>:takeover`` -- who has the call now, as JSON
  (``mode``, ``by``, ``by_user_id``, ``since``, ``bridge``). Absent while the
  agent has it. The API writes it when somebody joins (``SET NX``, so two
  people cannot both take a call) and the worker running the call rewrites
  or deletes it as the call moves on; the worker is the authority.
- ``live:call:<run>:tko`` -- pub/sub: commands *to* the call (join, switch,
  let the agent answer, hand back, and the talking socket's presence ping).
  Only the worker running the call subscribes, and only when the feature
  was on when the call started: a publish that reaches nobody means the
  call cannot be joined.
- ``live:call:<run>:mic`` -- pub/sub: the supervisor's microphone, one
  packet per message in live supervision's audio format with side ``s``.
"""

from __future__ import annotations

import struct

from api.services.live_supervision import channels as live_channels

#: The supervisor's side in an audio packet (the caller is ``c``, the agent
#: ``a``). Packets with it reach listeners too, so a second supervisor hears
#: the first; the one speaking skips their own.
SIDE_SUPERVISOR = b"s"
#: Rates a browser may send at. Anything else is refused at the socket.
MIC_RATES = frozenset({8000, 16000, 24000, 48000})
#: A packet longer than this is not a microphone slice (a second at 48 kHz).
MAX_MIC_BYTES = 6 + 48000 * 2
#: The state key outlives a worker that died mid take-over by this much.
STATE_TTL_SECONDS = 60 * 60

_HEADER = struct.Struct("<cIB")


def redis():
    return live_channels.redis()


def state_key(run_id: int) -> str:
    return f"live:call:{run_id}:takeover"


def command_channel(run_id: int) -> str:
    return f"live:call:{run_id}:tko"


def mic_channel(run_id: int) -> str:
    return f"live:call:{run_id}:mic"


def valid_mic_packet(packet: bytes) -> bool:
    """A microphone slice the call can play: side ``s``, a rate it knows,
    mono, whole 16-bit samples, not absurdly long."""
    if not isinstance(packet, (bytes, bytearray)):
        return False
    if len(packet) <= _HEADER.size or len(packet) > MAX_MIC_BYTES:
        return False
    side, rate, chans = _HEADER.unpack_from(packet)
    return (
        side == SIDE_SUPERVISOR
        and rate in MIC_RATES
        and chans == 1
        and (len(packet) - _HEADER.size) % 2 == 0
    )
