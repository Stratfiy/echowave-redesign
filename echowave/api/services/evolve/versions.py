"""Skill versions: the card, publishing by a person, history and rollback.

The active version of a skill is the newest ``published`` row. Nothing
publishes one except a person pressing Publish on its card (or on the
Skills tab), and that person must be allowed to: a workspace skill needs an
admin or the owner; a draft somebody remembered for themselves is theirs
alone until they publish it.

Rolling back marks the active version ``rolled_back``; the version before it
is active again, with exactly the text it had, because nothing about it was
ever changed. That is the whole reason versions are rows rather than edits.

A learned version of a shipped skill holds only its lessons -- the shipped
text stays the shipped text and the lessons are added beneath it. A skill a
person remembered holds all of itself.
"""

from __future__ import annotations

import dataclasses
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api.db import db_client
from api.enums import ORGANIZATION_ROLE_RANK, AgentEventActor, AgentEventKind
from api.services import evolve
from api.services.evolve import guard
from api.services.workflow import agent_timeline

#: Card types, in ``payload["type"]``.
CARD_OFFER = "offer"
CARD_REMEMBERED = "remembered"
CARD_DISABLE = "disable"
CARD_ATTACH = "attach"

#: Which presses each card type takes.
CARD_ACTIONS: dict[str, tuple[str, ...]] = {
    CARD_OFFER: ("publish", "discard"),
    CARD_REMEMBERED: ("publish", "discard"),
    CARD_DISABLE: ("rollback", "keep"),
    CARD_ATTACH: ("add", "discard"),
}


class VersionError(ValueError):
    """Something the person should be told, in their words."""


# --- what a version reads as --------------------------------------------------


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def render(content: dict[str, Any]) -> str:
    """A version's procedure as the agent reads it. Fixed sections in a
    fixed order, so the same content always renders the same text."""
    parts: list[str] = []
    if content.get("when_to_use"):
        parts.append(f"## When to use\n{content['when_to_use']}")
    if content.get("steps"):
        parts.append(
            "## Steps\n"
            + "\n".join(f"{n}. {s}" for n, s in enumerate(content["steps"], 1))
        )
    if content.get("inputs"):
        parts.append("## What it needs\n" + _bullets(content["inputs"]))
    if content.get("outputs"):
        parts.append("## What it produces\n" + _bullets(content["outputs"]))
    if content.get("wont_do"):
        parts.append("## What it will not do\n" + _bullets(content["wont_do"]))
    if content.get("example"):
        parts.append(f"## Example\n{content['example']}")
    return "\n\n".join(parts)


def lessons_section(lessons: list[dict[str, Any]], version: int | None) -> str:
    if not lessons:
        return ""
    label = f" (version {version})" if version else ""
    return (
        f"## Learned from this workspace's own work{label}\n"
        f"{guard.PROMPT_FENCE}\n"
        + _bullets([str(lesson.get("text") or "") for lesson in lessons])
    )


def is_whole(content: dict[str, Any]) -> bool:
    """Whether a version carries a whole procedure, or only lessons to add
    beneath one."""
    return bool(content.get("steps"))


def body_for(content: dict[str, Any], base_body: str, version: int | None) -> str:
    own = render(content) if is_whole(content) else base_body
    lessons = lessons_section(list(content.get("lessons") or []), version)
    return "\n\n".join(p for p in (own, lessons) if p)


async def active(organization_id: int, slug: str) -> Any | None:
    try:
        return await db_client.active_skill_version(
            organization_id=organization_id, slug=slug
        )
    except Exception as exc:  # noqa: BLE001 - one reading of several
        logger.warning("evolve: could not read the version of {}: {}", slug, exc)
        return None


