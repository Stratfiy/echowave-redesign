"""The four chips under the composer on the home screen.

Every chip is built from something that is actually true of this account
right now. That is the whole design rule, and it is worth stating why: a chip
that says "Ask me anything" is decoration, and decoration next to real
prompts teaches people that none of them are worth reading. A chip earns its
place by naming a fact the owner did not already have on screen.

Two rules the chips must not break.

*A chip never fires the action.* Clicking one puts a sentence in the composer
or opens a screen; it never starts calling customers. The day a chip silently
dials a hundred people is the day an account leaves, and no amount of
convenience is worth that.

*A chip is never generic except on an empty account.* With no agents there is
nothing true to say, so the openers describe what the product does. The moment
there is one agent, everything on this list comes from data.
"""

from __future__ import annotations

from typing import Any, Optional

#: What clicking does. ``prompt`` fills the composer and waits for the person
#: to send it; ``link`` navigates. Nothing here executes anything.
ACTION_PROMPT = "prompt"
ACTION_LINK = "link"

#: Four is the cap. A fifth chip wraps to a second row, and a second row of
#: suggestions reads as a menu rather than as a nudge.
MAX_SUGGESTIONS = 4

#: Failures in the window before a connector is worth interrupting somebody
#: about. Same threshold as the readiness checklist on purpose -- one screen
#: must not call a connector healthy while another calls it broken.
FAILURES_BEFORE_CONCERN = 3

#: Calls an agent can take without filing anything before that is worth
#: asking about. One call that books nothing is a wrong number; ten is a
#: broken tool, a missing connector, or a prompt that never asks for the
#: booking.
CALLS_BEFORE_NOTHING_FILED_MATTERS = 5

#: The only hardcoded chips in the product, and only for an account with no
#: agents. They describe jobs rather than features because the person reading
#: them has not yet decided this thing is for them.
STARTERS: tuple[str, ...] = (
    "Answer my clinic's phone and book appointments",
    "Confirm COD orders before we ship them",
    "Call back everyone who rang while we were closed",
)


def _chip(
    kind: str,
    text: str,
    *,
    action: str,
    prompt: Optional[str] = None,
    href: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "text": text,
        "action": action,
        "prompt": prompt,
        "href": href,
    }


def _app_label(app: str) -> str:
    """``googlecalendar`` -> ``Google calendar``.

    Best effort and deliberately not a lookup table: a table would need a new
    entry for every connector the vendor adds, and a missing entry would print
    nothing at all rather than a slightly awkward name.
    """
    cleaned = str(app or "").replace("_", " ").replace("-", " ").strip()
    if not cleaned:
        return "A connector"
    return cleaned[0].upper() + cleaned[1:]


def build(
    *,
    members: list[dict[str, Any]],
    failures_by_app: dict[str, int],
    unreturned_missed_calls: int = 0,
) -> list[dict[str, Any]]:
    """The chips, most urgent first, capped at four.

    ``members`` are the rows the team endpoint already computed, so this adds
    no queries of its own beyond the missed-call count.
    """
    if not members:
        return [
            _chip("starter", text, action=ACTION_PROMPT, prompt=text)
            for text in STARTERS
        ]

    chips: list[dict[str, Any]] = []

    # Something is broken. Loudest, because it is costing the customer money
    # right now and nobody has told them.
    for app, failures in sorted(
        failures_by_app.items(), key=lambda pair: pair[1], reverse=True
    ):
        if failures >= FAILURES_BEFORE_CONCERN:
            chips.append(
                _chip(
                    "connector_failing",
                    f"{_app_label(app)} failed {failures} times this week — reconnect it",
                    action=ACTION_LINK,
                    href="/integrations/apps",
                )
            )
            break

    # An agent that answers the phone and files nothing. The single most
    # expensive silent failure in the product: the calls are billed, the
    # customer believes it is working, and no record is being created.
    for member in members:
        if (
            member.get("is_live")
            and int(member.get("calls") or 0) >= CALLS_BEFORE_NOTHING_FILED_MATTERS
            and int(member.get("outcomes") or 0) == 0
        ):
            chips.append(
                _chip(
                    "no_outcomes",
                    f"{member['name']} took {member['calls']} calls and filed nothing — see what it needs",
                    action=ACTION_LINK,
                    href=f"/workflow/{member['workflow_id']}/settings",
                )
            )
            break

    if unreturned_missed_calls > 0:
        caller = "caller" if unreturned_missed_calls == 1 else "callers"
        chips.append(
            _chip(
                "missed_calls",
                f"{unreturned_missed_calls} {caller} rang and nobody called back",
                action=ACTION_LINK,
                href="/missed-calls",
            )
        )

    for member in members:
        if not member.get("is_live"):
            chips.append(
                _chip(
                    "paused",
                    f"{member['name']} is paused — turn it back on?",
                    action=ACTION_LINK,
                    href=f"/workflow/{member['workflow_id']}",
                )
            )
            break

    # Nothing is wrong. Rather than pad with something generic, offer the one
    # thing an owner with a working team actually wants next.
    if not chips:
        chips.append(
            _chip(
                "hire",
                "What else could an agent take off my hands?",
                action=ACTION_PROMPT,
                prompt="What else could an agent take off my hands?",
            )
        )

    return chips[:MAX_SUGGESTIONS]
