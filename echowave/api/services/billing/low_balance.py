"""Warning a customer before the balance runs out, rather than after.

The dunning schedule already handles a rental that goes unpaid: day 7 suspend,
day 45 release-eligible. What it does not do is tell anyone. A customer whose
number is suspended on day 7 having received no warning is the worst version of
that schedule — the money was there, they simply did not know it was needed.

The policy here is pure and lives in one place, like the dunning schedule it
complements. Two ways to qualify, deliberately:

* **days of runway** — the useful one. Balance divided by recent daily burn. It
  is the number a customer can act on, and it scales: an account spending ₹5,000
  a day and one spending ₹50 need warning at wildly different balances.
* **an absolute floor** — the backstop. A new or quiet account has no burn rate
  to divide by, and a runway of "infinite" on ₹12 of credit is not a useful
  answer to anybody.

Severity, not repetition, is what earns a second email. An account is warned
once a day at most, and again the same day only if it has crossed into the
critical band since.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from api.constants import MIN_BALANCE_PAISE, MIN_TOPUP_PAISE

#: Runway thresholds, in days.
WARN_AT_DAYS = 10
CRITICAL_AT_DAYS = 3

#: Warning thresholds, for accounts with no measurable burn rate. ₹200 buys a
#: little under an hour of conversation at the launch rate — enough that the
#: warning is early rather than an obituary.
#:
#: Both sit above ``MIN_BALANCE_PAISE``, the balance at which calling actually
#: stops, and that ordering is the point: the critical warning has to arrive
#: while the account can still place the call that would tell them.
WARN_BELOW_PAISE = 20_000
CRITICAL_BELOW_PAISE = 5_000

LOW = "low"
CRITICAL = "critical"

KIND = "low_balance"


@dataclass(frozen=True)
class Assessment:
    """What we would tell this account, and why."""

    level: str | None
    days_remaining: int | None
    balance_paise: int
    daily_burn_paise: int

    @property
    def should_warn(self) -> bool:
        return self.level is not None


def days_remaining(balance_paise: int, daily_burn_paise: int) -> int | None:
    """How long the balance lasts at the recent burn rate.

    ``None`` rather than a large number when nothing is being spent: "infinite"
    and "we cannot tell" are the same answer here, and both mean the runway
    figure must not be the thing that decides.
    """
    if daily_burn_paise <= 0:
        return None
    return max(0, balance_paise // daily_burn_paise)


def assess(*, balance_paise: int, daily_burn_paise: int) -> Assessment:
    """Whether this account needs telling, and how urgently.

    A negative balance counts as critical rather than as a strange case to skip.
    It happens — a call in flight when the balance hits zero finishes and is
    charged — and it is the single most urgent version of this warning.
    """
    runway = days_remaining(balance_paise, daily_burn_paise)

    level: str | None = None
    if balance_paise <= CRITICAL_BELOW_PAISE:
        level = CRITICAL
    elif runway is not None and runway <= CRITICAL_AT_DAYS:
        level = CRITICAL
    elif balance_paise <= WARN_BELOW_PAISE:
        level = LOW
    elif runway is not None and runway <= WARN_AT_DAYS:
        level = LOW

    return Assessment(
        level=level,
        days_remaining=runway,
        balance_paise=balance_paise,
        daily_burn_paise=daily_burn_paise,
    )


def dedupe_key(level: str, day: date) -> str:
    """What makes two of these the same notice.

    Severity and day. One warning a day per account, and a second the same day
    only if the account has got worse since — which is the one case where a
    repeat is information rather than noise.
    """
    return f"{level}:{day.isoformat()}"


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def compose(
    *,
    assessment: Assessment,
    account_name: str,
    top_up_url: str,
    has_numbers: bool,
) -> tuple[str, str]:
    """The subject and body for one warning.

    Written as plain sentences with the consequence stated. "Your balance is
    low" is not actionable; "calls stop when it reaches zero, and your number is
    suspended seven days after a missed rental" is.
    """
    urgent = assessment.level == CRITICAL
    balance = _rupees(assessment.balance_paise)

    subject = (
        f"Action needed: {account_name} is almost out of credit"
        if urgent
        else f"{account_name} is running low on credit"
    )

    lines = [
        f"Your Decibyl balance is {balance}.",
        "",
    ]

    if assessment.days_remaining is not None:
        lines.append(
            f"At your recent usage that is about {assessment.days_remaining} "
            f"day{'' if assessment.days_remaining == 1 else 's'} of calling."
        )
    else:
        lines.append(
            "You have not placed enough calls recently for us to estimate how "
            "long that lasts."
        )

    lines += [
        "",
        # The floor, not zero. A warning that says calls stop at zero, to an
        # account that then cannot dial at ₹18, reads as a broken system rather
        # than as a thing the customer can fix.
        f"Calling pauses once the balance falls below {_rupees(MIN_BALANCE_PAISE)}, "
        f"and resumes as soon as you top up (from {_rupees(MIN_TOPUP_PAISE)}).",
    ]

    if has_numbers:
        # Stated only when it applies. Telling an account with no numbers that
        # their number will be suspended is the kind of detail that costs trust.
        lines.append(
            "A monthly number rental that cannot be collected suspends the "
            "number after seven days. The number is held, not lost, and a "
            "top-up restores it."
        )

    lines += [
        "",
        f"Top up: {top_up_url}",
        "",
        "— Decibyl",
    ]

    return subject, "\n".join(lines)