async def overlay(organization_id: int | None, slug: str, portable: Any) -> Any:
    """``portable`` as the workspace's active version has it. The same object
    when the flag is off or there is no version, so a prompt with nothing
    learned is byte for byte what it was. Never raises."""
    if not organization_id or not evolve.enabled(organization_id):
        return portable
    row = await active(organization_id, slug)
    if row is None:
        return portable
    content = dict(row.content or {})
    if guard.check(content):
        # Validated on every write, so this is a row somebody changed by
        # hand. It does not reach a prompt.
        logger.error("evolve: version {} of {} fails the guard", row.version, slug)
        return portable
    try:
        return dataclasses.replace(
            portable,
            body=body_for(content, getattr(portable, "body", "") or "", row.version),
        )
    except Exception as exc:  # noqa: BLE001 - the prompt without the version
        logger.warning("evolve: could not apply version of {}: {}", slug, exc)
        return portable


async def overlay_entry(organization_id: int | None, entry: Any) -> Any:
    """The same, for a catalogue entry (services/workflow/skill_context)."""
    skill = getattr(entry, "skill", None)
    if skill is None:
        return entry
    changed = await overlay(organization_id, entry.slug, skill)
    if changed is skill:
        return entry
    return dataclasses.replace(entry, skill=changed)


# --- who may decide -----------------------------------------------------------


async def _role_rank(organization_id: int, user_id: int) -> int:
    membership = await db_client.get_membership(user_id, organization_id)
    return ORGANIZATION_ROLE_RANK.get(membership.role if membership else "", -1)


async def may_decide(organization_id: int, user_id: int, row: Any) -> bool:
    """A private draft: its owner. Anything that changes a workspace skill:
    an admin or the owner of the workspace."""
    if row.owner_user_id is not None:
        return row.owner_user_id == user_id
    return await _role_rank(organization_id, user_id) >= ORGANIZATION_ROLE_RANK["admin"]


def visible_to(row: Any, user_id: int | None) -> bool:
    return row.owner_user_id is None or row.owner_user_id == user_id


# --- writing versions ---------------------------------------------------------


async def create(
    *,
    organization_id: int,
    slug: str,
    content: dict[str, Any],
    origin: str,
    status: str = evolve.DRAFT,
    base_version: int | None = None,
    evidence: list[dict[str, Any]] | None = None,
    evaluation: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
    workflow_id: int | None = None,
    thread_id: str | None = None,
    author_user_id: int | None = None,
    owner_user_id: int | None = None,
    reason: str | None = None,
) -> Any:
    """A new version row. The guard runs here for every origin: a version
    that touches a control is written as ``rejected`` with the reason, never
    as anything that could be offered."""
    shaped = guard.normalise(content)
    violations = guard.check(shaped)
    if violations:
        status = evolve.REJECTED
        reason = guard.reason(violations)
        # The offending text is not kept on a rejected row either: it is the
        # reason that is the record, and it quotes enough.
        shaped = {k: v for k, v in shaped.items() if k in ("title", "description")}
        shaped = guard.strip(shaped)[0]
    return await db_client.create_skill_version(
        organization_id=organization_id,
        slug=slug,
        content=shaped,
        origin=origin,
        status=status,
        base_version=base_version,
        evidence=evidence or [],
        evaluation=evaluation,
        cost=cost or {},
        workflow_id=workflow_id,
        thread_id=thread_id,
        author_user_id=author_user_id,
        owner_user_id=owner_user_id,
        reason=(reason or None) and reason[:500],
    )


async def edit_draft(
    organization_id: int, version_id: int, user_id: int, content: dict[str, Any]
) -> Any:
    """A person edits a draft before publishing it. Their own words go
    through the same guard; a line about a control is refused, not saved."""
    row = await db_client.get_skill_version(version_id, organization_id=organization_id)
    if row is None or not visible_to(row, user_id):
        raise VersionError("That draft is not here.")
    if (
        row.status not in (evolve.DRAFT, evolve.OFFERED)
        or row.origin == evolve.ORIGIN_LEARNED
    ):
        raise VersionError("Only a draft you are writing can be edited.")
    if not await may_decide(organization_id, user_id, row):
        raise VersionError("Only the person it belongs to can edit this draft.")
    try:
        shaped = guard.validate(content)
    except guard.GuardRejected as exc:
        raise VersionError(str(exc)) from exc
    if not (shaped.get("title") or "").strip():
        raise VersionError("Give it a name.")
    await db_client.update_skill_version(
        version_id, organization_id=organization_id, content=shaped
    )
    if row.card_event_id:
        event = await db_client.get_agent_event(
            row.card_event_id, organization_id=organization_id
        )
        if event is not None:
            payload = dict(event.payload or {})
            payload["content"] = shaped
            payload["title"] = shaped.get("title") or payload.get("title")
            await db_client.set_agent_event_payload(
                event.id, organization_id=organization_id, payload=payload
            )
    return await db_client.get_skill_version(
        version_id, organization_id=organization_id
    )


