"""What an account has done with the skills catalogue.

Three questions, and the shelf is built from them: which skills are
installed, which bot each one is on, and what the rest of the catalogue
holds. Installing is deliberately separate from putting a skill on a bot --
somebody browsing wants to keep one without deciding, then attach it to
three bots at once from the card.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from loguru import logger

from api.db import db_client
from api.services.skills import catalogue
from api.services.skills.document import prompt_block

#: How many skills one bot may carry. Each one is a body of prompt text on
#: every turn, so this is a cost ceiling as much as a sanity one.
MAX_PER_BOT = 8


class SkillError(ValueError):
    """Something a person should be told, in their words."""


@dataclass(frozen=True)
class Installed:
    slug: str
    #: Workflow ids this skill is on, in the order they were added.
    on_bots: tuple[int, ...]


async def installed(organization_id: int) -> dict[str, Installed]:
    """Every skill this account keeps, and the bots each is on."""
    rows = await db_client.list_organisation_skills(organization_id=organization_id)
    by_slug: dict[str, list[int]] = {}
    for row in rows:
        by_slug.setdefault(row.slug, [])
        if row.workflow_id is not None:
            by_slug[row.slug].append(row.workflow_id)
    return {slug: Installed(slug, tuple(bots)) for slug, bots in by_slug.items()}


async def shelf(organization_id: int) -> dict[str, Any]:
    """The Skills shelf: what is installed, then everything else.

    Installed first because that is the founder's rule for every shelf here
    and the right one: what you already have is what you came back for.
    """
    have = await installed(organization_id)
    names = await _bot_names(organization_id)
    cards_installed: list[dict[str, Any]] = []
    cards_rest: list[dict[str, Any]] = []
    for slug, skill in catalogue.all_skills().items():
        card = skill.as_card()
        if slug in have:
            card["on_bots"] = [
                {"id": wid, "name": names.get(wid, "a bot")}
                for wid in have[slug].on_bots
            ]
            cards_installed.append(card)
        else:
            cards_rest.append(card)
    return {
        "installed": cards_installed,
        "skills": cards_rest,
        "divisions": catalogue.divisions(),
        "attributions": catalogue.attributions(),
        "max_per_bot": MAX_PER_BOT,
    }


async def _bot_names(organization_id: int) -> dict[int, str]:
    try:
        rows = await db_client.get_all_workflows_for_listing(
            organization_id=organization_id
        )
    except Exception as exc:  # noqa: BLE001 - a name is a nicety
        logger.warning("Could not read bot names for the skills shelf: {}", exc)
        return {}
    return {row.id: row.name for row in rows}


async def install(
    organization_id: int, slug: str, *, user_id: int | None = None
) -> None:
    """Keep a skill. Installing twice is the same as installing once."""
    if catalogue.get(slug) is None:
        raise SkillError(f"There is no skill called {slug!r}.")
    await db_client.add_organisation_skill(
        organization_id=organization_id, slug=slug, workflow_id=None, user_id=user_id
    )


async def uninstall(organization_id: int, slug: str) -> None:
    """Drop a skill and take it off every bot it was on."""
    await db_client.remove_organisation_skill(
        organization_id=organization_id, slug=slug, workflow_id=None, all_rows=True
    )


async def set_bots(
    organization_id: int,
    slug: str,
    workflow_ids: Iterable[int],
    *,
    user_id: int | None = None,
) -> list[int]:
    """Put one skill on exactly these bots, and take it off the rest.

    The whole set rather than one at a time, because the card offers a
    multi-select: unticking a bot has to mean something, and a
    one-at-a-time API makes "unticked" indistinguishable from "not sent".

    Every id is checked against this organisation before anything is
    written. A workflow id in a request body proves nothing.
    """
    if catalogue.get(slug) is None:
        raise SkillError(f"There is no skill called {slug!r}.")
    wanted = list(dict.fromkeys(int(w) for w in workflow_ids))
    if len(wanted) > MAX_PER_BOT * 50:
        raise SkillError("That is more bots than this account has.")

    mine = {
        row.id
        for row in await db_client.get_all_workflows_for_listing(
            organization_id=organization_id
        )
    }
    unknown = [w for w in wanted if w not in mine]
    if unknown:
        raise SkillError("One of those bots is not in this workspace.")

    for workflow_id in wanted:
        count = await db_client.count_skills_on_workflow(
            organization_id=organization_id, workflow_id=workflow_id
        )
        already = await db_client.list_organisation_skills(
            organization_id=organization_id, workflow_id=workflow_id
        )
        if slug not in {r.slug for r in already} and count >= MAX_PER_BOT:
            raise SkillError(
                f"A bot can carry {MAX_PER_BOT} skills. Take one off first."
            )

    await db_client.set_skill_bots(
        organization_id=organization_id,
        slug=slug,
        workflow_ids=wanted,
        user_id=user_id,
    )
    return wanted


async def for_workflow(organization_id: int, workflow_id: int) -> list[str]:
    """The slugs on one bot, in the order they were added."""
    rows = await db_client.list_organisation_skills(
        organization_id=organization_id, workflow_id=workflow_id
    )
    return [row.slug for row in rows if row.workflow_id == workflow_id]


async def prompt_for_workflow(
    organization_id: Optional[int], workflow_id: Optional[int]
) -> str:
    """Every skill on this bot, as one block for its prompt.

    Empty on anything at all going wrong: a bot that answers without a
    skill is a bot that answers less well, and one that raises because the
    skills table could not be read is a call nobody picks up.
    """
    if not organization_id or not workflow_id:
        return ""
    try:
        slugs = await for_workflow(organization_id, workflow_id)
    except Exception as exc:  # noqa: BLE001 - a skill is an improvement
        logger.warning("Could not read skills for workflow {}: {}", workflow_id, exc)
        return ""
    blocks = []
    for slug in slugs[:MAX_PER_BOT]:
        skill = catalogue.get(slug)
        if skill is not None:
            blocks.append(prompt_block(skill.skill))
    if not blocks:
        return ""
    return (
        "The procedures this bot has been taught. Follow them when the "
        "situation they describe comes up.\n\n" + "\n\n".join(blocks)
    )
