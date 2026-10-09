"""Live take-over: a supervisor joins a call and speaks to the caller.

Built on live supervision (``services/live_supervision``): the same panel,
the same people, the same Redis plumbing. Two modes:

- **Barge** -- three on the line. The agent is paused the moment the
  supervisor joins: it keeps transcribing, and nothing it would say reaches
  the caller. The supervisor can let it answer (it speaks until the
  supervisor next talks, which cuts it off again) or hand the call back.
- **Take-over** -- the supervisor replaces the agent. It says nothing for the
  rest of the call unless the call is handed back.

Either way the caller's words keep being transcribed and the agent keeps
reading them, so a hand-back finds it knowing what was said to it. If the
supervisor drops off mid-call, the agent picks the call back up after
``LIVE_TAKEOVER_RECOVERY_SECONDS`` with a short line rather than leave the
caller in silence.

- ``gates`` -- the two processors that silence the agent. Added to a call's
  pipeline only when the feature is on for its organisation.
- ``controller`` -- on the worker running the call: commands, the
  supervisor's audio, the watchdog, the call record.
- ``registry`` -- the API side: join, switch, let the agent answer, hand back.
- ``access`` -- the workspace switch ("Allow supervisors to join calls").
- ``bridge`` -- how the supervisor's voice reaches the caller: from the
  browser through the call's own audio (``PipelineBridge``), or by phone
  through a Plivo Multi-Party Call (``plivo_mpc``, assumptions listed there).
- ``channels`` -- the Redis names and the microphone's wire format.
- ``speech`` -- the supervisor's words: transcribed by a copy of the call's
  own speech-to-text, labelled with who spoke, and read for the agent's name
  (in a barge it answers only when addressed or when "Let the agent answer"
  is clicked).

On a phone call the browser's voice is not mixed into the call
(``constants.ALLOW_SERVER_MIXED_PSTN_BARGE``, off pending telecom counsel):
barge is refused there and a take-over is silent. While a supervisor has
the call, escalation (``services/escalation``) acts on nothing and records
what it held back.

Behind the ``live_takeover`` flag (``LIVE_TAKEOVER_ENABLED``), which needs
``live_supervision`` on as well.
"""