def _changes(
    content: dict[str, Any], base: dict[str, Any] | None
) -> list[dict[str, str]]:
    """The lessons this version adds and drops against its base, itemised."""
    before = {str(lesson.get("text")) for lesson in (base or {}).get("lessons") or []}
    after = [str(lesson.get("text")) for lesson in content.get("lessons") or []]
    out = [{"op": "add", "text": t} for t in after if t not in before]
    out += [{"op": "remove", "text": t} for t in sorted(before) if t not in set(after)]
    return out


async def _title(organization_id: int, slug: str, content: dict[str, Any]) -> str:
    if content.get("title"):
        return str(content["title"])
    from api.services.skills import catalogue

    entry = catalogue.get(slug)
    if entry is not None:
        return entry.title
    doc = await db_client.get_skill_document(organization_id=organization_id, slug=slug)
    return doc.title if doc is not None else slug


async def post_card(
    organization_id: int,
    row: Any,
    card_type: str,
    extra: dict[str, Any] | None = None,
) -> int | None:
    """Put the card on the thread the version belongs to and remember its id."""
    content = dict(row.content or {})
    title = await _title(organization_id, row.slug, content)
    base = None
    if row.base_version is not None:
        base_rows = await db_client.list_skill_versions(
            organization_id=organization_id, slug=row.slug
        )
        base = next((b for b in base_rows if b.version == row.base_version), None)
    payload: dict[str, Any] = {
        "type": card_type,
        "version_id": row.id,
        "slug": row.slug,
        "title": title,
        "version": row.version,
        "base_version": row.base_version,
        "origin": row.origin,
        "changes": _changes(content, dict(base.content or {}) if base else None),
        "evidence": list(row.evidence or [])[:12],
        "evaluation": row.evaluation,
        "cost": row.cost or {},
        **(extra or {}),
    }
    if card_type == CARD_REMEMBERED:
        payload["content"] = content
    if row.owner_user_id is not None:
        payload["private_to"] = str(row.owner_user_id)
    summary = {
        CARD_OFFER: f"I've learned a better way to {title}",
        CARD_REMEMBERED: f"Remembered as your way: {title}",
        CARD_DISABLE: f"{title} has done worse since version {row.version}",
        CARD_ATTACH: f"Add {title} to an agent?",
    }.get(card_type, title)
    event_id = await agent_timeline.record(
        organization_id=organization_id,
        kind=AgentEventKind.SKILL_LESSON.value,
        actor=AgentEventActor.AGENT.value,
        summary=summary[:300],
        workflow_id=row.workflow_id,
        payload=payload,
        in_channel=row.workflow_id is not None,
        thread_id=row.thread_id,
    )
    if event_id is not None and card_type in (CARD_OFFER, CARD_REMEMBERED):
        await db_client.update_skill_version(
            row.id, organization_id=organization_id, card_event_id=event_id
        )
    return event_id


async def offer(organization_id: int, version_id: int) -> int | None:
    """A version that passed the gate becomes a card. Draft -> offered by
    compare-and-swap, so a retried job posts one card."""
    row = await db_client.get_skill_version(version_id, organization_id=organization_id)
    if row is None or guard.check(dict(row.content or {})):
        return None
    if not await db_client.move_skill_version(
        version_id,
        organization_id=organization_id,
        from_statuses=[evolve.DRAFT],
        to_status=evolve.OFFERED,
    ):
        return None
    row = await db_client.get_skill_version(version_id, organization_id=organization_id)
    return await post_card(organization_id, row, CARD_OFFER)


