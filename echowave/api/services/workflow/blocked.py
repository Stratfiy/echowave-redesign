"""When a bot stops, name the wall and offer a way past it.

``ask_for_decision`` already does this well for a question the bot chose to
ask: a card, options, an answer written back into the same row. This is the
other half -- the times the bot did not choose to stop and something stopped
it. Those rows render as an amber icon and a sentence, and a sentence is not
something you can act on.

Three rules the wording follows, because each is a way this goes wrong:

**Name the wall, not the symptom.** "Could not finish its run" is a symptom.
"It ran out of credit" is a wall, and the difference is whether the reader
knows what to do next.

**Never offer a door that is not there.** Every way forward below is a real
route in this product. An option that 404s or that leads to a screen with
no such control is worse than no option: it costs the reader the walk.

**Lettered, and few.** Two or three choices somebody can pick between, in
the order most people need them. A wall with nine doors is a wall.

Classified at read time rather than stored on the row. The rows already
exist, so old ones benefit too, and the wording can improve without a
migration of every timeline in the product.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from api.enums import AgentEventKind

#: The kinds that mean something stopped the bot. Kept narrow on purpose:
#: ``needs_decision`` and ``needs_secret`` already have their own cards, and
#: giving them a second one would be two things to press for one problem.
BLOCKED_KINDS: frozenset[str] = frozenset(
    {AgentEventKind.COULD_NOT.value, AgentEventKind.NEEDS_ATTENTION.value}
)


@dataclass(frozen=True)
class Way:
    """One lettered choice. ``href`` is a route inside this product."""

    letter: str
    label: str
    href: str


@dataclass(frozen=True)
class Wall:
    """What stopped the bot, and what can be done about it."""

    #: Stable token, for anything that wants to count walls by kind.
    reason: str
    #: The wall in the reader's words. One sentence, no apology.
    says: str
    ways: list[Way] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "says": self.says,
            "ways": [
                {"letter": w.letter, "label": w.label, "href": w.href}
                for w in self.ways
            ],
        }


def _lettered(*pairs: tuple[str, str]) -> list[Way]:
    return [
        Way(letter=chr(ord("A") + i), label=label, href=href)
        for i, (label, href) in enumerate(pairs)
    ]


def _run_href(workflow_id: Optional[int]) -> str:
    return f"/workflow/{workflow_id}/runs" if workflow_id else "/usage"


def _no_quota(workflow_id: Optional[int]) -> Wall:
    return Wall(
        reason="no_quota",
        # Every writer of this reason refuses the run before it starts, so
        # "part-way through" was a story about a thing that never happened.
        says="It has no credit to run on, so it did not start.",
        ways=_lettered(
            ("Top up now", "/billing"),
            ("Turn on automatic top-up", "/billing"),
            ("See what it was doing", _run_href(workflow_id)),
        ),
    )


#: How many missing fields to name before summarising the rest. Three is
#: what fits a sentence somebody reads rather than parses; past that the
#: list stops being information and becomes a wall of its own.
NAMED_FIELDS = 3


def _missing_fields(fields: list[str], workflow_id: Optional[int]) -> Wall:
    named = ", ".join(fields[:NAMED_FIELDS])
    rest = len(fields) - NAMED_FIELDS
    if named and rest > 0:
        named = f"{named} and {rest} more"
    return Wall(
        reason="missing_fields",
        says=(
            f"What arrived did not include {named}, so it had nothing to work from."
            if named
            else "What arrived did not include everything it was told to expect."
        ),
        ways=_lettered(
            ("See what arrived", _run_href(workflow_id)),
            ("Change what it expects", f"/workflow/{workflow_id}/triggers")
            if workflow_id
            else ("Change what it expects", "/workflow"),
        ),
    )


def _failed(workflow_id: Optional[int]) -> Wall:
    return Wall(
        reason="failed",
        says="A step threw an error and it stopped there.",
        ways=_lettered(
            ("See what happened", _run_href(workflow_id)),
            ("Check its connections", f"/workflow/{workflow_id}/settings")
            if workflow_id
            else ("Check its connections", "/integrations"),
        ),
    )


def _unknown(workflow_id: Optional[int]) -> Wall:
    # Deliberately still a card. "Something stopped it and we cannot say
    # what" with a way to look is more useful than a bare sentence, and it
    # is honest about not knowing rather than guessing a wall.
    return Wall(
        reason="unknown",
        says="Something stopped it. Its history has the detail.",
        ways=_lettered(("See what happened", _run_href(workflow_id))),
    )


def classify(
    kind: str, payload: Any, *, workflow_id: Optional[int] = None
) -> Optional[Wall]:
    """The wall behind one timeline row, or None when the row is not a wall.

    Reads the payload the writer already recorded. Nothing here guesses from
    the summary text: a sentence written for a human is not a field, and
    matching on its words would break the first time somebody improved one.
    """
    if kind not in BLOCKED_KINDS:
        return None
    data = payload if isinstance(payload, Mapping) else {}

    if data.get("reason") == "no_quota":
        return _no_quota(workflow_id)

    missing = data.get("missing_fields")
    if isinstance(missing, (list, tuple)) and missing:
        return _missing_fields([str(f) for f in missing if f], workflow_id)

    if data.get("error"):
        return _failed(workflow_id)

    return _unknown(workflow_id)
