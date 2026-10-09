""" "Call me when it's done": Decibyl rings a person when a task they handed
over finishes, the way a human assistant would.

Two halves, both behind ``call_when_done`` (off by default; off, nothing
here is offered, recorded or rung):

* **The ask** (``optin``). Per task, from the chat -- the person says "call
  me when it's done", taps the "Call me when done" chip, or Decibyl's
  ``call_me_when_done`` tool -- or as a standing preference ("Call me when
  long tasks finish", on ``member_preferences``). The number is confirmed
  once, on a card in the thread (``number``), the consent pattern care's
  reminder calls use.
* **The call** (``calls``). Completion is heard in ONE place: every finished
  job already writes its line through ``agent_timeline.record``, and that
  function hands each row to ``completion.on_recorded``. A finish settles
  the callbacks watching it and queues one call per person; tasks that
  finish together share it. The call is the platform's own outbound path
  (``telephony.outbound.dial_workflow``), metered like every outbound call,
  announced as Decibyl in the person's language (``agent``).

The rules that hold whatever else changes:

* **Calling hours and the do-not-call list are hard rules.**
  ``dnd.assert_may_call`` is asked immediately before every dial, with the
  calling window enforced. A finish outside hours is queued for the next
  opening (09:00 local) and the thread says so.
* **Never silent.** Where no call can be placed -- no line, no confirmed
  number, a listed number, a refused dial -- the person is told in the
  thread and on their own notification channels, and the thread says why.
* **One row per call, one call per row.** ``done_calls`` moves queued ->
  calling by a compare-and-swap, so two ticks or two workers place it once.
* **No report is not "no answer".** A call whose outcome nobody has proved
  is ``unknown``, never re-dialled, and corrected when evidence arrives
  (``calls.reconcile``).
* **At most five a day per person, reserved before the dial**
  (``allowance``), across every workspace.
"""

from __future__ import annotations

from api.services import features

FLAG = "call_when_done"

# done_callbacks.state
PENDING = "pending"
FINISHED = "finished"
CANCELLED = "cancelled"

# done_calls.state
QUEUED = "queued"
CALLING = "calling"
ANSWERED = "answered"
NOT_ANSWERED = "not_answered"
FAILED = "failed"
#: No call could be placed; the person was told in the app instead.
NOTIFIED = "notified"
#: A dial may have reached the carrier, and nothing has proved what came of
#: it (a timeout after the carrier accepted, no report by the answer
#: window). Never re-dialled; reconciled against the run, and moved by
#: later evidence. Not "not answered": that needs evidence.
UNKNOWN = "unknown"
CALL_STATES = (QUEUED, CALLING, ANSWERED, NOT_ANSWERED, FAILED, NOTIFIED, UNKNOWN)


class CallWhenDoneError(ValueError):
    """Said to the person (or the model) as it is; never retried."""


class NotHere(CallWhenDoneError):
    """A task, agent or callback that is not in this workspace (or not this
    person's): answered the way an id that names nothing is."""


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)
