"""The skills a bot has, and the one the question actually invoked.

A skill is a procedure an account installed from the shelf -- how this
business chases a payment, triages a billing complaint, writes a follow-up.
The catalogue ships 103 of them, the install path works, the shelf shows
them, and the row that records the choice carries a comment about "the
order their prompt blocks are joined".

Nothing joined them. Grep the prompt builders for the word and you get
nothing back: a skill could be installed, shown as installed, and never
reach a model. It was a feature in every place except the one that matters.

**Named, not dumped.** The catalogue is 1.2 MB of text: 12 KB for the
average skill and 31 KB for the largest. Four installed skills would be a
prompt of pure procedure before the question is even asked. So there are
two levels, and the split is the same one the connected-app tools use:

- every installed skill is **one line** -- its title and what it is for, so
  the model knows it exists and can say so;
- the skill the question **names** is included whole, because that is the
  one being asked for.

Matching is on the skill's own title and slug, not its description. A
description is a paragraph written to be persuasive on a shelf ("use when
the user needs to help a customer...") and matching on it would pull 12 KB
of procedure into a prompt because somebody said the word "customer". A
title is what a person calls the thing when they mean it.

Never raises. A skill shelf that cannot be read is a prompt without one,
which is every prompt before this existed.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from api.db import db_client
from api.services.skills import catalogue

#: How many skills may be included in full in one turn.
#:
#: One. A question invoking two procedures at once is rare, and the cost of
#: being wrong is the largest single block in the prompt.
MAX_BODIES = 1

#: How much of a skill's body is carried.
#:
#: The average skill is 12 KB and the longest is 31 KB. The opening of a
#: skill is its procedure; the tail is examples and edge cases. Six thousand
#: characters is about 1,500 tokens -- affordable for the one skill actually
#: being used, and unaffordable for four that are not.
MAX_BODY_CHARS = 6_000

#: What every skill body in one prompt may cost, together.
#:
#: The per-skill cap bounds one block; this bounds the section. A bot with
#: four procedures attached is the case the per-skill cap does not catch,
#: and a caller waiting on a first syllable is the one paying for it.
BODY_BUDGET = 8_000

#: How much of a skill's description its index line carries.
MAX_LINE_CHARS = 160

#: The least body worth carrying at all. Below this the block is a heading
#: and a fragment, which reads as a procedure that stops rather than one
#: that was never there.
MIN_BODY_CHARS = 500

#: The shortest word in a title worth matching on. Three-letter words in a
#: title ("Ops", "and") match half the language.
MIN_WORD = 4

#: Title words that are what a skill *is*, not what it is *about*. A
#: question saying "design" should not invoke every design skill at once.
_GENERIC = frozenset(
    {
        "ops",
        "operations",
        "management",
        "engine",
        "report",
        "structure",
        "review",
        "reviewer",
        "specialist",
        "architect",
        "guardian",
        "discovery",
        "audit",
        "tracking",
        "writing",
        "research",
        "deep",
        "gate",
        "finish",
        "walkthrough",
    }
)

_WORDS = re.compile(r"[A-Za-z]+")


def _title_words(skill: Any) -> set[str]:
    """The words in a skill's title and slug that could name it."""
    text = f"{getattr(skill, 'title', '')} {getattr(skill, 'slug', '')}"
    return {
        word.lower()
        for word in _WORDS.findall(text)
        if len(word) >= MIN_WORD and word.lower() not in _GENERIC
    }


def named(question: str, skills: list[Any]) -> list[Any]:
    """The installed skills this question invokes by name.

    A skill is invoked when the question uses a word from its title that is
    not one of the words every skill shares. Ordered by how many such words
    matched, so "chase the billing refund" reaches the billing skill rather
    than whichever sorted first.
    """
    if not question or not skills:
        return []
    asked = {w.lower() for w in _WORDS.findall(question) if len(w) >= MIN_WORD}
    if not asked:
        return []
    scored = []
    for skill in skills:
        overlap = len(_title_words(skill) & asked)
        if overlap:
            scored.append((overlap, getattr(skill, "slug", ""), skill))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [skill for _, _, skill in scored[:MAX_BODIES]]


def block(
    installed: list[Any],
    invoked: list[Any] | None = None,
    *,
    budget: int = BODY_BUDGET,
) -> str:
    """The skills as the model reads them: every one named, the invoked in full.

    ``budget`` is what all the bodies may cost together. Spent in order, so
    the first skill invoked is the one that gets its whole procedure and a
    fourth one silently gets none rather than all four getting a quarter
    each -- half a procedure is worse than no procedure, because a model
    will follow it to where it stops.
    """
    if not installed:
        return ""
    invoked = invoked or []
    lines = []
    for skill in installed:
        title = getattr(skill, "title", None) or getattr(skill, "slug", "a skill")
        description = (getattr(skill, "description", "") or "").strip()
        lines.append(f"- {title}: {description[:MAX_LINE_CHARS]}")
    out = ["\n".join(lines)]
    left = budget
    for skill in invoked:
        body = (getattr(getattr(skill, "skill", None), "body", "") or "").strip()
        if not body:
            continue
        room = min(left, MAX_BODY_CHARS)
        if room < MIN_BODY_CHARS:
            break
        title = getattr(skill, "title", None) or getattr(skill, "slug", "a skill")
        carried = body[:room]
        left -= len(carried)
        out.append(f"### {title}, in full\n{carried}")
    return "\n\n".join(out)


async def installed_for(
    organization_id: int, workflow_id: int | None = None
) -> list[Any]:
    """The catalogue entries for the skills this account installed.

    A row whose slug is no longer in the catalogue is skipped rather than
    rendered as a blank: the slug is deliberately not a foreign key, so a
    skill withdrawn from a release leaves the row alone, and a prompt is
    not the place to find out.

    Never raises: skills are one reading of several.
    """
    try:
        rows = await db_client.list_organisation_skills(
            organization_id=organization_id, workflow_id=workflow_id
        )
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("Could not read the skills for org {}: {}", organization_id, exc)
        return []
    found = []
    seen: set[str] = set()
    imported = None
    for row in rows:
        slug = getattr(row, "slug", None)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        entry = catalogue.get(slug)
        if entry is None:
            # Not shipped: an imported one, body kept in the workspace (D-1b).
            if imported is None:
                imported = await imported_skills(organization_id)
            entry = imported.get(slug)
        if entry is not None:
            found.append(entry)
    return found


async def imported_skills(organization_id: int) -> dict[str, Any]:
    """The skills this workspace imported, as catalogue entries. Never
    raises: one reading of several."""
    from api.services.skills.catalogue import CatalogueSkill
    from api.services.skills.document import PortableSkill

    try:
        rows = await db_client.list_skill_documents(organization_id=organization_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not read imported skills for org {}: {}", organization_id, exc
        )
        return {}
    out: dict[str, Any] = {}
    for row in rows:
        out[row.slug] = CatalogueSkill(
            slug=row.slug,
            title=row.title,
            description=row.description or "",
            division="Imported",
            emoji=str((row.metadata_ or {}).get("emoji") or ""),
            source=row.source_repo or "",
            license=row.licence or "",
            skill=PortableSkill(
                name=row.slug,
                description=row.description or "",
                body=row.body or "",
                metadata=dict(row.metadata_ or {}),
            ),
        )
    return out
