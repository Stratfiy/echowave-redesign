"""Saved items and search, one scope at a time (screen 15).

A saved item is something a person kept -- a reply, a note, a file -- held
in one scope: a workspace or their personal space. Search runs in exactly
one scope, which the screen states:

* ``personal`` -- the person's own private items in the workspaces they are
  still in, their personal memory here, and everything in their personal
  space;
* ``workspace`` -- this workspace's shared items and its shared memory.

Never another person's private item or memory, not even as a hint: the
filter is in the query, and a title that does not match the scope is never
read. Deleting goes through a card and is reversible; it removes the saved
copy only, never the conversation it came from.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, or_, select, update

from api.db import db_client
from api.db.models import (
    OrganisationFactModel,
    OrganizationMembershipModel,
    OrganizationModel,
)
from api.db.settings_models import SavedItemModel
from api.services import features
from api.services.settings import SAVED_ITEMS

KINDS = ("reply", "note", "file", "link")
VISIBILITIES = ("private", "workspace")
SCOPES = ("personal", "workspace")
MAX_TITLE = 200
MAX_BODY = 20_000
MAX_RESULTS = 50


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(SAVED_ITEMS, organization_id)


class SavedNotFound(LookupError):
    pass


class SavedInvalid(ValueError):
    pass


def _view(row: SavedItemModel, user_id: int) -> dict[str, Any]:
    href = None
    if row.thread_id or row.source_event_id:
        href = "/overview" + (f"?thread={row.thread_id}" if row.thread_id else "")
    return {
        "id": int(row.id),
        "organization_id": int(row.organization_id),
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "visibility": row.visibility,
        "status": row.status,
        "mine": row.owner_user_id == user_id,
        "source_event_id": row.source_event_id,
        "thread_id": row.thread_id,
        "conversation_href": href,
        "file": row.file_ref,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


async def _member_orgs(user_id: int) -> list[int]:
    async with db_client.async_session() as session:
        rows = await session.execute(
            select(OrganizationMembershipModel.organization_id).where(
                OrganizationMembershipModel.user_id == user_id
            )
        )
        return [int(r[0]) for r in rows.all()]


async def _personal_space_id(user_id: int) -> int | None:
    async with db_client.async_session() as session:
        found = await session.scalar(
            select(OrganizationModel.id).where(
                OrganizationModel.personal_owner_user_id == user_id,
                OrganizationModel.kind == "personal",
            )
        )
        return int(found) if found else None


def _readable(user_id: int, organization_id: int, member_orgs: list[int]):
    """Items this person may see: their own anywhere they are still a
    member, and the shared ones of the workspace they are in."""
    return or_(
        and_(
            SavedItemModel.owner_user_id == user_id,
            SavedItemModel.organization_id.in_(member_orgs or [-1]),
        ),
        and_(
            SavedItemModel.organization_id == organization_id,
            SavedItemModel.visibility == "workspace",
        ),
    )


async def save(
    *,
    organization_id: int,
    user_id: int,
    title: str,
    kind: str = "reply",
    body: str | None = None,
    visibility: str = "private",
    source_event_id: int | None = None,
    thread_id: str | None = None,
    file_ref: dict[str, Any] | None = None,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise SavedInvalid("Give it a name.")
    if len(title) > MAX_TITLE:
        raise SavedInvalid(f"A name can be up to {MAX_TITLE} characters.")
    if kind not in KINDS:
        raise SavedInvalid("That is not something that can be saved.")
    if visibility not in VISIBILITIES:
        raise SavedInvalid("Keep it to yourself or share it with the workspace.")
    if body and len(body) > MAX_BODY:
        raise SavedInvalid("That is too long to save.")
    if source_event_id is not None:
        # The line must be in this workspace: an id from another one is not
        # found, and is never stored as if it were.
        event = await db_client.get_agent_event(
            int(source_event_id), organization_id=organization_id
        )
        if event is None:
            raise SavedNotFound
        thread_id = thread_id or event.thread_id
        if not body:
            body = str((event.payload or {}).get("body") or event.summary or "")[
                :MAX_BODY
            ]
    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        row = SavedItemModel(
            organization_id=organization_id,
            owner_user_id=user_id,
            visibility=visibility,
            kind=kind,
            title=title,
            body=body,
            source_event_id=source_event_id,
            thread_id=thread_id,
            file_ref=file_ref,
            status="ready",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return _view(row, user_id)


async def list_items(
    *, organization_id: int, user_id: int, scope: str
) -> list[dict[str, Any]]:
    if scope not in SCOPES:
        raise SavedInvalid("Search in your own things or the workspace's.")
    member_orgs = await _member_orgs(user_id)
    async with db_client.async_session() as session:
        query = select(SavedItemModel).where(SavedItemModel.status != "deleted")
        if scope == "personal":
            query = query.where(
                SavedItemModel.owner_user_id == user_id,
                SavedItemModel.visibility == "private",
                SavedItemModel.organization_id.in_(member_orgs or [-1]),
            )
        else:
            query = query.where(
                SavedItemModel.organization_id == organization_id,
                SavedItemModel.visibility == "workspace",
            )
        rows = (
            await session.execute(
                query.order_by(SavedItemModel.updated_at.desc()).limit(200)
            )
        ).scalars()
        return [_view(row, user_id) for row in rows]


async def _row(
    organization_id: int, user_id: int, item_id: int, *, own: bool
) -> SavedItemModel:
    member_orgs = await _member_orgs(user_id)
    async with db_client.async_session() as session:
        query = select(SavedItemModel).where(
            SavedItemModel.id == item_id,
            _readable(user_id, organization_id, member_orgs),
        )
        if own:
            query = query.where(SavedItemModel.owner_user_id == user_id)
        row = await session.scalar(query)
    if row is None:
        raise SavedNotFound
    return row


async def get(*, organization_id: int, user_id: int, item_id: int) -> dict[str, Any]:
    row = await _row(organization_id, user_id, item_id, own=False)
    if row.status == "deleted" and row.owner_user_id != user_id:
        raise SavedNotFound
    return _view(row, user_id)


async def rename(
    *, organization_id: int, user_id: int, item_id: int, title: str
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title or len(title) > MAX_TITLE:
        raise SavedInvalid(f"A name is 1 to {MAX_TITLE} characters.")
    row = await _row(organization_id, user_id, item_id, own=True)
    async with db_client.async_session() as session:
        await session.execute(
            update(SavedItemModel)
            .where(SavedItemModel.id == row.id, SavedItemModel.owner_user_id == user_id)
            .values(title=title, updated_at=datetime.now(UTC))
        )
        await session.commit()
    return await get(organization_id=organization_id, user_id=user_id, item_id=item_id)


def deletion_effects(row: dict[str, Any]) -> list[str]:
    """What deleting it does, and what it does not (screen 15: "Deletion
    describes linked tasks and provenance effects")."""
    lines = ["Only this saved copy is deleted."]
    if row.get("conversation_href"):
        lines.append("The conversation it came from stays as it is.")
    if row.get("visibility") == "workspace":
        lines.append("It also disappears for everyone in the workspace.")
    lines.append("No task or routine uses saved items, so nothing else changes.")
    lines.append("You can put it back from the card.")
    return lines


async def propose_delete(
    *, organization_id: int, user_id: int, item_id: int
) -> dict[str, Any]:
    from api.services.settings import cards

    row = await _row(organization_id, user_id, item_id, own=True)
    if row.status == "deleted":
        raise SavedInvalid("That is already deleted.")
    view = _view(row, user_id)
    return await cards.propose(
        organization_id=int(row.organization_id),
        owner_user_id=user_id,
        action=cards.DELETE_SAVED_ITEM,
        args={"item_id": int(row.id), "title": row.title},
        label="Delete a saved item",
        effect=" ".join(deletion_effects(view)),
        reversible=True,
    )


async def _set_status(
    organization_id: int, user_id: int, item_id: int, status: str
) -> None:
    from api.services.workflow.actions import ActionError

    now = datetime.now(UTC)
    async with db_client.async_session() as session:
        hit = await session.execute(
            update(SavedItemModel)
            .where(
                SavedItemModel.id == item_id,
                SavedItemModel.organization_id == organization_id,
                SavedItemModel.owner_user_id == user_id,
            )
            .values(
                status=status,
                deleted_at=now if status == "deleted" else None,
                updated_at=now,
            )
        )
        if not hit.rowcount:
            await session.rollback()
            raise ActionError("That saved item is no longer here.")
        await session.commit()


async def mark_deleted(*, organization_id: int, user_id: int, item_id: int) -> None:
    await _set_status(organization_id, user_id, item_id, "deleted")


async def restore(*, organization_id: int, user_id: int, item_id: int) -> None:
    await _set_status(organization_id, user_id, item_id, "ready")


# --- search ------------------------------------------------------------------


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search(
    *, organization_id: int, user_id: int, scope: str, query: str
) -> dict[str, Any]:
    """Saved items and memories matching ``query`` in one scope."""
    if scope not in SCOPES:
        raise SavedInvalid("Search in your own things or the workspace's.")
    query = (query or "").strip()[:200]
    if not query:
        return {"scope": scope, "query": "", "results": []}
    pattern = _like(query)
    member_orgs = await _member_orgs(user_id)
    personal_space = await _personal_space_id(user_id) if scope == "personal" else None
    results: list[dict[str, Any]] = []
    async with db_client.async_session() as session:
        items = select(SavedItemModel).where(
            SavedItemModel.status != "deleted",
            or_(
                SavedItemModel.title.ilike(pattern, escape="\\"),
                SavedItemModel.body.ilike(pattern, escape="\\"),
            ),
        )
        if scope == "personal":
            items = items.where(
                SavedItemModel.owner_user_id == user_id,
                SavedItemModel.visibility == "private",
                SavedItemModel.organization_id.in_(member_orgs or [-1]),
            )
        else:
            items = items.where(
                SavedItemModel.organization_id == organization_id,
                SavedItemModel.visibility == "workspace",
            )
        for row in (
            await session.execute(
                items.order_by(SavedItemModel.updated_at.desc()).limit(MAX_RESULTS)
            )
        ).scalars():
            results.append(
                {
                    "type": "saved",
                    "id": int(row.id),
                    "title": row.title,
                    "snippet": (row.body or "")[:160],
                    "kind": row.kind,
                    "href": f"/settings/saved?item={row.id}",
                }
            )

        facts = select(OrganisationFactModel).where(
            OrganisationFactModel.kind == "fact",
            OrganisationFactModel.status != "rejected",
            or_(
                OrganisationFactModel.key.ilike(pattern, escape="\\"),
                OrganisationFactModel.value.ilike(pattern, escape="\\"),
            ),
        )
        if scope == "personal":
            mine_here = and_(
                OrganisationFactModel.organization_id == organization_id,
                OrganisationFactModel.user_id == user_id,
            )
            facts = facts.where(
                or_(
                    mine_here,
                    and_(
                        OrganisationFactModel.organization_id == (personal_space or -1),
                        OrganisationFactModel.workflow_id.is_(None),
                    ),
                )
            )
        else:
            facts = facts.where(
                OrganisationFactModel.organization_id == organization_id,
                OrganisationFactModel.user_id.is_(None),
                OrganisationFactModel.workflow_id.is_(None),
            )
        for row in (
            await session.execute(
                facts.order_by(OrganisationFactModel.last_seen_at.desc()).limit(
                    MAX_RESULTS
                )
            )
        ).scalars():
            results.append(
                {
                    "type": "memory",
                    "id": int(row.id),
                    "title": row.key,
                    "snippet": (row.value or "")[:160],
                    "kind": "memory",
                    "href": f"/settings/memory?fact={row.id}",
                }
            )
    return {"scope": scope, "query": query, "results": results}
