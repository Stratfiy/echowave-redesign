"""Contacts in and out of the database, always as one owner's.

Every function takes ``organization_id`` and ``owner_user_id`` and puts both
in its query. There is no function here that lists contacts across owners:
the only cross-owner read is ``shared_with``, which returns what an owner
chose to show one colleague, and only the card fields.

**Same contact or a duplicate?** An incoming contact is the *same* as a
stored one when the source says so (its provider id is on file) or when the
name matches and they share a number or address -- re-importing a vCard
twice changes nothing. A different name sharing a number or address is a
*possible duplicate*: both are kept and a merge is suggested for the owner
to decide. Nothing is ever merged silently.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api.db import db_client
from api.db.people_models import (
    PeopleSettingsModel,
    PersonHandleModel,
    PersonInteractionModel,
    PersonMergeModel,
    PersonModel,
    PersonShareModel,
    PersonSourceModel,
)
from api.services.people.normalise import Incoming, name_key

#: How many interactions a person page and a brief read.
RECENT = 8
SOURCES = ("google", "microsoft", "vcard", "csv", "picker", "manual", "decibyl")


def now() -> datetime:
    return datetime.now(UTC)


class NotFound(LookupError):
    """No such contact for this owner -- a colleague's is answered the same."""


@dataclass
class Upserted:
    person: PersonModel
    created: bool
    changed: bool


# --- reading ---------------------------------------------------------------


def _owner(model, organization_id: int, owner_user_id: int):
    return and_(
        model.organization_id == organization_id,
        model.owner_user_id == owner_user_id,
    )


async def get(organization_id: int, owner_user_id: int, uuid: str) -> PersonModel:
    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(PersonModel).where(
                    _owner(PersonModel, organization_id, owner_user_id),
                    PersonModel.uuid == uuid,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise NotFound(uuid)
    return row


async def listing(
    organization_id: int,
    owner_user_id: int,
    *,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PersonModel], int]:
    """The owner's contacts, most recently in touch first, then by name."""
    where = [_owner(PersonModel, organization_id, owner_user_id)]
    q = (query or "").strip()
    if q:
        like = f"%{q.lower()}%"
        digits = "".join(ch for ch in q if ch.isdigit())
        handle = select(PersonHandleModel.person_id).where(
            _owner(PersonHandleModel, organization_id, owner_user_id),
            PersonHandleModel.value.like(f"%{digits}%" if len(digits) >= 4 else like),
        )
        where.append(
            or_(
                func.lower(PersonModel.name).like(like),
                func.lower(func.coalesce(PersonModel.company, "")).like(like),
                PersonModel.id.in_(handle),
            )
        )
    async with db_client.async_session() as session:
        total = (
            await session.execute(select(func.count(PersonModel.id)).where(*where))
        ).scalar_one()
        rows = (
            (
                await session.execute(
                    select(PersonModel)
                    .where(*where)
                    .order_by(
                        PersonModel.last_interaction_at.desc().nulls_last(),
                        func.lower(PersonModel.name),
                        PersonModel.id,
                    )
                    .limit(max(1, min(limit, 200)))
                    .offset(max(0, offset))
                )
            )
            .scalars()
            .all()
        )
    return list(rows), int(total)


async def count(organization_id: int, owner_user_id: int) -> int:
    async with db_client.async_session() as session:
        return int(
            (
                await session.execute(
                    select(func.count(PersonModel.id)).where(
                        _owner(PersonModel, organization_id, owner_user_id)
                    )
                )
            ).scalar_one()
        )