async def _note(organization_id: int, row: Any, line: str, user_id: int) -> None:
    try:
        await agent_timeline.record(
            organization_id=organization_id,
            kind=AgentEventKind.MESSAGE.value,
            actor=AgentEventActor.HUMAN.value,
            summary=line[:500],
            workflow_id=row.workflow_id,
            payload={"body": line, "author_id": user_id, "skill_version_id": row.id},
            in_channel=False,
            thread_id=row.thread_id,
        )
    except Exception as exc:  # noqa: BLE001 - the decision stands regardless
        logger.warning("evolve: could not note the decision on the thread: {}", exc)


_SLUG_WORDS = re.compile(r"[^a-z0-9]+")


def own_slug(title: str) -> str:
    from api.services.skills.shelf import OWN_PREFIX

    words = _SLUG_WORDS.sub("-", (title or "").lower()).strip("-")[:40].strip("-")
    return f"{OWN_PREFIX}{words or 'skill'}-{secrets.token_hex(3)}"


async def _write_document(organization_id: int, row: Any, user_id: int) -> None:
    """A remembered skill becomes one of the workspace's own on the shelf,
    reviewed by the person publishing it, and on the agent it came from."""
    content = dict(row.content or {})
    now = datetime.now(UTC)
    await db_client.upsert_skill_document(
        organization_id=organization_id,
        slug=row.slug,
        title=str(content.get("title") or row.slug)[:200],
        description=str(content.get("description") or content.get("when_to_use") or "")[
            :2000
        ],
        body=render(content),
        metadata_={"origin": row.origin, "version": row.version},
        source_repo="",
        source_ref="",
        source_path="",
        licence="",
        concerns=[],
        created_by=row.author_user_id,
        reviewed_by=user_id,
        reviewed_at=now,
    )
    await db_client.add_organisation_skill(
        organization_id=organization_id,
        slug=row.slug,
        workflow_id=None,
        user_id=user_id,
    )
    if row.workflow_id is not None:
        await db_client.add_organisation_skill(
            organization_id=organization_id,
            slug=row.slug,
            workflow_id=row.workflow_id,
            user_id=user_id,
        )


async def publish(organization_id: int, version_id: int, user_id: int) -> Any:
    row = await db_client.get_skill_version(version_id, organization_id=organization_id)
    if row is None or not visible_to(row, user_id):
        raise VersionError("That version is not here.")
    if row.status not in (evolve.DRAFT, evolve.OFFERED):
        raise VersionError("That version has already been settled.")
    if row.origin == evolve.ORIGIN_LEARNED and row.status != evolve.OFFERED:
        # A learned version is offered only after the gate passed it.
        raise VersionError("That version has not passed its evaluation.")
    if not await may_decide(organization_id, user_id, row):
        raise VersionError(
            "Only an admin of this workspace can publish a change to it."
        )
    try:
        guard.validate(dict(row.content or {}))
    except guard.GuardRejected as exc:
        raise VersionError(str(exc)) from exc
    if row.origin == evolve.ORIGIN_REMEMBERED and not (row.content or {}).get("steps"):
        raise VersionError("Add at least one step before saving it as a skill.")
    if row.origin == evolve.ORIGIN_REMEMBERED:
        await _write_document(organization_id, row, user_id)
    now = datetime.now(UTC)
    moved = await db_client.move_skill_version(
        version_id,
        organization_id=organization_id,
        from_statuses=[evolve.DRAFT, evolve.OFFERED],
        to_status=evolve.PUBLISHED,
        decided_by=user_id,
        decided_at=now,
        published_at=now,
        owner_user_id=None,
    )
    if not moved:
        raise VersionError("That version has already been settled.")
    title = await _title(organization_id, row.slug, dict(row.content or {}))
    await _note(
        organization_id, row, f"Published version {row.version} of {title}", user_id
    )
    return await db_client.get_skill_version(
        version_id, organization_id=organization_id
    )


