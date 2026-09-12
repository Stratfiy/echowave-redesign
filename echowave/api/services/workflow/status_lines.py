"""The one line under an agent's name that says what it has been doing.

The whole point of listing agents like people rather than like rows in a
configuration table. A row called "Front Desk" with a pencil icon beside it
tells an owner nothing; "9 calls, 6 answered, 4 bookings filed" tells them the
thing they hired it for is happening.

Built from ``app_interactions`` and the run table rather than from a status
field somebody has to remember to update, so the line cannot drift from what
actually happened.

Two rules it must not break.

*Never invent the verb.* The count of outcomes is the count of calls that
reached an outside system and were not refused; it is not "bookings" unless the
agent's own tool is called something like a booking. Where the tool name gives
us a noun we use it, and where it does not we say "handled", which is vague and
true, rather than "booked", which is specific and sometimes false.

*Silence is a state, not a blank.* An agent that did nothing today gets a
sentence saying so. A blank line reads as a loading failure, and an owner who
cannot tell "no calls" from "screen broken" stops trusting the screen.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

#: Errors in the window before the line changes tone. Matches the readiness
#: checklist deliberately: one screen must not call a connector healthy while
#: the other calls it broken.
FAILURES_BEFORE_CONCERN = 3

TONE_WORKING = "working"
TONE_IDLE = "idle"
TONE_ATTENTION = "attention"
TONE_PAUSED = "paused"

#: Tool-name stems we are willing to turn into a past-tense noun in the line.
#: An allow-list on purpose. Guessing a verb from an arbitrary operator-chosen
#: tool name is how a screen ends up claiming an agent booked appointments when
#: it looked up a price.
_OUTCOME_NOUNS: tuple[tuple[str, str, str], ...] = (
    ("book", "booking", "bookings"),
    ("appointment", "booking", "bookings"),
    ("schedul", "booking", "bookings"),
    ("reschedul", "reschedule", "reschedules"),
    ("cancel", "cancellation", "cancellations"),
    ("order", "order", "orders"),
    ("confirm", "confirmation", "confirmations"),
    ("ticket", "ticket", "tickets"),
    ("lead", "lead", "leads"),
    ("refund", "refund", "refunds"),
    ("payment", "payment", "payments"),
    ("invoice", "invoice", "invoices"),
    ("message", "message", "messages"),
    ("whatsapp", "message", "messages"),
    ("sms", "message", "messages"),
    ("email", "email", "emails"),
    ("mail", "email", "emails"),
)


def humanise_tool_name(name: Optional[str]) -> str:
    """``book_appointment`` -> ``Book appointment``.

    Operators name their own tools and they name them in code style. Printing
    the raw slug on a screen an owner reads is the difference between software
    that was designed and software that was exported.
    """
    if not name:
        return "Action"
    words = re.sub(r"[_\-.]+", " ", str(name)).strip()
    words = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", words)
    words = re.sub(r"\s+", " ", words).strip()
    if not words:
        return "Action"
    return words[0].upper() + words[1:]


def _outcome_noun(last_action: Optional[dict[str, Any]], count: int) -> str:
    """The noun for the outcome count, or a safe generic one."""
    name = (last_action or {}).get("name") or ""
    lowered = str(name).lower()
    for stem, singular, plural in _OUTCOME_NOUNS:
        if stem in lowered:
            return singular if count == 1 else plural
    return "handled" if count == 1 else "handled"


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def status_line(
    activity: Optional[dict[str, Any]],
    *,
    is_live: bool = True,
    hours: int = 24,
) -> dict[str, Any]:
    """One sentence, a tone, and the timestamp it refers to.

    The timestamp comes back raw rather than as "2m ago" because the reader's
    clock is in the browser and ours is in a data centre; rendering "2m ago"
    here means rendering it in the wrong timezone for half the accounts.
    """
    activity = activity or {}
    calls = int(activity.get("calls") or 0)
    answered = int(activity.get("answered") or 0)
    dialled = int(activity.get("dialled") or 0)
    outcomes = int(activity.get("outcomes") or 0)
    failures = int(activity.get("failures") or 0)
    last_action = activity.get("last_action") or None
    last_at = activity.get("last_run_at")
    if last_action and last_action.get("at"):
        action_at = last_action["at"]
        if last_at is None or _after(action_at, last_at):
            last_at = action_at

    window = "today" if hours >= 24 else f"in the last {hours}h"

    if not is_live:
        # Paused wins over everything. An agent that is not taking calls and
        # shows "9 calls today" invites the owner to believe it is still on.
        return _line("Paused — not taking calls", TONE_PAUSED, last_at, last_action)

    if calls == 0 and not last_action:
        return _line(f"Nothing {window}", TONE_IDLE, last_at, last_action)

    parts: list[str] = []
    if calls:
        parts.append(_plural(calls, "call", "calls"))
        # Only claim an answer rate where a carrier could report one. A browser
        # test never gets an ``answered_at`` and counting it would print
        # "3 calls, 0 answered" over a morning that went fine.
        if dialled:
            parts.append(f"{answered} answered")
    if outcomes:
        parts.append(f"{outcomes} {_outcome_noun(last_action, outcomes)}")

    if failures >= FAILURES_BEFORE_CONCERN:
        parts.append(_plural(failures, "failure", "failures"))
        tone = TONE_ATTENTION
    elif last_action and last_action.get("status") == "error":
        parts.append("last action failed")
        tone = TONE_ATTENTION
    elif calls or outcomes:
        tone = TONE_WORKING
    else:
        tone = TONE_IDLE

    if not parts:
        return _line(f"Nothing {window}", TONE_IDLE, last_at, last_action)

    return _line(", ".join(parts), tone, last_at, last_action)


def _after(a: Any, b: Any) -> bool:
    """Whether ``a`` is later than ``b``, tolerating one being naive.

    Rows written before the column carried a timezone still exist, and a
    ``TypeError`` deep in a comparison would take the whole home screen down to
    decide which of two timestamps to print.
    """
    if not isinstance(a, datetime) or not isinstance(b, datetime):
        return a is not None
    if (a.tzinfo is None) != (b.tzinfo is None):
        return True
    return a > b


def _line(
    text: str,
    tone: str,
    at: Any,
    last_action: Optional[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "text": text,
        "tone": tone,
        "at": at if isinstance(at, datetime) else None,
        "last_action": (
            {
                "label": humanise_tool_name((last_action or {}).get("name")),
                "app": (last_action or {}).get("app"),
                "status": (last_action or {}).get("status"),
                "at": (last_action or {}).get("at"),
            }
            if last_action
            else None
        ),
    }
