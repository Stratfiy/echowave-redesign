"""Trackers: a list a person asked Decibyl to build ("track my client
visits"), from the describe-it builder.

Creating one is a card (``create_tracker``): the person sees its name and
columns and confirms. Adding a row is organising, not sending -- like
saving a prospect -- so it runs in the turn. Private to the person who
made it until they share it with the workspace.
"""

from __future__ import annotations

import re
import uuid as uuid_lib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from api.db import db_client
from api.db.agents_models import TrackerEntryModel, TrackerModel
from api.services import features
from api.services.helpers import sharing

FLAG = "describe_builder"
TYPES = ("text", "number", "date", "money", "yes_no")
MAX_COLUMNS = 12
MAX_ENTRIES_SHOWN = 50

Invalid = sharing.Invalid


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


def clean_spec(raw: dict[str, Any]) -> dict[str, Any]:
    name = re.sub(r"\s+", " ", str(raw.get("name") or "")).strip()[:120]
    if not name:
        raise Invalid("Name the tracker.")
    columns: list[dict[str, str]] = []
    seen: set[str] = set()
    for col in raw.get("columns") or []:
        if isinstance(col, str):
            col = {"name": col}
        if not isinstance(col, dict):
            continue
        cname = re.sub(r"\s+", " ", str(col.get("name") or "")).strip()[:60]
        ctype = str(col.get("type") or "text").strip().lower()
        if not cname or cname.lower() in seen:
            continue
        if ctype not in TYPES:
            ctype = "text"
        seen.add(cname.lower())
        columns.append({"name": cname, "type": ctype})
    if not columns:
        raise Invalid("Say what each row records, such as date and client.")
    if len(columns) > MAX_COLUMNS:
        raise Invalid(f"Keep it to {MAX_COLUMNS} columns.")
    return {"name": name, "columns": columns}


def label(spec: dict[str, Any]) -> str:
    cols = ", ".join(c["name"] for c in spec["columns"])
    return f"Create tracker {spec['name']} ({cols})"[:200]


async def create(
    spec: dict[str, Any],
    *,
    organization_id: int,
    user_id: int,
    visibility: str = sharing.PRIVATE,
) -> TrackerModel:
    row = TrackerModel(
        uuid=str(uuid_lib.uuid4()),
        organization_id=organization_id,
        owner_user_id=user_id,
        visibility=sharing.clean(visibility),
        name=spec["name"],
        columns=spec["columns"],
        created_at=datetime.now(UTC),
    )
    async with db_client.async_session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def list_visible(*, organization_id: int, user_id: int) -> list[TrackerModel]:
    async with db_client.async_session() as session:
        rows = await session.scalars(
            select(TrackerModel)
            .where(
                *sharing.visible_to(
                    TrackerModel, organization_id=organization_id, user_id=user_id
                )
            )
            .order_by(TrackerModel.created_at.desc())
        )
        return list(rows.all())


async def find_visible(
    *,
    organization_id: int,
    user_id: int,
    name: str | None = None,
    uuid: str | None = None,
) -> TrackerModel | None:
    query = select(TrackerModel).where(
        *sharing.visible_to(
            TrackerModel, organization_id=organization_id, user_id=user_id
        )
    )
    if uuid:
        query = query.where(TrackerModel.uuid == uuid)
    elif name:
        query = query.where(func.lower(TrackerModel.name) == name.strip().lower())
    else:
        return None
    async with db_client.async_session() as session:
        return await session.scalar(query.limit(1))


def clean_values(tracker: TrackerModel, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise Invalid("Give the row as column: value.")
    by_name = {c["name"].lower(): c for c in tracker.columns or []}
    out: dict[str, Any] = {}
    unknown = []
    for key, value in raw.items():
        col = by_name.get(str(key).strip().lower())
        if col is None:
            unknown.append(str(key))
            continue
        out[col["name"]] = str(value)[:500] if value is not None else None
    if unknown:
        # Said, not dropped: a value with nowhere to go is a question.
        raise Invalid(
            f"{tracker.name} has no column {', '.join(unknown)}. Its columns are "
            + ", ".join(c["name"] for c in tracker.columns or [])
            + "."
        )
    if not any(v not in (None, "") for v in out.values()):
        raise Invalid("The row is empty.")
    return out


async def add_entry(
    tracker: TrackerModel, values: dict[str, Any], *, user_id: int | None
) -> TrackerEntryModel:
    row = TrackerEntryModel(
        tracker_id=tracker.id,
        organization_id=tracker.organization_id,
        values=values,
        created_by=user_id,
        created_at=datetime.now(UTC),
    )
    async with db_client.async_session() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


async def entries(tracker: TrackerModel, limit: int = MAX_ENTRIES_SHOWN) -> list[dict]:
    async with db_client.async_session() as session:
        rows = await session.scalars(
            select(TrackerEntryModel)
            .where(
                TrackerEntryModel.tracker_id == tracker.id,
                TrackerEntryModel.organization_id == tracker.organization_id,
            )
            .order_by(TrackerEntryModel.created_at.desc(), TrackerEntryModel.id.desc())
            .limit(limit)
        )
        return [
            {"id": r.id, "values": r.values, "at": r.created_at.isoformat()}
            for r in rows.all()
        ]


def describe(row: TrackerModel, *, user_id: int) -> dict[str, Any]:
    return {
        "uuid": row.uuid,
        "name": row.name,
        "columns": row.columns or [],
        "visibility": row.visibility,
        "mine": row.owner_user_id == user_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
