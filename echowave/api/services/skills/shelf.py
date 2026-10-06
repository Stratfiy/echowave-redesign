"""What an account has done with the skills catalogue.

Three questions, and the shelf is built from them: which skills are
installed, which bot each one is on, and what the rest of the catalogue
holds. Installing is deliberately separate from putting a skill on a bot --
somebody browsing wants to keep one without deciding, then attach it to
three bots at once from the card.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable, Optional

from loguru import logger

from api.db import db_client
from api.services.skills import catalogue
from api.services.skills.document import PortableSkill, prompt_block

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
    from api.services.workflow.skill_context import imported_skills

    shelved = {**catalogue.all_skills(), **await imported_skills(organization_id)}
    for slug, skill in shelved.items():
        card = skill.as_card()
        if slug in have:
            card["on_bots"] = [
                {"id": wid, "name": names.get(wid, "an agent")}
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
        logger.warning("Could not read agent names for the skills shelf: {}", exc)
        return {}
    return {row.id: row.name for row in rows}


async def install(
    organization_id: int, slug: str, *, user_id: int | None = None
) -> None:
    """Keep a skill. Installing twice is the same as installing once."""
    if catalogue.get(slug) is None:
        imported = await db_client.get_skill_document(
            organization_id=organization_id, slug=slug
        )
        if imported is None:
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
        raise SkillError("That is more agents than this account has.")

    mine = {
        row.id
        for row in await db_client.get_all_workflows_for_listing(
            organization_id=organization_id
        )
    }
    unknown = [w for w in wanted if w not in mine]
    if unknown:
        raise SkillError("One of those agents is not in this workspace.")

    for workflow_id in wanted:
        count = await db_client.count_skills_on_workflow(
            organization_id=organization_id, workflow_id=workflow_id
        )
        already = await db_client.list_organisation_skills(
            organization_id=organization_id, workflow_id=workflow_id
        )
        if slug not in {r.slug for r in already} and count >= MAX_PER_BOT:
            raise SkillError(
                f"An agent can carry {MAX_PER_BOT} skills. Take one off first."
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
            continue
        # One of the workspace's own: written on the agent's page, or
        # imported. Only a reviewed one reaches a prompt -- an import waits
        # for its review, and a skill somebody wrote here is reviewed by
        # the person who wrote it.
        own = await db_client.get_skill_document(organization_id=organization_id, slug=slug)
        if own is not None and own.reviewed_at is not None:
            blocks.append(
                prompt_block(
                    PortableSkill(name=own.title, description=own.description, body=own.body)
                )
            )
    if not blocks:
        return ""
    return (
        "The procedures this agent has been taught. Follow them when the "
        "situation they describe comes up.\n\n" + "\n\n".join(blocks)
    )


# --- skills a person writes on the agent's page ------------------------------

#: Slugs of skills written here, so they never collide with the catalogue's.
OWN_PREFIX = "own-"


def _own_slug(title: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return f"{OWN_PREFIX}{words or 'skill'}-{secrets.token_hex(3)}"


async def write_own(
    organization_id: int,
    workflow_id: int,
    *,
    title: str,
    description: str,
    user_id: int | None = None,
) -> str:
    """A skill described in plain words ("check stock in my Google Sheet
    before quoting"), kept on the shelf and put on this one agent.

    Stored as the workspace's own skill document, the way an imported skill
    is, so it lists, attaches and comes off like any other. Reviewed on
    write: the person describing it is the reviewer an import waits for.
    """
    title = (title or "").strip()
    description = (description or "").strip()
    if not title or not description:
        raise SkillError("Say what the skill is called and what it should do.")
    workflow = await db_client.get_workflow(workflow_id, organization_id=organization_id)
    if workflow is None:
        raise SkillError("That agent is not in this workspace.")
    count = await db_client.count_skills_on_workflow(
        organization_id=organization_id, workflow_id=workflow_id
    )
    if count >= MAX_PER_BOT:
        raise SkillError(f"An agent can carry {MAX_PER_BOT} skills. Take one off first.")

    slug = _own_slug(title)
    now = datetime.now(UTC)
    await db_client.upsert_skill_document(
        organization_id=organization_id,
        slug=slug,
        title=title[:200],
        description=description[:2000],
        body=description,
        metadata_={},
        source_repo="",
        source_ref="",
        source_path="",
        licence="",
        concerns=[],
        created_by=user_id,
        reviewed_by=user_id,
        reviewed_at=now,
    )
    await db_client.add_organisation_skill(
        organization_id=organization_id, slug=slug, workflow_id=None, user_id=user_id
    )
    await db_client.add_organisation_skill(
        organization_id=organization_id, slug=slug, workflow_id=workflow_id, user_id=user_id
    )
    return slug


async def take_off(organization_id: int, slug: str, workflow_id: int) -> bool:
    """Take one skill off one agent; it stays on the shelf and on the others."""
    removed = await db_client.remove_organisation_skill(
        organization_id=organization_id, slug=slug, workflow_id=workflow_id
    )
    return removed > 0
