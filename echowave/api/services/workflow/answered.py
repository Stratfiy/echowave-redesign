"""Whether somebody was actually on the line, asked in one place.

``answered_at`` is stamped by the carrier's status webhook, and only an
outbound call has one. Judging "answered" on it alone writes off every
inbound call and every browser test as unanswered -- a clinic's whole day
of people ringing the front desk reads as nobody picking up.

That has now been got wrong three times and fixed twice:

* the backfill in ``e6c1d4b7a9f2`` wrote "Call not answered" onto every
  inbound call, corrected by ``a9c3e5d7f1b2``;
* the call timeline judged the same way, corrected in ``agent_timeline``;
* ``agent_activity`` -- the headline, the team block, the home screen and
  Decibyl's context -- was never corrected at all, so the founder was told
  "15 calls today, 0 answered" about a day of answered inbound calls, and
  the same call read as answered on its own timeline.

Two copies of a rule is how the third copy goes wrong, so the rule lives
here once, in both the forms that need it: an expression for the queries
that count in SQL, and a predicate for the code that has the row in hand.

**The rule.** A call was answered if the carrier said so, or if it has
billable seconds -- somebody was on the line long enough to be charged for
it. Either is sufficient; neither is required of the other.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func

from api.db.models import WorkflowRunModel


def answered_sql():
    """The rule as a SQL expression, for counting over many rows."""
    return WorkflowRunModel.answered_at.isnot(None) | (
        func.coalesce(WorkflowRunModel.billable_seconds, 0) > 0
    )


def was_answered(run: Any) -> bool:
    """The rule for a run already loaded.

    Falls back to the carrier's own window when ``billable_seconds`` has not
    been written yet -- a run that has ended but not been costed still knows
    how long it was connected.
    """
    if getattr(run, "answered_at", None) is not None:
        return True
    seconds = getattr(run, "billable_seconds", None)
    if seconds is None:
        answered_at = getattr(run, "answered_at", None)
        ended_at = getattr(run, "ended_at", None)
        if answered_at and ended_at:
            seconds = int((ended_at - answered_at).total_seconds())
    return (seconds or 0) > 0
