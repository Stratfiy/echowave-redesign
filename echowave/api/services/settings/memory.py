"""The memory manager (screen 16; handoff 8 "Memory").

What a person sees and changes about what Decibyl remembers, in the
workspace they are in:

* **Yours** -- their personal memory (MEM-1, ``organisation_facts.user_id``
  is them). Private to them unless they share it.
* **The workspace's** -- what every member and agent of this workspace
  reads (``user_id`` and ``workflow_id`` NULL).

Every fact carries its provenance (where it came from, when it was first and
last seen, whether a person confirmed it) and its history (each edit, forget,
put-back and share is a row in ``memory_fact_revisions``). An edit names the
value it read; if the fact changed since, it is a conflict carrying the
stored value, never a silent overwrite. Forgetting goes through a card
(``cards.FORGET_MEMORY``). Sharing names its destination and shows exactly
what will be shared before it happens.

Scope is in every query, not checked afterwards: a colleague's personal fact
is not found, the same as another workspace's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update

from api.db import db_client
from api.db.models import OrganisationFactModel, OrganizationMembershipModel
from api.db.settings_models import MemoryFactRevisionModel
from api.services import features
from api.services.knowledge_graph import personal
from api.services.settings import MEMORY_MANAGER

MAX_VALUE = 2000

CREATED = "created"
EDITED = "edited"
CONFIRMED = "confirmed"
FORGOTTEN = "forgotten"
RESTORED = "restored"
SHARED = "shared"


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(MEMORY_MANAGER, organization_id)


class FactNotFound(LookupError):
    pass


class MemoryInvalid(ValueError):
    pass


@dataclass
class Conflict(Exception):
    stored: dict[str, Any]


def _visible(user_id: int):
    """The rows this person may see in a workspace: the workspace's own
    (not a bot's private instruction, not anybody's personal memory) and
    their own personal memory. Never a colleague's."""
    workspace = and_(
        OrganisationFactModel.user_id.is_(None),
        OrganisationFactModel.workflow_id.is_(None),
    )
    return or_(workspace, OrganisationFactModel.user_id == user_id)


def _source(row: Any) -> dict[str, Any]:
    if row.source_run_id is not None:
        kind, line = "call", "Learned on a call"
    elif row.user_id is not None:
        kind, line = "conversation", "From your conversations with Decibyl"
    elif row.status == "learned":
        kind, line = "conversation", "Noticed in a conversation"
    else:
        kind, line = "told", "Told to Decibyl"
    return {
        "kind": kind,
        "line": line,
        "run_id": row.source_run_id,
        "first_seen_at": row.first_seen_at.isoformat() if row.first_seen_at else None,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "times_seen": int(row.times_seen or 0),
    }


def _item(row: Any, revisions: int = 0) -> dict[str, Any]:
    subject = None
    if row.subject_type != "organisation":
        subject = {"kind": row.subject_type, "key": row.subject_key}
    return {
        "id": int(row.id),
        "key": row.key,
        "value": row.value,
        "subject": subject,
        "kind": row.kind,
        "status": row.status,
        "scope": "mine" if row.user_id is not None else "workspace",
        "source": _source(row),
        "saved_at": (
            row.confirmed_at or row.first_seen_at or datetime.now(UTC)
        ).isoformat(),
        "revisions": revisions,
    }


