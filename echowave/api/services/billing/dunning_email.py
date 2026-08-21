"""Telling a customer their number is about to go, before it goes.

``dunning.py`` decides the schedule — retry daily, suspend at seven days,
release-eligible at forty-five — and has always computed a customer-facing
message for each step. Nothing sent it. A number went quiet on day seven and
was queued for release on day forty-five with no email at any point, which is
the worst version of that schedule: the money was usually there, the customer
simply did not know it was needed.

The low-balance warning is a different thing and does not cover this. It fires
on the balance, once a day, and says calls will pause. This fires on a specific
charge failing to collect and says *this number* is suspended — which a customer
can act on, and which arrives even when the balance looks fine because the
failure was a revoked mandate rather than an empty account.

**Once per state, not once per attempt.** The rental job retries daily, so
keying the notice on the attempt would send seven identical emails in the week
before a suspension. It is keyed on the charge and the state it reached, so the
customer hears when something changes and not otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from api.services.billing.dunning import DunningState


@dataclass(frozen=True)
class Notice:
    subject: str
    body: str
    #: What makes two of these the same notice. Deduplicated on it, so a daily
    #: retry that changes nothing sends nothing.
    dedupe_key: str


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def compose(
    *,
    state: DunningState,
    charge_id: int,
    account_name: str,
    phone_number: str | None,
    amount_paise: int,
    top_up_url: str,
) -> Notice | None:
    """What to tell them about this charge, or ``None`` if nothing has changed.

    The consequence is stated in every one of them, and stated specifically. "A
    payment failed" is not actionable; "calls on +91 80 4718 2200 are paused and
    a top-up resumes them immediately" is.
    """
    if not state.should_warn or not state.message:
        return None

    number = phone_number or "your number"

    if state.may_release:
        subject = f"Last warning: {number} is scheduled for release"
    elif state.should_suspend:
        subject = f"Calls on {number} are paused"
    else:
        subject = f"We could not collect the rental for {number}"

    lines = [
        f"We could not collect {_rupees(amount_paise)} for {number}.",
        "",
        state.message,
        "",
    ]

    if state.may_release:
        # The one irreversible step in the whole schedule, so it says so.
        lines += [
            "A released number cannot be recovered — it goes back to the "
            "carrier's pool and may be issued to someone else. If it is "
            "printed on your signage or given to patients, top up today.",
            "",
        ]

    lines += [
        f"Top up: {top_up_url}",
        "",
        "If you meant to stop this number, cancel autopay from the billing "
        "screen and let it lapse — there is nothing else you need to do.",
        "",
        f"— Decibyl, for {account_name}",
    ]

    return Notice(
        subject=subject,
        body="\n".join(lines),
        # The charge and the state it reached. A daily retry that leaves the
        # state where it was sends nothing; crossing into suspension sends one.
        dedupe_key=f"dunning:{charge_id}:{state.status.value}:{state.days_overdue}",
    )