async def discard(
    organization_id: int, version_id: int, user_id: int, reason: str = ""
) -> Any:
    row = await db_client.get_skill_version(version_id, organization_id=organization_id)
    if row is None or not visible_to(row, user_id):
        raise VersionError("That version is not here.")
    if not await may_decide(organization_id, user_id, row):
        raise VersionError("Only an admin of this workspace can turn this down.")
    if not await db_client.move_skill_version(
        version_id,
        organization_id=organization_id,
        from_statuses=[evolve.DRAFT, evolve.OFFERED],
        to_status=evolve.DISCARDED,
        decided_by=user_id,
        decided_at=datetime.now(UTC),
        reason=(reason or "Not now")[:500],
    ):
        raise VersionError("That version has already been settled.")
    return await db_client.get_skill_version(
        version_id, organization_id=organization_id
    )


async def rollback(
    organization_id: int, slug: str, user_id: int, reason: str = ""
) -> dict[str, Any]:
    """Take the active version back out. The one before it is active again,
    exactly as it was; with none before it, the skill reads as shipped."""
    row = await db_client.active_skill_version(
        organization_id=organization_id, slug=slug
    )
    if row is None:
        raise VersionError("There is no version of this skill to roll back.")
    if await _role_rank(organization_id, user_id) < ORGANIZATION_ROLE_RANK["admin"]:
        raise VersionError("Only an admin of this workspace can roll a skill back.")
    published = await db_client.list_skill_versions(
        organization_id=organization_id, slug=slug, statuses=[evolve.PUBLISHED]
    )
    if row.origin == evolve.ORIGIN_REMEMBERED and len(published) == 1:
        raise VersionError(
            "This is the first version of a skill you wrote. Take it off its "
            "agents instead."
        )
    if not await db_client.move_skill_version(
        row.id,
        organization_id=organization_id,
        from_statuses=[evolve.PUBLISHED],
        to_status=evolve.ROLLED_BACK,
        rolled_back_by=user_id,
        rolled_back_at=datetime.now(UTC),
        reason=(reason or "Rolled back")[:500],
    ):
        raise VersionError("That version has already been rolled back.")
    now_active = await db_client.active_skill_version(
        organization_id=organization_id, slug=slug
    )
    title = await _title(organization_id, slug, dict(row.content or {}))
    back_to = f"version {now_active.version}" if now_active else "the shipped version"
    await _note(
        organization_id,
        row,
        f"Rolled {title} back from version {row.version} to {back_to}",
        user_id,
    )
    return {
        "slug": slug,
        "rolled_back": row.version,
        "active": now_active.version if now_active else None,
    }


# --- the card's buttons ---------------------------------------------------------


