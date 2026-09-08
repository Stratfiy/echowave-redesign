"""Deciding when to take money from a customer without asking them first.

That sentence is the whole design brief. Everything else in billing moves money
because a person just clicked something; this moves money because a number went
below another number, on a schedule, with nobody watching. So the interesting
part is not the charge — it is the set of refusals around it.

**Why a runway trigger rather than a low balance.** India's e-mandate rules
require a pre-debit notification to the customer a clear 24 hours before each
debit, so an auto top-up can never be instant: by the time a balance is nearly
gone it is far too late to start a day-long clock. Triggering on *days of
runway* — the same measure :mod:`low_balance` already warns on — means the
notice period fits inside the cushion by construction rather than by luck. An
absolute floor backs it up for accounts too new or too quiet to have a burn
rate, exactly as it does there.

**The five refusals, and what each one costs when it is missing.**

* **In flight.** An attempt already scheduled or charging blocks another. The
  balance stays low for the whole notice period, so without this every sweep in
  that window starts a fresh debit and the customer is charged once per sweep.
* **Cooldown.** After a successful top-up, nothing for a while — even if the
  balance is low again. A large campaign can burn a top-up in minutes, and a
  balance that is low again for a real reason is indistinguishable from one that
  is low because the credit never landed.
* **A monthly ceiling, in money and in count.** The runaway guard. Any bug that
  drains a balance — a mispriced call, a double debit, a loop — becomes a bug
  that drains a card, and it does so at machine speed. The ceiling is what makes
  the worst case a bounded, refundable amount instead of an unbounded one.
* **Consecutive failures.** Repeated declines are not free: banks charge for
  some, and a merchant that keeps retrying dead cards gets flagged by its
  processor. After a few, this stops and asks for a human.
* **A live authorisation, with room.** The debit has to fit under the mandate's
  registered maximum, because a charge above it is refused by the bank anyway —
  better to refuse it here, where we can say why.

Every refusal returns a :class:`Decision` naming itself. Nothing here logs and
returns ``None``: a top-up that did not happen is a question somebody will ask.

This module decides. It does not talk to a provider — see :func:`execute` in
``auto_topup_charge`` for that, and note that taking a variable amount on demand
requires recurring payments to be enabled on the merchant account, which is an
approval rather than a setting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from api.constants import MIN_TOPUP_PAISE
from api.services.billing.low_balance import days_remaining

#: India's e-mandate framework requires the customer be notified a clear 24
#: hours before each debit. An hour of headroom absorbs a late sweep without
#: shortening the notice below what is required.
NOTICE_HOURS = 25

#: How long after a successful top-up before another may be considered. Long
#: enough that a burst of spending cannot chain top-ups, short enough that a
#: genuinely heavy account is not left dry for a working day.
COOLDOWN_HOURS = 20

#: The runaway ceiling, per calendar month. Both halves matter: the count stops
#: a fast loop, the money stops a slow one.
DEFAULT_MAX_PER_MONTH = 4

#: Stop and ask a human. Three declines in a row is a dead instrument, not bad
#: luck, and continuing to present it costs money and merchant standing.
MAX_CONSECUTIVE_FAILURES = 3

#: Trigger points, mirroring `low_balance` so a customer is never warned about a
#: balance that was about to be topped up anyway.
DEFAULT_TRIGGER_DAYS = 5
DEFAULT_TRIGGER_PAISE = 15_000

# What `decide` can answer. Strings rather than an enum so a new outcome does
# not need a migration to appear in the attempt ledger.
SCHEDULE = "schedule"
CHARGE = "charge"
SKIP = "skip"


@dataclass(frozen=True)
class Settings:
    """One account's configuration. Defaults are the safe ones: off."""

    enabled: bool = False
    trigger_days: int = DEFAULT_TRIGGER_DAYS
    trigger_paise: int = DEFAULT_TRIGGER_PAISE
    amount_paise: int = 0
    monthly_cap_paise: int = 0
    max_per_month: int = DEFAULT_MAX_PER_MONTH


