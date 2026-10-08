"""A phone's own address book, synced by the native app (PEOPLE.md).

The app reads the device's contacts and sends them here in batches of up to
``MAX_BATCH``, each with the device's stable id for that contact. The server
answers every batch with a new opaque ``cursor``; the next batch must carry
it. That is how a re-sync sends only what changed: the app diffs against what
it last sent, and the cursor proves both sides agree on what that was.

* **Incremental**: ``cursor`` from the last answer, ``contacts`` added or
  changed since, ``removed`` ids deleted since.
* **Full**: ``full`` true and ``cursor`` null on the first page, then the
  returned cursor on each next page, ``final`` true on the last. Every
  contact on a page is stamped with this sync's generation; on the final
  page, every contact from this device that was not sent goes, by the same
  rule as a contact deleted at Google (kept if another source or Decibyl's
  own history holds it).
* A cursor that does not match -- a second install with the same device id,
  a lost answer, a restored backup -- changes nothing and answers
  ``full_required``: send everything again as a full sync.

Everything is the signed-in person's own: the sync state, the provider ids
and the contacts are all filtered by owner, and a device id is only ever
looked up under its owner, so two people's phones can never meet.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db import db_client
from api.db.people_models import PeopleSyncModel, PersonSourceModel
from api.services.people import store
from api.services.people.normalise import Incoming

PROVIDER = "device"
MAX_BATCH = 2000
MAX_DEVICE_ID = 100
MAX_CONTACT_ID = 150


@dataclass
class DeviceContact:
    id: str
    name: str | None = None
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    company: str | None = None


@dataclass
class Result:
    cursor: str | None
    full_required: bool = False
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    skipped: int = 0


def _sync_key(device_id: str) -> str:
    """The ``people_syncs.provider`` value for one device (16 characters)."""
    return "d:" + hashlib.sha256(device_id.encode()).hexdigest()[:14]


def _external(device_id: str, contact_id: str) -> str:
    return f"{device_id}:{contact_id}"


async def _state(organization_id: int, user_id: int, key: str) -> dict[str, Any] | None:
    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(PeopleSyncModel.cursor).where(
                    PeopleSyncModel.organization_id == organization_id,
                    PeopleSyncModel.user_id == user_id,
                    PeopleSyncModel.provider == key,
                )
            )
        ).scalar_one_or_none()
    if not row:
        return None
    try:
        value = json.loads(row)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def _save(
    organization_id: int,
    user_id: int,
    key: str,
    state: dict[str, Any],
    counts: dict[str, int],
) -> None:
    now = store.now()
    values = {
        "status": "ok",
        "cursor": json.dumps(state),
        "last_error": None,
        "last_synced_at": now,
        "counts": counts,
        "updated_at": now,
    }
    async with db_client.async_session() as session:
        await session.execute(
            pg_insert(PeopleSyncModel)
            .values(
                organization_id=organization_id,
                user_id=user_id,
                provider=key,
                **values,
            )
            .on_conflict_do_update(
                index_elements=["organization_id", "user_id", "provider"],
                set_=values,
            )
        )
        await session.commit()


async def sync(
    organization_id: int,
    user_id: int,
    *,
    device_id: str,
    cursor: str | None,
    full: bool,
    final: bool,
    contacts: list[DeviceContact],
    removed: list[str],
) -> Result:
    key = _sync_key(device_id)
    state = await _state(organization_id, user_id, key)
    expected = (state or {}).get("cursor")
    if full and cursor is None:
        generation = secrets.token_hex(8)  # a new full sync starts here
    elif expected and cursor == expected:
        generation = (state or {}).get("generation") or secrets.token_hex(8)
        if full and not state.get("in_full"):
            # A "next page" of a full sync that never started.
            return Result(cursor=None, full_required=True)
    else:
        return Result(cursor=None, full_required=True)

    result = Result(cursor=None)
    for item in contacts:
        clean = Incoming(
            name=item.name,
            phones=list(item.phones),
            emails=list(item.emails),
            company=item.company,
            external_id=_external(device_id, item.id),
            etag=generation,
        ).clean()
        if clean is None:
            result.skipped += 1
            continue
        done = await store.upsert(
            organization_id, user_id, clean, source="device", provider=PROVIDER
        )
        if done.created:
            result.added += 1
        elif done.changed:
            result.updated += 1
        else:
            result.unchanged += 1
    for contact_id in removed:
        if await store.forget_source(
            organization_id, user_id, PROVIDER, _external(device_id, contact_id)
        ):
            result.removed += 1
    if full and final:
        # Everything this device sent before that this full sync did not.
        async with db_client.async_session() as session:
            stale = (
                (
                    await session.execute(
                        select(PersonSourceModel.external_id).where(
                            PersonSourceModel.organization_id == organization_id,
                            PersonSourceModel.owner_user_id == user_id,
                            PersonSourceModel.provider == PROVIDER,
                            PersonSourceModel.external_id.startswith(
                                f"{device_id}:", autoescape=True
                            ),
                            PersonSourceModel.etag.is_distinct_from(generation),
                        )
                    )
                )
                .scalars()
                .all()
            )
        for external_id in stale:
            if await store.forget_source(
                organization_id, user_id, PROVIDER, external_id
            ):
                result.removed += 1

    result.cursor = secrets.token_urlsafe(18)
    await _save(
        organization_id,
        user_id,
        key,
        {
            "cursor": result.cursor,
            "generation": generation,
            "in_full": bool(full and not final),
        },
        {
            "added": result.added,
            "updated": result.updated,
            "unchanged": result.unchanged,
            "removed": result.removed,
            "skipped": result.skipped,
        },
    )
    return result
