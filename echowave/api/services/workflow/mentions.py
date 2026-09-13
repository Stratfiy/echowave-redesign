"""Working out which bot a person meant.

A channel holds several bots and several people, so "@Ops bot chase the
suppliers" has to resolve to one workflow before anything can answer it. This
module is only that resolution: it reads text and a roster and returns ids. It
does not route, reply, or record — those have their own failure modes and
belong where they can be tested apart from this.

The rule it follows is the product's rule everywhere else: **never guess**. A
mention that matches nothing resolves to nothing and the caller says so. The
alternative — picking the closest name — is a bot answering a question that
was addressed to a different bot, which is worse than an unanswered message
and far harder to explain afterwards.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, NamedTuple

#: What a mention looks like before a name is matched against it.
#:
#: Bot names contain spaces -- "Narayani Dental front desk" -- so a mention
#: cannot stop at the first whitespace the way a Twitter handle does. It takes
#: everything after the @ and lets the roster decide where the name ends, which
#: is why matching is longest-name-first below.
#:
#: The leading boundary is what keeps an email address out. Without it
#: "ramesh@clinic.example" yields a mention of "clinic.example", which matches
#: no bot and is therefore reported as a name we did not recognise -- so
#: mentioning a customer's email would have the channel answer "I do not know
#: who clinic.example is". An @ only starts a mention at the beginning of the
#: text or after whitespace.
_MENTION = re.compile(r"(?:(?<=\s)|^)@([^\n@]{1,120})")


class Mention(NamedTuple):
    workflow_id: int
    name: str


class Resolution(NamedTuple):
    #: Bots the text addressed, in the order they were addressed.
    mentioned: list[Mention]
    #: Text after an @ that matched no bot on the roster. Returned rather than
    #: dropped: a person who typed "@Op bot" and got silence needs to be told
    #: the name did not match, and the only way to tell them is to know.
    unknown: list[str]


def _normalise(value: str) -> str:
    # Case and inner spacing only. Punctuation is deliberately left alone: a
    # bot really can be called "Meera — Decibyl Sales Assistant", and stripping
    # the dash would make two differently-named bots collide.
    return re.sub(r"\s+", " ", value).strip().casefold()


def resolve(text: str, roster: Iterable[Mapping[str, object]]) -> Resolution:
    """Which bots this text addresses, and which @names matched nothing.

    ``roster`` is the bots a person can address here: dicts with ``id`` and
    ``name``. Passed in rather than queried so the caller owns the tenancy
    question — a roster is the answer to "which bots may this person address",
    and that is not something a text parser should be deciding.
    """
    names = [
        (_normalise(str(bot.get("name") or "")), int(bot["id"]))
        for bot in roster
        if bot.get("name") and bot.get("id") is not None
    ]
    # Longest first, so "@Sales bot India" prefers that bot over "Sales bot".
    # Shortest-first would match the prefix and leave the rest as prose,
    # silently addressing the wrong teammate.
    names.sort(key=lambda pair: len(pair[0]), reverse=True)

    mentioned: list[Mention] = []
    unknown: list[str] = []
    seen: set[int] = set()

    for candidate in _MENTION.findall(text or ""):
        tail = _normalise(candidate)
        for name, workflow_id in names:
            if tail == name or tail.startswith(f"{name} "):
                if workflow_id not in seen:
                    seen.add(workflow_id)
                    mentioned.append(Mention(workflow_id=workflow_id, name=name))
                break
        else:
            # The first word is what somebody meant to type, and the whole tail
            # is the rest of their sentence. Reporting the sentence back would
            # be unreadable.
            first = tail.split(" ", 1)[0] if tail else ""
            if first:
                unknown.append(first)

    return Resolution(mentioned=mentioned, unknown=unknown)


__all__ = ["Mention", "Resolution", "resolve"]