@dataclass(frozen=True)
class Account:
    """What the sweep knows about an account at this moment."""

    balance_paise: int
    daily_burn_paise: int
    #: Provider authorisation. `None` means nothing to charge against.
    mandate_max_paise: int | None = None
    mandate_is_live: bool = False


@dataclass(frozen=True)
class History:
    """What has already been attempted, so nothing is attempted twice."""

    in_flight: bool = False
    last_success_at: datetime | None = None
    charged_this_month_paise: int = 0
    count_this_month: int = 0
    consecutive_failures: int = 0
    #: Set when a notice has been sent and the debit is waiting out its clock.
    notified_at: datetime | None = None


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    amount_paise: int = 0
    #: When the debit may run. Only meaningful for ``SCHEDULE``.
    charge_after: datetime | None = None

    @property
    def will_take_money(self) -> bool:
        return self.action == CHARGE


def _skip(reason: str) -> Decision:
    return Decision(action=SKIP, reason=reason)


def is_below_trigger(settings: Settings, account: Account) -> bool:
    """Has this account reached the point where it should be topped up?

    Runway first, because it is the measure that scales — an account spending
    ₹5,000 a day and one spending ₹50 need topping up at wildly different
    balances. The absolute floor catches the account with no measurable burn,
    where a runway of "infinite" on ₹40 of credit is not a useful answer.
    """
    runway = days_remaining(account.balance_paise, account.daily_burn_paise)
    if runway is not None and runway <= settings.trigger_days:
        return True
    return account.balance_paise <= settings.trigger_paise


def decide(
    *,
    settings: Settings,
    account: Account,
    history: History,
    now: datetime | None = None,
) -> Decision:
    """Whether to notify, to charge, or to do nothing — and why.

    Ordered so that the cheapest and most absolute refusals come first, and so
    that a reader can see the safety rules before the happy path.
    """
    now = now or datetime.now(UTC)

    if not settings.enabled:
        return _skip("auto top-up is off for this account")

    # A scheduled debit whose notice period has elapsed is the one case that
    # takes money, and it is checked before the trigger is re-evaluated: the
    # balance may well have recovered, but the customer has been told a debit is
    # coming and the authorisation to take it was given for this amount.
    if history.notified_at is not None:
        ready_at = history.notified_at + timedelta(hours=NOTICE_HOURS)
        if now < ready_at:
            return Decision(
                action=SKIP,
                reason="waiting out the pre-debit notice period",
                amount_paise=settings.amount_paise,
                charge_after=ready_at,
            )
        return Decision(
            action=CHARGE,
            reason="notice period elapsed",
            amount_paise=settings.amount_paise,
        )

    if history.in_flight:
        return _skip("an attempt is already in flight")

    if history.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return _skip(
            f"{history.consecutive_failures} consecutive failures; "
            "stopped until someone looks at it"
        )

    if not account.mandate_is_live:
        return _skip("no live payment authorisation on file")

    if settings.amount_paise < MIN_TOPUP_PAISE:
        return _skip(
            f"configured amount is below the {MIN_TOPUP_PAISE / 100:,.0f} minimum"
        )

    if (
        account.mandate_max_paise is not None
        and settings.amount_paise > account.mandate_max_paise
    ):
        # The bank would refuse this anyway. Refusing it here means the customer
        # is told to raise their authorisation rather than shown a decline.
        return _skip(
            "configured amount is above the authorised maximum "
            f"({account.mandate_max_paise / 100:,.0f})"
        )

    if history.count_this_month >= settings.max_per_month:
        return _skip(f"already topped up {history.count_this_month} times this month")

    if settings.monthly_cap_paise > 0:
        if (
            history.charged_this_month_paise + settings.amount_paise
            > settings.monthly_cap_paise
        ):
            return _skip("this month's cap would be exceeded")

    if history.last_success_at is not None:
        if now - history.last_success_at < timedelta(hours=COOLDOWN_HOURS):
            return _skip("topped up recently; in cooldown")

    if not is_below_trigger(settings, account):
        return _skip("balance is above the trigger")

    return Decision(
        action=SCHEDULE,
        reason="balance reached the trigger",
        amount_paise=settings.amount_paise,
        charge_after=now + timedelta(hours=NOTICE_HOURS),
    )