async def _revision_counts(organization_id: int, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    async with db_client.async_session() as session:
        rows = await session.execute(
            select(MemoryFactRevisionModel.fact_id, func.count())
            .where(
                MemoryFactRevisionModel.organization_id == organization_id,
                MemoryFactRevisionModel.fact_id.in_(ids),
            )
            .group_by(MemoryFactRevisionModel.fact_id)
        )
        return {int(fact_id): int(count) for fact_id, count in rows.all()}


async def overview(*, organization_id: int, user_id: int) -> dict[str, Any]:
    """Everything the manager shows above and in the list."""
    from api.services import member_preferences

    stored = await member_preferences.get(user_id)
    async with db_client.async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(OrganisationFactModel)
                    .where(
                        OrganisationFactModel.organization_id == organization_id,
                        OrganisationFactModel.kind == "fact",
                        OrganisationFactModel.status != "rejected",
                        _visible(user_id),
                    )
                    .order_by(
                        OrganisationFactModel.last_seen_at.desc(),
                        OrganisationFactModel.id.desc(),
                    )
                    .limit(500)
                )
            ).scalars()
        )
    counts = await _revision_counts(organization_id, [r.id for r in rows])
    items = [_item(row, counts.get(row.id, 0)) for row in rows]
    return {
        # NULL is "not chosen", which is off (handoff 24).
        "memory_enabled": stored.get("memory_enabled") is True,
        "memory_chosen": stored.get("memory_enabled") is not None,
        "revision": int(stored.get("revision") or 0),
        # With personal memory off, what a person tells Decibyl here is the
        # workspace's; the screen says so rather than showing an empty
        # "Yours" as if nothing were kept.
        "personal_memory": personal.enabled(),
        "mine": [i for i in items if i["scope"] == "mine"],
        "workspace": [i for i in items if i["scope"] == "workspace"],
    }


async def _row(organization_id: int, user_id: int, fact_id: int) -> Any:
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(OrganisationFactModel).where(
                OrganisationFactModel.id == fact_id,
                OrganisationFactModel.organization_id == organization_id,
                _visible(user_id),
            )
        )
    if row is None:
        raise FactNotFound
    return row


async def _record(
    session: Any,
    *,
    organization_id: int,
    fact_id: int,
    actor: int | None,
    change: str,
    before: str | None = None,
    after: str | None = None,
    note: str | None = None,
) -> None:
    session.add(
        MemoryFactRevisionModel(
            organization_id=organization_id,
            fact_id=fact_id,
            actor_user_id=actor,
            change=change,
            before_value=before,
            after_value=after,
            note=(note or None) and note[:255],
            created_at=datetime.now(UTC),
        )
    )


async def detail(*, organization_id: int, user_id: int, fact_id: int) -> dict[str, Any]:
    """One fact with its provenance and every change to it."""
    row = await _row(organization_id, user_id, fact_id)
    async with db_client.async_session() as session:
        revisions = list(
            (
                await session.execute(
                    select(MemoryFactRevisionModel)
                    .where(
                        MemoryFactRevisionModel.organization_id == organization_id,
                        MemoryFactRevisionModel.fact_id == fact_id,
                    )
                    .order_by(MemoryFactRevisionModel.id)
                )
            ).scalars()
        )
    item = _item(row, len(revisions))
    item["history"] = [
        {
            "change": r.change,
            "before": r.before_value,
            "after": r.after_value,
            "note": r.note,
            "at": r.created_at.isoformat(),
            "by_you": r.actor_user_id == user_id,
        }
        for r in revisions
    ]
    return item


async def edit(
    *,
    organization_id: int,
    user_id: int,
    fact_id: int,
    value: str,
    expected_value: str,
) -> dict[str, Any]:
    """Change what a fact says. Compare-and-swap on the value the screen
    read; a person's edit is also their confirmation of it."""
    value = (value or "").strip()
    if not value:
        raise MemoryInvalid("Say what it should be, or forget it instead.")
    if len(value) > MAX_VALUE:
        raise MemoryInvalid(f"A memory can be up to {MAX_VALUE} characters.")
    row = await _row(organization_id, user_id, fact_id)
    if row.value == value:
        return await detail(
            organization_id=organization_id, user_id=user_id, fact_id=fact_id
        )
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        hit = await session.execute(
            update(OrganisationFactModel)
            .where(
                OrganisationFactModel.id == fact_id,
                OrganisationFactModel.organization_id == organization_id,
                OrganisationFactModel.value == expected_value,
                _visible(user_id),
            )
            .values(value=value, status="confirmed", confirmed_at=now, last_seen_at=now)
        )
        if not hit.rowcount:
            await session.rollback()
            raise Conflict(
                stored=await detail(
                    organization_id=organization_id, user_id=user_id, fact_id=fact_id
                )
            )
        await _record(
            session,
            organization_id=organization_id,
            fact_id=fact_id,
            actor=user_id,
            change=EDITED,
            before=expected_value,
            after=value,
        )
        await session.commit()
    return await detail(
        organization_id=organization_id, user_id=user_id, fact_id=fact_id
    )


