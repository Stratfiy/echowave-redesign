"""What happens when a recurring charge cannot be collected.

This module is a policy, not a mechanism. It holds no I/O and touches no
database: given how long a charge has been failing, it says what state the
resource should be in and whether anyone is allowed to release it. Keeping it
that way means the schedule can be read — and tested — in one place instead of
being reconstructed from conditionals scattered through a cron job.

The schedule:

===========  ==========================================================
day 0        the charge fails; mark past due and retry daily
day 7        suspend calling; the number is still held
day 15       warn
day 25       warn
day 45       eligible for release, by explicit action only
===========  ==========================================================

Two rules matter more than the numbers, and both exist because releasing a
number is irreversible in a way that suspending is not. A released number goes
back to the carrier's pool and can be issued to someone else the same day; it
may also be printed on the customer's van, their storefront, and every invoice
they have ever sent.

**Nothing is ever released automatically.** Day 45 grants permission, not
action. :func:`may_release` is a precondition someone else has to satisfy, and
the release path additionally requires an actor and a reason.

**Nothing is released on a first failure.** A failed charge is far more often a
card that expired or a balance that ran out over a weekend than a customer who
has left. Forty-five days is chosen to be longer than any plausible billing
mishap, not to be an efficient use of carrier rent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from api.enums import RecurringChargeStatus

#: Calls stop. The resource is still held and fully recoverable.
SUSPEND_AFTER_DAYS = 7

#: Reminders *after* the suspension. They change nothing about the resource;
#: they exist so the customer is not surprised at day 45.
#:
#: The suspension itself is announced separately — see ``should_warn`` below.
#: It used not to be, so a number went silent on day 7 and the customer first
#: heard about it on day 15: eight days of a dead line at the exact moment they
#: could have fixed it with a top-up. That is the failure this module's own
#: docstring names, and it was in the schedule rather than in the sending.
WARN_AFTER_DAYS = (15, 25)

#: The earliest a release may be *considered*. Never a trigger.
RELEASE_ELIGIBLE_AFTER_DAYS = 45

#: How often to retry a failed charge.
RETRY_INTERVAL_DAYS = 1


@dataclass(frozen=True)
class DunningState:
    """Where a failing charge stands, and what should be done about it."""

    status: RecurringChargeStatus
    days_overdue: int
    should_suspend: bool
    should_warn: bool
    may_release: bool
    #: A sentence for the customer. Empty when nothing needs saying.
    message: str = ""


def days_overdue(first_failed_at: datetime | None, *, now: datetime) -> int:
    """Whole days since the first failure in the current run of failures.

    Anchored on the *first* failure rather than the most recent one, so a
    customer whose charge fails every day does not reset the clock daily and
    sit past due for ever.
    """
    if first_failed_at is None:
        return 0
    delta = now - first_failed_at
    return max(0, delta.days)


def evaluate(
    *,
    first_failed_at: datetime | None,
    current_status: RecurringChargeStatus | str,
    now: datetime,
) -> DunningState:
    """Decide what a charge's state should be, given how long it has failed."""
    try:
        current = RecurringChargeStatus(current_status)
    except ValueError:
        current = RecurringChargeStatus.ACTIVE

    if first_failed_at is None:
        # Nothing outstanding. A charge that just succeeded lands here, which is
        # what lifts a suspension.
        return DunningState(
            status=RecurringChargeStatus.ACTIVE,
            days_overdue=0,
            should_suspend=False,
            should_warn=False,
            may_release=False,
        )

    overdue = days_overdue(first_failed_at, now=now)

    if overdue >= RELEASE_ELIGIBLE_AFTER_DAYS:
        return DunningState(
            status=RecurringChargeStatus.PENDING_RELEASE,
            days_overdue=overdue,
            should_suspend=True,
            should_warn=True,
            may_release=True,
            message=(
                f"This number has been unpaid for {overdue} days and is now "
                "scheduled for release. Top up to keep it — once released it "
                "cannot be recovered."
            ),
        )

    if overdue >= SUSPEND_AFTER_DAYS:
        return DunningState(
            status=RecurringChargeStatus.SUSPENDED,
            days_overdue=overdue,
            should_suspend=True,
            # The day calls stop, and every reminder after it. Warning only on
            # the reminder days meant the one moment the customer could act on
            # — their number going quiet — was the one nobody was told about.
            should_warn=(overdue == SUSPEND_AFTER_DAYS or overdue in WARN_AFTER_DAYS),
            may_release=False,
            message=(
                "Calls on this number are paused because the monthly rental "
                f"is {overdue} days overdue. The number is still yours — top "
                "up and it resumes immediately."
            ),
        )

    return DunningState(
        status=RecurringChargeStatus.PAST_DUE,
        days_overdue=overdue,
        should_suspend=False,
        should_warn=overdue in WARN_AFTER_DAYS,
        may_release=False,
        message=(
            "We could not collect this month's number rental. We will retry "
            f"daily; calls pause in {SUSPEND_AFTER_DAYS - overdue} day(s) if "
            "it stays unpaid."
        ),
    )


def may_release(*, first_failed_at: datetime | None, now: datetime) -> bool:
    """Whether the grace period has elapsed.

    A precondition, asserted by the release path. It answers "is this
    permitted", never "should this happen" — no caller in this codebase treats
    a True here as an instruction.
    """
    if first_failed_at is None:
        return False
    return days_overdue(first_failed_at, now=now) >= RELEASE_ELIGIBLE_AFTER_DAYS


def next_retry_at(now: datetime) -> datetime:
    return now + timedelta(days=RETRY_INTERVAL_DAYS)