async def settle(
    *, organization_id: int, event_id: int, action: str, user_id: int
) -> dict[str, Any]:
    """What a press on a learning card does. Stamped into the card's own
    row, so whoever opens the thread next sees what was decided and by whom."""
    event = await db_client.get_agent_event(event_id, organization_id=organization_id)
    if event is None or event.kind != AgentEventKind.SKILL_LESSON.value:
        raise VersionError("That card is not here.")
    payload = dict(event.payload or {})
    private = payload.get("private_to")
    if private and str(private) != str(user_id):
        raise VersionError("That card is not here.")
    card_type = payload.get("type")
    if action not in CARD_ACTIONS.get(card_type, ()):
        raise VersionError("That is not something this card can do.")
    if payload.get("decided"):
        raise VersionError("Already settled.")
    version_id = payload.get("version_id")
    if card_type in (CARD_OFFER, CARD_REMEMBERED):
        if action == "publish":
            await publish(organization_id, int(version_id), user_id)
        else:
            await discard(organization_id, int(version_id), user_id)
    elif card_type == CARD_DISABLE:
        if action == "rollback":
            row = await db_client.get_skill_version(
                int(version_id), organization_id=organization_id
            )
            active_row = await db_client.active_skill_version(
                organization_id=organization_id, slug=payload.get("slug") or ""
            )
            if row is None or active_row is None or active_row.id != row.id:
                raise VersionError("That version is no longer the one in use.")
            await rollback(
                organization_id,
                row.slug,
                user_id,
                reason="Outcomes were worse on later tasks",
            )
        elif (
            await _role_rank(organization_id, user_id) < ORGANIZATION_ROLE_RANK["admin"]
        ):
            raise VersionError("Only an admin of this workspace can decide this.")
    elif card_type == CARD_ATTACH:
        if action == "add":
            await attach(
                organization_id,
                payload.get("slug") or "",
                int(payload.get("workflow_id") or 0),
                user_id,
            )
    payload["decided"] = {
        "action": action,
        "by": user_id,
        "at": datetime.now(UTC).isoformat(),
    }
    if not await db_client.set_agent_event_payload(
        event_id, organization_id=organization_id, payload=payload
    ):
        raise VersionError("That card is not here.")
    return payload


async def attach(
    organization_id: int, slug: str, workflow_id: int, user_id: int
) -> None:
    """Put a skill on one agent, keeping it on the others. The agent is
    checked against this workspace; an id in a card proves nothing."""
    from api.services.skills import catalogue, shelf

    workflow = await db_client.get_workflow(
        workflow_id, organization_id=organization_id
    )
    if workflow is None:
        raise VersionError("That agent is not in this workspace.")
    known = catalogue.get(slug) or await db_client.get_skill_document(
        organization_id=organization_id, slug=slug
    )
    if known is None:
        raise VersionError("There is no skill by that name here.")
    already = await shelf.for_workflow(organization_id, workflow_id)
    if slug in already:
        return
    if len(already) >= shelf.MAX_PER_BOT:
        raise VersionError(
            f"An agent can carry {shelf.MAX_PER_BOT} skills. Take one off first."
        )
    await db_client.add_organisation_skill(
        organization_id=organization_id, slug=slug, workflow_id=None, user_id=user_id
    )
    await db_client.add_organisation_skill(
        organization_id=organization_id,
        slug=slug,
        workflow_id=workflow_id,
        user_id=user_id,
    )


# --- history --------------------------------------------------------------------


def describe(row: Any) -> dict[str, Any]:
    content = dict(row.content or {})
    return {
        "id": row.id,
        "slug": row.slug,
        "version": row.version,
        "status": row.status,
        "origin": row.origin,
        "base_version": row.base_version,
        "lessons": [
            str(lesson.get("text") or "") for lesson in content.get("lessons") or []
        ],
        "content": content,
        "evidence": list(row.evidence or []),
        "evaluation": row.evaluation,
        "cost": row.cost or {},
        "author_user_id": row.author_user_id,
        "decided_by": row.decided_by,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "rolled_back_at": row.rolled_back_at.isoformat()
        if row.rolled_back_at
        else None,
        "rolled_back_by": row.rolled_back_by,
        "reason": row.reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "card_event_id": row.card_event_id,
    }


async def history(
    organization_id: int, slug: str | None, viewer_user_id: int | None
) -> list[dict[str, Any]]:
    rows = await db_client.list_skill_versions(
        organization_id=organization_id, slug=slug
    )
    return [describe(r) for r in rows if visible_to(r, viewer_user_id)]


__all__ = [
    "CARD_ACTIONS",
    "CARD_ATTACH",
    "CARD_DISABLE",
    "CARD_OFFER",
    "CARD_REMEMBERED",
    "VersionError",
    "active",
    "attach",
    "body_for",
    "create",
    "describe",
    "discard",
    "edit_draft",
    "history",
    "may_decide",
    "offer",
    "overlay",
    "overlay_entry",
    "own_slug",
    "post_card",
    "publish",
    "render",
    "rollback",
    "settle",
    "visible_to",
]
