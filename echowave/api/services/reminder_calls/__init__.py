"""Reminder calls: "call me tomorrow at 8:30 in Tamil to remind me to send
the proposal" (Stage 2 of docs/plans/reminder-calls.md).

Behind ``reminder_calls`` (off by default; off, nothing here is offered,
scheduled or rung). The pieces, in the order of the contract's flow:

* ``draft``   -- the typed, deterministic draft: the person's words, the full
  local date and time in their zone, language, recurrence. Outside calling
  hours the card says so and offers 09:00; nothing is moved silently.
* ``number``  -- the number card, confirmed once, with "I am 18 or over"
  (decision D4). Nobody is rung on a number without it.
* ``cards``   -- the reminder card. Confirming it saves a schedule whose
  version is the card's version (``schedule.save``).
* ``calls``   -- the minute tick (occurrence by unique key, dispatch row
  written before the dial), the post-call hook, the reconcile sweep, the
  one bounded retry and the notification fallback.
* ``gate``    -- every pre-dial check, in order, immediately before the dial.
* ``agent``   -- what the call says: who is calling, an identity question,
  and only then the person's own words (decisions D6, D7).
* ``policy``  -- the founder's open decisions D1-D7, each one constant.

Two states, never one: a reminder **task** (``occurrences.task_state``: did
the person deal with it) and each ring's **delivery**
(``dispatches.state``: did the call happen). Delivery never moves the task.
"""

from __future__ import annotations

from api.services import features

FLAG = "reminder_calls"

# reminder_call_schedules.state
ACTIVE = "active"
CANCELLED = "cancelled"

# reminder_call_occurrences.task_state
OPEN = "open"
SNOOZED = "snoozed"
USER_REPORTED_DONE = "user_reported_done"
TASK_CANCELLED = "cancelled"
TASK_STATES = (OPEN, SNOOZED, USER_REPORTED_DONE, TASK_CANCELLED)

# reminder_call_dispatches.state
QUEUED = "queued"
DISPATCHING = "dispatching"
ACCEPTED = "accepted"
ANSWERED = "answered"
NO_ANSWER = "no_answer"
FAILED = "failed"
#: It may have rung and nothing proves what happened. Never re-dialled;
#: reconciled against the run; moved by later evidence.
UNKNOWN = "unknown"
#: The gate said no, or it was never dialled. ``reason`` says why.
SKIPPED = "skipped"
DELIVERY_STATES = (
    QUEUED,
    DISPATCHING,
    ACCEPTED,
    ANSWERED,
    NO_ANSWER,
    FAILED,
    UNKNOWN,
    SKIPPED,
)


class ReminderCallError(ValueError):
    """Said to the person (or the model) as it is; never retried."""


class NotHere(ReminderCallError):
    """Not in this workspace, or not this person's: answered the way an id
    that names nothing is."""


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)
