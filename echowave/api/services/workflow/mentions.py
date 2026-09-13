"""Working out which bot a person meant.

A channel holds several bots, so "@ops-bot chase the suppliers" has to resolve
to one workflow before anything can answer it. This module is only that
resolution: it reads text and a roster and returns ids. It does not route,
reply, or record.

**Bots are addressed by a handle, not by their display name**, and that choice
removes the hard part rather than solving it. Display names are sentences --
"Narayani Dental front desk", "Meera - Decibyl Sales Assistant" -- so matching
them inside prose means deciding where the name ends, and the answer is a
guess. "@Sales bot India" could be that bot, or "Sales bot" followed by the
word India, and picking the shorter one silently addresses a different
teammate. A handle ends at a space, so there is nothing to decide.

The rule it still follows is the product's rule everywhere else: **never
guess**. A handle matching nothing resolves to nothing; a handle matching two
bots resolves to neither. Both are reported so a screen can say what happened,
because a bot answering a question addressed to a different bot is worse than
an unanswered message and far harder to explain afterwards.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Iterable, Mapping, NamedTuple

#: A handle: the characters a name is allowed to keep, and nothing else.
#:
#: Only starts after whitespace or at the beginning. Without that boundary
#: "ramesh@clinic.example" is a mention of "clinic.example", which matches no
#: bot and is therefore reported as a name nobody recognises -- so typing a
#: customer's email address would have the channel answer "there is nobody
#: here called clinic.example".
_MENTION = re.compile(r"(?:(?<=\s)|^)@([a-z0-9][a-z0-9_-]{0,63})", re.IGNORECASE)

#: Everything that is not a letter, a digit or a separator.
_UNWANTED = re.compile(r"[^a-z0-9\s_-]+")
_SEPARATORS = re.compile(r"[\s_-]+")


def handle_for(name: str) -> str:
    """The handle a display name is addressed by.

    Spaces become hyphens rather than disappearing: "front desk" reading as
    "frontdesk" is a handle somebody has to have memorised, while
    "front-desk" is one they can work out from the name on screen.

    Accents are folded because the handle is something people type on a phone
    keyboard under a fluorescent light, and "meera" should reach a bot called
    "Meerá". The display name keeps its accent; only the address loses it.
    """
    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = _UNWANTED.sub(" ", folded.casefold())
    return _SEPARATORS.sub("-", folded).strip("-")


class Mention(NamedTuple):
    workflow_id: int
    handle: str


class Resolution(NamedTuple):
    #: Bots the text addressed, in the order they were addressed.
    mentioned: list[Mention]
    #: Handles that matched no bot in this channel. Returned rather than
    #: dropped: somebody who typed "@op-bot" and got silence needs to be told
    #: the name did not match, and the only way to tell them is to know.
    unknown: list[str]
    #: Handles that matched more than one bot. Two bots called "Sales bot" in
    #: one channel is a naming problem, and answering with whichever came back
    #: first would hide it behind a bot that sometimes replies and sometimes
    #: does not.
    ambiguous: list[str]


def resolve(text: str, roster: Iterable[Mapping[str, object]]) -> Resolution:
    """Which bots this text addresses, and which handles could not be resolved.

    ``roster`` is the bots a person may address here: dicts with ``id`` and
    ``name``. Passed in rather than queried, because "which bots may this
    person address" is a tenancy answer and not a text parser's decision.
    """
    by_handle: dict[str, list[int]] = defaultdict(list)
    for bot in roster:
        if bot.get("id") is None:
            continue
        handle = handle_for(str(bot.get("name") or ""))
        if handle:
            by_handle[handle].append(int(bot["id"]))

    mentioned: list[Mention] = []
    unknown: list[str] = []
    ambiguous: list[str] = []
    seen: set[int] = set()

    for raw in _MENTION.findall(text or ""):
        handle = raw.casefold()
        matches = by_handle.get(handle) or []
        if len(matches) == 1:
            workflow_id = matches[0]
            if workflow_id not in seen:
                seen.add(workflow_id)
                mentioned.append(Mention(workflow_id=workflow_id, handle=handle))
        elif len(matches) > 1:
            if handle not in ambiguous:
                ambiguous.append(handle)
        elif handle not in unknown:
            unknown.append(handle)

    return Resolution(mentioned=mentioned, unknown=unknown, ambiguous=ambiguous)


__all__ = ["Mention", "Resolution", "handle_for", "resolve"]