async def confirm(
    *, organization_id: int, user_id: int, fact_id: int
) -> dict[str, Any]:
    """Keep a suggestion: a learned fact a person says is right."""
    row = await _row(organization_id, user_id, fact_id)
    if row.status != "confirmed":
        async with db_client.async_session() as session:
            await session.execute(
                update(OrganisationFactModel)
                .where(
                    OrganisationFactModel.id == fact_id,
                    OrganisationFactModel.organization_id == organization_id,
                    _visible(user_id),
                )
                .values(status="confirmed", confirmed_at=datetime.now(UTC))
            )
            await _record(
                session,
                organization_id=organization_id,
                fact_id=fact_id,
                actor=user_id,
                change=CONFIRMED,
                after=row.value,
            )
            await session.commit()
    return await detail(
        organization_id=organization_id, user_id=user_id, fact_id=fact_id
    )


async def propose_forget(
    *, organization_id: int, user_id: int, fact_id: int
) -> dict[str, Any]:
    """Raise the card that forgets a fact. Nothing changes until the person
    confirms it; Undo puts it back."""
    from api.services.settings import cards

    row = await _row(organization_id, user_id, fact_id)
    if row.status == "rejected":
        raise MemoryInvalid("That is already forgotten.")
    mine = row.user_id is not None
    return await cards.propose(
        organization_id=organization_id,
        owner_user_id=user_id,
        action=cards.FORGET_MEMORY,
        args={
            "fact_id": int(row.id),
            "title": row.key,
            "was_status": row.status,
        },
        # The label lands in the workspace's audit log: never a personal
        # fact's own words there.
        label="Forget one of your memories" if mine else f"Forget {row.key}",
        effect=(
            "Decibyl stops using it in any answer, here and on every channel. "
            "You can put it back."
        ),
        reversible=True,
    )


async def forget(*, organization_id: int, user_id: int, fact_id: int) -> None:
    """Called when the forget card runs."""
    await _set_status(organization_id, user_id, fact_id, "rejected", FORGOTTEN)


async def restore(
    *, organization_id: int, user_id: int, fact_id: int, status: str
) -> None:
    """Called by Undo on a forget card."""
    if status not in ("learned", "confirmed"):
        status = "confirmed"
    await _set_status(organization_id, user_id, fact_id, status, RESTORED)


async def _set_status(
    organization_id: int, user_id: int, fact_id: int, status: str, change: str
) -> None:
    from api.services.workflow.actions import ActionError

    async with db_client.async_session() as session:
        hit = await session.execute(
            update(OrganisationFactModel)
            .where(
                OrganisationFactModel.id == fact_id,
                OrganisationFactModel.organization_id == organization_id,
                _visible(user_id),
            )
            .values(
                status=status,
                confirmed_at=datetime.now(UTC) if status == "confirmed" else None,
            )
        )
        if not hit.rowcount:
            await session.rollback()
            raise ActionError("That memory is no longer here.")
        await _record(
            session,
            organization_id=organization_id,
            fact_id=fact_id,
            actor=user_id,
            change=change,
        )
        await session.commit()


# --- sharing -----------------------------------------------------------------


async def destinations(*, user_id: int) -> list[dict[str, Any]]:
    """The workspaces this person can share into: every one they belong to
    that is not a personal space."""
    rows = await db_client.list_user_organizations(user_id)
    out = []
    for organization_id, name, _role in rows:
        organization = await db_client.get_organization_by_id(organization_id)
        if getattr(organization, "kind", None) == "personal":
            continue
        out.append(
            {"organization_id": int(organization_id), "name": name or "Workspace"}
        )
    return out


async def _member_count(organization_id: int) -> int:
    async with db_client.async_session() as session:
        return int(
            await session.scalar(
                select(func.count()).where(
                    OrganizationMembershipModel.organization_id == organization_id
                )
            )
            or 0
        )