async def interactions(
    person: PersonModel, *, limit: int = RECENT
) -> list[PersonInteractionModel]:
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    select(PersonInteractionModel)
                    .where(
                        PersonInteractionModel.person_id == person.id,
                        _owner(
                            PersonInteractionModel,
                            person.organization_id,
                            person.owner_user_id,
                        ),
                    )
                    .order_by(
                        PersonInteractionModel.at.desc(),
                        PersonInteractionModel.id.desc(),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def find_by_handle(
    organization_id: int, owner_user_id: int, kind: str, value: str
) -> PersonModel | None:
    """The owner's contact with this number or address; the oldest when a
    duplicate is still waiting for its merge."""
    async with db_client.async_session() as session:
        return (
            await session.execute(
                select(PersonModel)
                .join(PersonHandleModel, PersonHandleModel.person_id == PersonModel.id)
                .where(
                    _owner(PersonHandleModel, organization_id, owner_user_id),
                    PersonHandleModel.kind == kind,
                    PersonHandleModel.value == value,
                )
                .order_by(PersonModel.id)
                .limit(1)
            )
        ).scalar_one_or_none()


async def find_by_name(
    organization_id: int, owner_user_id: int, name: str, *, limit: int = 5
) -> list[PersonModel]:
    """Contacts whose name is this, starts with it, or has it as a word."""
    key = name_key(name)
    if len(key) < 2:
        return []
    async with db_client.async_session() as session:
        rows = (
            (
                await session.execute(
                    select(PersonModel)
                    .where(
                        _owner(PersonModel, organization_id, owner_user_id),
                        or_(
                            func.lower(PersonModel.name) == key,
                            func.lower(PersonModel.name).like(f"{key}%"),
                            func.lower(PersonModel.name).like(f"% {key}%"),
                        ),
                    )
                    .order_by(
                        (func.lower(PersonModel.name) == key).desc(),
                        PersonModel.last_interaction_at.desc().nulls_last(),
                        PersonModel.id,
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


# --- writing ---------------------------------------------------------------


async def _set_handles(session, person: PersonModel) -> None:
    await session.execute(
        delete(PersonHandleModel).where(PersonHandleModel.person_id == person.id)
    )
    for kind, values in (
        ("phone", person.phones or []),
        ("email", person.emails or []),
    ):
        for value in values:
            session.add(
                PersonHandleModel(
                    person_id=person.id,
                    organization_id=person.organization_id,
                    owner_user_id=person.owner_user_id,
                    kind=kind,
                    value=value,
                )
            )


async def _suggest_merges(session, person: PersonModel) -> int:
    """Open a merge for every other contact of this owner that shares a
    number or address with ``person``. A pair decided once stays decided."""
    handles = [("phone", v) for v in person.phones or []] + [
        ("email", v) for v in person.emails or []
    ]
    if not handles:
        return 0
    others = (
        await session.execute(
            select(
                PersonHandleModel.person_id,
                PersonHandleModel.kind,
                PersonHandleModel.value,
            ).where(
                _owner(PersonHandleModel, person.organization_id, person.owner_user_id),
                PersonHandleModel.person_id != person.id,
                or_(
                    *(
                        and_(PersonHandleModel.kind == k, PersonHandleModel.value == v)
                        for k, v in handles
                    )
                ),
            )
        )
    ).all()
    opened = 0
    seen: set[int] = set()
    for other_id, kind, value in others:
        if other_id in seen:
            continue
        seen.add(other_id)
        keep, other = sorted((person.id, other_id))
        result = await session.execute(
            pg_insert(PersonMergeModel)
            .values(
                uuid=str(uuid4()),
                organization_id=person.organization_id,
                owner_user_id=person.owner_user_id,
                keep_id=keep,
                other_id=other,
                reason=kind,
                value=value,
                status="open",
                created_at=now(),
            )
            .on_conflict_do_nothing(
                index_elements=["owner_user_id", "keep_id", "other_id"]
            )
        )
        opened += result.rowcount or 0
    return opened


def _add_source(person: PersonModel, source: str) -> bool:
    sources = list(person.sources or [])
    if source in sources:
        return False
    person.sources = [*sources, source]
    return True


def _union(existing: Iterable[str], incoming: Iterable[str]) -> list[str]:
    return list(dict.fromkeys([*(existing or []), *(incoming or [])]))[:20]


async def upsert(
    organization_id: int,
    owner_user_id: int,
    contact: Incoming,
    *,
    source: str,
    provider: str | None = None,
) -> Upserted:
    """Store one cleaned contact for its owner. See the module docstring for
    when it updates and when it adds a second contact and a merge."""
    assert source in SOURCES, source
    async with db_client.async_session() as session:
        person: PersonModel | None = None
        if provider and contact.external_id:
            person = (
                await session.execute(
                    select(PersonModel)
                    .join(
                        PersonSourceModel, PersonSourceModel.person_id == PersonModel.id
                    )
                    .where(
                        _owner(PersonSourceModel, organization_id, owner_user_id),
                        PersonSourceModel.provider == provider,
                        PersonSourceModel.external_id == contact.external_id,
                    )
                )
            ).scalar_one_or_none()
        if person is None:
            handles = [("phone", v) for v in contact.phones] + [
                ("email", v) for v in contact.emails
            ]
            if handles:
                candidates = (
                    (
                        await session.execute(
                            select(PersonModel)
                            .join(
                                PersonHandleModel,
                                PersonHandleModel.person_id == PersonModel.id,
                            )
                            .where(
                                _owner(
                                    PersonHandleModel, organization_id, owner_user_id
                                ),
                                or_(
                                    *(
                                        and_(
                                            PersonHandleModel.kind == k,
                                            PersonHandleModel.value == v,
                                        )
                                        for k, v in handles
                                    )
                                ),
                            )
                            .order_by(PersonModel.id)
                        )
                    )
                    .scalars()
                    .unique()
                    .all()
                )
                same = name_key(contact.name)
                person = next((c for c in candidates if name_key(c.name) == same), None)
        created = person is None
        changed = created
        if person is None:
            person = PersonModel(
                organization_id=organization_id,
                owner_user_id=owner_user_id,
                name=contact.name,
                phones=list(contact.phones),
                emails=list(contact.emails),
                company=contact.company,
                relation=contact.relation,
                sources=[source],
            )
            session.add(person)
            await session.flush()
        else:
            phones = _union(person.phones, contact.phones)
            emails = _union(person.emails, contact.emails)
            if phones != list(person.phones or []) or emails != list(
                person.emails or []
            ):
                person.phones, person.emails, changed = phones, emails, True
            # A provider's rename is the person's own edit in their address
            # book; follow it when that provider is where the contact lives.
            if (
                provider
                and contact.name
                and contact.name != person.name
                and (list(person.sources or []) == [provider])
            ):
                person.name, changed = contact.name, True
            if contact.company and not person.company:
                person.company, changed = contact.company, True
            if contact.relation and not person.relation:
                person.relation, changed = contact.relation, True
            changed = _add_source(person, source) or changed
        if provider and contact.external_id:
            await session.execute(
                pg_insert(PersonSourceModel)
                .values(
                    person_id=person.id,
                    organization_id=organization_id,
                    owner_user_id=owner_user_id,
                    provider=provider,
                    external_id=contact.external_id,
                    etag=contact.etag,
                    updated_at=now(),
                )
                .on_conflict_do_update(
                    index_elements=[
                        "organization_id",
                        "owner_user_id",
                        "provider",
                        "external_id",
                    ],
                    set_={
                        "person_id": person.id,
                        "etag": contact.etag,
                        "updated_at": now(),
                    },
                )
            )
        if changed:
            await _set_handles(session, person)
            await session.flush()
            await _suggest_merges(session, person)
        await session.commit()
        await session.refresh(person)
    return Upserted(person=person, created=created, changed=changed)


async def attach(
    person: PersonModel, *, phones: list[str], emails: list[str], source: str
) -> PersonModel:
    """Add numbers or addresses to one known contact (already normalised)."""
    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(PersonModel).where(
                    PersonModel.id == person.id,
                    _owner(PersonModel, person.organization_id, person.owner_user_id),
                )
            )
        ).scalar_one()
        row.phones = _union(row.phones, phones)
        row.emails = _union(row.emails, emails)
        _add_source(row, source)
        await _set_handles(session, row)
        await session.flush()
        await _suggest_merges(session, row)
        await session.commit()
        await session.refresh(row)
    return row


async def forget_source(
    organization_id: int, owner_user_id: int, provider: str, external_id: str
) -> bool:
    """A contact deleted at the provider. The contact stays if it has
    another source or anything Decibyl recorded; otherwise it goes."""
    async with db_client.async_session() as session:
        link = (
            await session.execute(
                select(PersonSourceModel).where(
                    _owner(PersonSourceModel, organization_id, owner_user_id),
                    PersonSourceModel.provider == provider,
                    PersonSourceModel.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if link is None:
            return False
        person = await session.get(PersonModel, link.person_id)
        await session.delete(link)
        if person is not None:
            remaining = (
                await session.execute(
                    select(func.count(PersonSourceModel.id)).where(
                        PersonSourceModel.person_id == person.id,
                        PersonSourceModel.provider == provider,
                    )
                )
            ).scalar_one()
            if not remaining:
                sources = [s for s in person.sources or [] if s != provider]
                has_history = (
                    await session.execute(
                        select(func.count(PersonInteractionModel.id)).where(
                            PersonInteractionModel.person_id == person.id
                        )
                    )
                ).scalar_one()
                if not sources and not has_history and person.brief_by != "you":
                    await session.delete(person)
                else:
                    person.sources = sources or ["decibyl"]
        await session.commit()
    return True


async def create_manual(
    organization_id: int, owner_user_id: int, contact: Incoming
) -> Upserted:
    return await upsert(organization_id, owner_user_id, contact, source="manual")


async def edit(
    organization_id: int, owner_user_id: int, uuid: str, changes: dict[str, Any]
) -> PersonModel:
    """The owner's own edits: name, company, relation, numbers, addresses
    and the brief. An edited brief is theirs (``brief_by`` = ``you``)."""
    async with db_client.async_session() as session:
        person = (
            await session.execute(
                select(PersonModel).where(
                    _owner(PersonModel, organization_id, owner_user_id),
                    PersonModel.uuid == uuid,
                )
            )
        ).scalar_one_or_none()
        if person is None:
            raise NotFound(uuid)
        handles_changed = False
        for key in ("name", "company", "relation"):
            if key in changes:
                setattr(
                    person,
                    key,
                    changes[key] or (person.name if key == "name" else None),
                )
        if "phones" in changes:
            person.phones, handles_changed = list(changes["phones"]), True
        if "emails" in changes:
            person.emails, handles_changed = list(changes["emails"]), True
        if "brief" in changes:
            person.brief = changes["brief"] or None
            person.brief_by = "you" if person.brief else None
            person.brief_at = now()
            person.brief_due_at = None
        if handles_changed:
            await _set_handles(session, person)
            await session.flush()
            await _suggest_merges(session, person)
        await session.commit()
        await session.refresh(person)
    return person


async def remove(organization_id: int, owner_user_id: int, uuid: str) -> None:
    async with db_client.async_session() as session:
        result = await session.execute(
            delete(PersonModel).where(
                _owner(PersonModel, organization_id, owner_user_id),
                PersonModel.uuid == uuid,
            )
        )
        await session.commit()
    if not result.rowcount:
        raise NotFound(uuid)


# --- merges ------------------------------------------------------------------


async def open_merges(organization_id: int, owner_user_id: int) -> list[dict[str, Any]]:
    """Each open suggestion with both contacts as the owner sees them."""
    async with db_client.async_session() as session:
        merges = (
            (
                await session.execute(
                    select(PersonMergeModel)
                    .where(
                        _owner(PersonMergeModel, organization_id, owner_user_id),
                        PersonMergeModel.status == "open",
                    )
                    .order_by(PersonMergeModel.id)
                    .limit(100)
                )
            )
            .scalars()
            .all()
        )
        ids = {m.keep_id for m in merges} | {m.other_id for m in merges}
        people = {
            p.id: p
            for p in (
                await session.execute(
                    select(PersonModel).where(
                        _owner(PersonModel, organization_id, owner_user_id),
                        PersonModel.id.in_(ids or {0}),
                    )
                )
            )
            .scalars()
            .all()
        }
    return [
        {"merge": m, "keep": people[m.keep_id], "other": people[m.other_id]}
        for m in merges
        if m.keep_id in people and m.other_id in people
    ]


async def decide_merge(
    organization_id: int, owner_user_id: int, uuid: str, *, merge: bool
) -> PersonModel | None:
    """Merge the pair into the older contact, or keep both. Returns the
    contact that remains when merged."""
    async with db_client.async_session() as session:
        row = (
            await session.execute(
                select(PersonMergeModel).where(
                    _owner(PersonMergeModel, organization_id, owner_user_id),
                    PersonMergeModel.uuid == uuid,
                    PersonMergeModel.status == "open",
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFound(uuid)
        row.decided_at = now()
        if not merge:
            row.status = "dismissed"
            await session.commit()
            return None
        keep = await session.get(PersonModel, row.keep_id)
        other = await session.get(PersonModel, row.other_id)
        if keep is None or other is None:
            raise NotFound(uuid)
        keep.phones = _union(keep.phones, other.phones)
        keep.emails = _union(keep.emails, other.emails)
        keep.sources = _union(keep.sources, other.sources)
        keep.company = keep.company or other.company
        keep.relation = keep.relation or other.relation
        if not keep.brief and other.brief:
            keep.brief, keep.brief_by, keep.brief_at = (
                other.brief,
                other.brief_by,
                other.brief_at,
            )
        if other.last_interaction_at and (
            not keep.last_interaction_at
            or other.last_interaction_at > keep.last_interaction_at
        ):
            keep.last_interaction_at = other.last_interaction_at
        for model in (PersonInteractionModel, PersonSourceModel):
            await session.execute(
                update(model)
                .where(model.person_id == other.id)
                .values(person_id=keep.id)
            )
        # A colleague the other card was shown to keeps seeing it, as one.
        shared = (
            (
                await session.execute(
                    select(PersonShareModel.shared_with_user_id).where(
                        PersonShareModel.person_id == other.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for user_id in shared:
            await session.execute(
                pg_insert(PersonShareModel)
                .values(
                    person_id=keep.id,
                    organization_id=organization_id,
                    owner_user_id=owner_user_id,
                    shared_with_user_id=user_id,
                    created_at=now(),
                )
                .on_conflict_do_nothing(
                    index_elements=["person_id", "shared_with_user_id"]
                )
            )
        row.status = "merged"
        await session.flush()
        # Every other suggestion about the contact that is going: those
        # pairs are now about ``keep``, and are worked out again below.
        await session.execute(
            delete(PersonMergeModel).where(
                PersonMergeModel.status == "open",
                or_(
                    PersonMergeModel.keep_id == other.id,
                    PersonMergeModel.other_id == other.id,
                ),
            )
        )
        await session.delete(other)
        await session.flush()
        await _set_handles(session, keep)
        await session.flush()
        await _suggest_merges(session, keep)
        if keep.last_interaction_at:
            keep.brief_due_at = keep.brief_due_at or now()
        await session.commit()
        await session.refresh(keep)
    return keep


async def open_merge_count(organization_id: int, owner_user_id: int) -> int:
    async with db_client.async_session() as session:
        return int(
            (
                await session.execute(
                    select(func.count(PersonMergeModel.id)).where(
                        _owner(PersonMergeModel, organization_id, owner_user_id),
                        PersonMergeModel.status == "open",
                    )
                )
            ).scalar_one()
        )


# --- sharing -----------------------------------------------------------------


async def share(
    organization_id: int, owner_user_id: int, uuid: str, with_user_id: int
) -> None:
    """Show one contact card to one colleague in the same workspace."""
    person = await get(organization_id, owner_user_id, uuid)
    if with_user_id == owner_user_id:
        raise NotFound("yourself")
    if await db_client.get_membership(with_user_id, organization_id) is None:
        raise NotFound("member")
    async with db_client.async_session() as session:
        await session.execute(
            pg_insert(PersonShareModel)
            .values(
                person_id=person.id,
                organization_id=organization_id,
                owner_user_id=owner_user_id,
                shared_with_user_id=with_user_id,
                created_at=now(),
            )
            .on_conflict_do_nothing(index_elements=["person_id", "shared_with_user_id"])
        )
        await session.commit()


async def unshare(
    organization_id: int, owner_user_id: int, uuid: str, with_user_id: int
) -> None:
    person = await get(organization_id, owner_user_id, uuid)
    async with db_client.async_session() as session:
        await session.execute(
            delete(PersonShareModel).where(
                PersonShareModel.person_id == person.id,
                _owner(PersonShareModel, organization_id, owner_user_id),
                PersonShareModel.shared_with_user_id == with_user_id,
            )
        )
        await session.commit()


async def shared_to(person: PersonModel) -> list[int]:
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(PersonShareModel.shared_with_user_id).where(
                        PersonShareModel.person_id == person.id,
                        _owner(
                            PersonShareModel,
                            person.organization_id,
                            person.owner_user_id,
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )


async def shared_with(
    organization_id: int, user_id: int, *, uuid: str | None = None
) -> list[PersonModel]:
    """Cards colleagues showed this person. The caller returns the card
    fields only (``views.shared_card``), never the brief or interactions."""
    async with db_client.async_session() as session:
        stmt = (
            select(PersonModel)
            .join(PersonShareModel, PersonShareModel.person_id == PersonModel.id)
            .where(
                PersonShareModel.organization_id == organization_id,
                PersonShareModel.shared_with_user_id == user_id,
                PersonModel.organization_id == organization_id,
            )
            .order_by(func.lower(PersonModel.name))
            .limit(200)
        )
        if uuid is not None:
            stmt = stmt.where(PersonModel.uuid == uuid)
        return list((await session.execute(stmt)).scalars().all())


# --- settings ----------------------------------------------------------------


async def agents_may_read(organization_id: int, user_id: int) -> bool:
    async with db_client.async_session() as session:
        value = (
            await session.execute(
                select(PeopleSettingsModel.agents_may_read).where(
                    PeopleSettingsModel.organization_id == organization_id,
                    PeopleSettingsModel.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
    return bool(value)


async def set_agents_may_read(organization_id: int, user_id: int, value: bool) -> None:
    async with db_client.async_session() as session:
        await session.execute(
            pg_insert(PeopleSettingsModel)
            .values(
                organization_id=organization_id,
                user_id=user_id,
                agents_may_read=value,
                updated_at=now(),
            )
            .on_conflict_do_update(
                index_elements=["organization_id", "user_id"],
                set_={"agents_may_read": value, "updated_at": now()},
            )
        )
        await session.commit()
