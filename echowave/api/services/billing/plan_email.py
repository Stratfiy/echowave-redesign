"""Confirming a plan the moment the bank authorises it.

A customer authorises a standing instruction at their own bank and then hears
nothing. On a monthly cycle the next thing that happens can be weeks away, and
when it does happen it is a debit — so the first confirmation that the
authorisation worked at all was money leaving their account.

Two things go wrong in that silence and both cost more than the email. Somebody
who is not sure it worked authorises again, and now there are two mandates on
one account. Somebody who forgot they did it sees an unexplained debit weeks
later and asks their bank about it, which is a chargeback rather than a
question.

**What it has to say is the debit.** Not "thank you for subscribing" — the
amount, the day, and how to stop it. Under GST the amount collected is the
grossed-up figure and the plan's price is net, so quoting the net price here
would set up an argument with the first bank statement. The gross is what the
bank is authorised for and the gross is what this says.
"""

from __future__ import annotations

from api.services.billing.subscription_plans import Plan
from api.services.messaging.announce import Notice

KIND = "plan_authorised"


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def compose(
    *,
    plan: Plan | None,
    mandate_id: int,
    authorised_paise: int,
    app_url: str,
) -> Notice:
    """Confirm the standing instruction, in the terms the bank will act on.

    ``plan`` is optional because a mandate can be authorised for a plan row
    that has since been renamed or withdrawn, and the confirmation is owed
    either way — the bank does not care that our catalogue moved. Without it
    the message drops the contents and keeps the part that matters, which is
    the amount and how to stop it.
    """
    base = (app_url or "").rstrip("/")
    amount = _rupees(authorised_paise)
    label = plan.label if plan else "your plan"

    contents = ""
    if plan is not None:
        lines = [f"  · {_rupees(plan.balance_paise)} of call balance"]
        if plan.included_numbers == 1:
            lines.append("  · 1 phone number")
        elif plan.included_numbers > 1:
            lines.append(f"  · {plan.included_numbers} phone numbers")
        if plan.extra_number_price_paise > 0:
            lines.append(
                f"  · any further number is {_rupees(plan.extra_number_price_paise)} "
                "a month on top"
            )
        contents = "Each month this adds:\n" + "\n".join(lines) + "\n\n"

    body = f"""Your bank has authorised the standing instruction for {label}.

{amount} will be collected each month, including GST, starting with this
cycle. Nothing is charged until the first collection lands, and you will get a
receipt for every one.

{contents}Your balance and this authorisation are both at {base}/billing. You
can cancel the standing instruction there at any time — cancelling stops future
collections and does not touch balance you have already been granted or numbers
you already hold.

— Decibyl
"""

    return Notice(
        subject=f"Autopay is set up — {amount} a month",
        body=body,
        # Per mandate, not per event. Razorpay delivers `authenticated` and
        # `activated` for one authorisation and retries both at least once;
        # keying on the event would confirm the same instruction four times.
        dedupe_key=f"mandate:{mandate_id}",
    )
