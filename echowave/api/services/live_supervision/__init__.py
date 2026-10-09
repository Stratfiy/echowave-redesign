"""Live supervision: listen in on a customer call and whisper to its agent.

Everything happens inside Decibyl; nothing about the call's telephony
changes, and the caller's audio path gains nothing to wait for.

- ``session`` -- on the worker running the call: the tap, the publishers,
  the heartbeat and the whisper receiver. ``attach`` is the pipeline hook.
- ``tap`` -- the observer that copies audio and words without blocking.
- ``lines`` -- words into transcript lines, masked, in order.
- ``whisper`` -- an instruction as a context message the voice never sees.
- ``registry`` -- the API side: the live list, one call, sending a whisper.
- ``access`` -- the workspace switch and who may listen.
- ``consent`` -- whether the opening tells callers about monitoring.
- ``channels`` -- the Redis names and the audio wire format.

Behind the ``live_supervision`` flag (``LIVE_SUPERVISION_ENABLED``).
"""