async def share_preview(
    *, organization_id: int, user_id: int, fact_id: int, destination_id: int
) -> dict[str, Any]:
    """Exactly what sharing would do, before it does it (screen 16: "Share
    requires an explicit destination preview")."""
    row = await _row(organization_id, user_id, fact_id)
    if row.user_id != user_id:
        raise MemoryInvalid("Only your own memories can be shared.")
    if await db_client.get_membership(user_id, destination_id) is None:
        # Not a member: not found, the way a wrong tenant is.
        raise FactNotFound
    organization = await db_client.get_organization_by_id(destination_id)
    if getattr(organization, "kind", None) == "personal":
        raise MemoryInvalid("Choose a team workspace to share with.")
    same = destination_id == organization_id
    members = await _member_count(destination_id)
    name = getattr(organization, "name", None) or "the workspace"
    return {
        "fact_id": int(row.id),
        "key": row.key,
        "value": row.value,
        "destination": {"organization_id": destination_id, "name": name},
        "members": members,
        "moves": same,
        "lines": [
            (
                f"Everyone in {name} ({members} {'person' if members == 1 else 'people'}) "
                "and every agent there will be able to use it."
            ),
            (
                "It leaves your personal memory and becomes the workspace's."
                if same
                else "Your own copy stays private in your memory."
            ),
            "Only this one memory is shared, exactly as shown.",
        ],
    }


async def share(
    *,
    organization_id: int,
    user_id: int,
    fact_id: int,
    destination_id: int,
    expected_value: str,
) -> dict[str, Any]:
    """Share one of the person's own facts to a workspace they belong to,
    exactly as the preview showed it."""
    preview = await share_preview(
        organization_id=organization_id,
        user_id=user_id,
        fact_id=fact_id,
        destination_id=destination_id,
    )
    if preview["value"] != expected_value:
        raise Conflict(
            stored=await detail(
                organization_id=organization_id, user_id=user_id, fact_id=fact_id
            )
        )
    row = await _row(organization_id, user_id, fact_id)
    name = preview["destination"]["name"]
    if preview["moves"]:
        shared = await db_client.share_member_fact(
            organization_id=organization_id, user_id=user_id, fact_id=fact_id
        )
        if shared is None:
            raise FactNotFound
        new_id = int(shared.id)
        async with db_client.async_session() as session:
            # The history follows the fact to its new row.
            await session.execute(
                update(MemoryFactRevisionModel)
                .where(
                    MemoryFactRevisionModel.organization_id == organization_id,
                    MemoryFactRevisionModel.fact_id == fact_id,
                )
                .values(fact_id=new_id)
            )
            await _record(
                session,
                organization_id=organization_id,
                fact_id=new_id,
                actor=user_id,
                change=SHARED,
                after=row.value,
                note=f"Shared to {name}",
            )
            await session.commit()
        return {"shared_fact_id": new_id, "destination": preview["destination"]}

    await db_client.remember_organisation_facts(
        organization_id=destination_id,
        facts={row.key: row.value},
        status="confirmed",
        subject_type=row.subject_type,
        subject_key=row.subject_key,
    )
    async with db_client.async_session() as session:
        target = await session.scalar(
            select(OrganisationFactModel).where(
                OrganisationFactModel.organization_id == destination_id,
                OrganisationFactModel.subject_type == row.subject_type,
                OrganisationFactModel.subject_key == row.subject_key,
                OrganisationFactModel.key == row.key,
                OrganisationFactModel.user_id.is_(None),
                OrganisationFactModel.workflow_id.is_(None),
            )
        )
        if target is None:
            raise FactNotFound
        await _record(
            session,
            organization_id=destination_id,
            fact_id=int(target.id),
            actor=user_id,
            change=SHARED,
            after=row.value,
            note="Shared by a member from their personal memory",
        )
        await _record(
            session,
            organization_id=organization_id,
            fact_id=fact_id,
            actor=user_id,
            change=SHARED,
            after=row.value,
            note=f"Shared to {name}",
        )
        await session.commit()
    return {"shared_fact_id": int(target.id), "destination": preview["destination"]}
