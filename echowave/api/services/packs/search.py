"""Finding the role that matches what somebody just said about their business.

Reaches through to the wrapped template's ``example_requests``, which is where
the phrasings actually live -- "clinic ka phone uthana hai" and "front desk for
my dental practice" both have to land on Front Desk, and neither of those
strings appears anywhere in the pack itself.

Scoring is crude on purpose. This narrows a shelf of six or sixty for a reader
-- a person scanning cards or a model about to read every match anyway -- and a
cleverer ranker would only be a second thing to keep correct. What matters is
that nothing matching is *dropped*: a role that exists and cannot be found is
worse than a role ranked third.
"""

from __future__ import annotations

from typing import Iterable, Optional

from api.services.packs._base import AgentPack, Channel
from api.services.packs.catalogue import listed_packs


def _haystack(pack: AgentPack) -> str:
    """Everything about a pack worth matching a sentence against."""
    parts = [pack.name, pack.job, pack.summary, *pack.industries]
    template = pack.template
    if template is not None:
        # The phrasings a real person uses live on the template, not here.
        parts += [template.vertical, template.summary, *template.example_requests]
    return " ".join(parts).lower()


def search_packs(
    query: str,
    *,
    packs: Optional[Iterable[AgentPack]] = None,
) -> tuple[AgentPack, ...]:
    """Roles matching free text, best first.

    An empty or too-short query returns the whole shelf rather than nothing.
    Somebody who typed two characters wants to browse, and an empty result
    would read as "we have nothing for you".
    """
    shelf = tuple(listed_packs() if packs is None else packs)
    terms = [term for term in query.lower().split() if len(term) > 2]
    if not terms:
        return shelf

    scored: list[tuple[int, int, AgentPack]] = []
    for index, pack in enumerate(shelf):
        haystack = _haystack(pack)
        score = sum(1 for term in terms if term in haystack)
        # A match on the role's own name outranks a match buried in a
        # template's prose: somebody typing "front desk" means the role.
        if any(term in pack.name.lower() for term in terms):
            score += 3
        if score:
            # Index keeps the shelf's own order as the tie-break, which is the
            # order we want people to hire in.
            scored.append((score, -index, pack))

    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return tuple(pack for _, _, pack in scored)


def filter_packs(
    *,
    job: Optional[str] = None,
    industry: Optional[str] = None,
    language: Optional[str] = None,
    calling: Optional[bool] = None,
    packs: Optional[Iterable[AgentPack]] = None,
) -> tuple[AgentPack, ...]:
    """The shelf, narrowed.

    Every filter is case-insensitive and every one is optional. Industry and
    language match membership rather than equality, because a role serves
    several of each and a card claiming five languages must be findable by all
    five.
    """
    shelf = tuple(listed_packs() if packs is None else packs)
    out = []
    for pack in shelf:
        if job and pack.job.lower() != job.lower():
            continue
        if industry and industry.lower() not in {
            value.lower() for value in pack.industries
        }:
            continue
        if language and language.lower() not in {
            value.lower() for value in pack.languages
        }:
            continue
        if calling is not None:
            makes_calls = bool(
                set(pack.channels) & {Channel.INBOUND_CALL, Channel.OUTBOUND_CALL}
            )
            if makes_calls is not calling:
                continue
        out.append(pack)
    return tuple(out)
