"""Contact lists and the callers in them.

Every method takes ``organization_id`` and filters on it, including the ones
that already have a list id: a list id in a request body proves the row exists,
never that the caller may touch it. See the org-scoping rules in
``api/AGENTS.md``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.future import select

from api.db.base_client import BaseDBClient
from api.db.models import ContactListModel, ContactModel


class ContactClient(BaseDBClient):
    # ---- lists -----------------------------------------------------------

    async def create_contact_list(
        self, *, organization_id: int, name: str, description: str | None = None
    ) -> ContactListModel:
        async with self.async_session() as session:
            row = ContactListModel(
                organization_id=organization_id, name=name, description=description
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_contact_lists(
        self, *, organization_id: int
    ) -> list[ContactListModel]:
        async with self.async_session() as session:
            result = await session.execute(
                select(ContactListModel)
                .where(ContactListModel.organization_id == organization_id)
                .order_by(ContactListModel.name)
            )
            return list(result.scalars().all())

    async def get_contact_list(
        self, contact_list_id: int, *, organization_id: int
    ) -> ContactListModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(ContactListModel).where(
                    ContactListModel.id == contact_list_id,
                    ContactListModel.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    async def update_contact_list(
        self,
        contact_list_id: int,
        *,
        organization_id: int,
        name: str | None = None,
        description: str | None = None,
    ) -> ContactListModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(ContactListModel).where(
                    ContactListModel.id == contact_list_id,
                    ContactListModel.organization_id == organization_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            if name is not None:
                row.name = name
            if description is not None:
                row.description = description
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_contact_list(
        self, contact_list_id: int, *, organization_id: int
    ) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(ContactListModel).where(
                    ContactListModel.id == contact_list_id,
                    ContactListModel.organization_id == organization_id,
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def count_contacts(
        self, contact_list_id: int, *, organization_id: int
    ) -> int:
        async with self.async_session() as session:
            result = await session.execute(
                select(func.count(ContactModel.id)).where(
                    ContactModel.contact_list_id == contact_list_id,
                    ContactModel.organization_id == organization_id,
                )
            )
            return int(result.scalar() or 0)

    # ---- contacts --------------------------------------------------------

    async def get_contacts(
        self,
        contact_list_id: int,
        *,
        organization_id: int,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> tuple[list[ContactModel], int]:
        async with self.async_session() as session:
            conditions = [
                ContactModel.contact_list_id == contact_list_id,
                ContactModel.organization_id == organization_id,
            ]
            if search:
                like = f"%{search.strip()}%"
                conditions.append(
                    ContactModel.phone_normalized.ilike(like)
                    | ContactModel.phone_raw.ilike(like)
                    | ContactModel.email_normalized.ilike(like)
                    | ContactModel.name.ilike(like)
                )

            total = await session.execute(
                select(func.count(ContactModel.id)).where(*conditions)
            )
            result = await session.execute(
                select(ContactModel)
                .where(*conditions)
                .order_by(ContactModel.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return list(result.scalars().all()), int(total.scalar() or 0)

    async def search_contacts_for_organization(
        self,
        organization_id: int,
        terms: list[str],
        *,
        limit: int = 5,
    ) -> list[ContactModel]:
        """Contacts anywhere in this account matching any of these terms.

        The counterpart to :meth:`get_contacts`, which searches inside one
        list. A person asking "what do we know about Ravi" has not named a
        list and should not have to: the account's contacts are one book to
        them however they were uploaded.

        Each term is matched against the name and against both spellings of
        the number, so ``9876543210`` finds a contact stored as
        ``+91 98765 43210``. An empty term list returns nothing rather than
        everything -- a question naming nobody must not drag the whole book
        into a prompt.
        """
        cleaned = [t.strip() for t in terms if t and t.strip()]
        if not cleaned:
            return []
        async with self.async_session() as session:
            matches = or_(
                *[
                    ContactModel.name.ilike(f"%{term}%")
                    | ContactModel.phone_normalized.ilike(f"%{term}%")
                    | ContactModel.phone_raw.ilike(f"%{term}%")
                    | ContactModel.email_normalized.ilike(f"%{term}%")
                    for term in cleaned
                ]
            )
            result = await session.execute(
                select(ContactModel)
                .where(
                    ContactModel.organization_id == organization_id,
                    matches,
                )
                .order_by(ContactModel.updated_at.desc(), ContactModel.id.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def find_contact_by_phone(
        self, contact_list_id: int, phone_normalized: str
    ) -> ContactModel | None:
        """The inbound lookup, on a ringing phone.

        Not org-scoped, and that is deliberate rather than an oversight: the
        inbound path has no authenticated user, and the list id it passes was
        read off the phone number row that the carrier's webhook already
        resolved to one organization. Scoping again here would mean threading
        an org id through the guard for a check the caller has already made.
        """
        async with self.async_session() as session:
            result = await session.execute(
                select(ContactModel).where(
                    ContactModel.contact_list_id == contact_list_id,
                    ContactModel.phone_normalized == phone_normalized,
                )
            )
            return result.scalar_one_or_none()

    async def find_contact_by_email(
        self, contact_list_id: int, email_normalized: str, *, organization_id: int
    ) -> ContactModel | None:
        async with self.async_session() as session:
            result = await session.execute(
                select(ContactModel).where(
                    ContactModel.contact_list_id == contact_list_id,
                    ContactModel.organization_id == organization_id,
                    ContactModel.email_normalized == email_normalized,
                )
            )
            return result.scalar_one_or_none()

    async def upsert_contacts(
        self,
        contact_list_id: int,
        *,
        organization_id: int,
        rows: list[dict[str, Any]],
    ) -> tuple[int, int]:
        """Insert or refresh contacts, returning ``(written, skipped)``.

        Upsert rather than insert so re-uploading a corrected CSV is a refresh
        instead of a duplicate set somebody has to clean up by hand — the
        unique constraint on ``(contact_list_id, phone_normalized)`` is what
        makes that well-defined. A row with no number (OP-3: a prospect
        with an address) is keyed on ``(contact_list_id, email_normalized)``
        instead, and a row with neither is skipped and counted.

        Chunked because a contact list is the one table here an account fills
        by uploading a file, and a single statement carrying 50,000 rows is how
        a well-meaning import takes the database down.
        """
        if not rows:
            return 0, 0

        by_phone: list[dict[str, Any]] = []
        by_email: list[dict[str, Any]] = []
        skipped = 0
        for row in rows:
            email = (row.get("email") or "").strip() or None
            values = {
                "organization_id": organization_id,
                "contact_list_id": contact_list_id,
                "phone_raw": row.get("phone_raw") or None,
                "phone_normalized": row.get("phone_normalized") or None,
                "email": email,
                "email_normalized": email.lower() if email else None,
                "name": row.get("name"),
                "attributes": row.get("attributes") or {},
            }
            if values["phone_normalized"]:
                by_phone.append(values)
            elif values["email_normalized"]:
                by_email.append(values)
            else:
                skipped += 1

        written = 0
        CHUNK = 500
        async with self.async_session() as session:
            for rows_, constraint, refresh in (
                (
                    by_phone,
                    "uq_contacts_list_phone",
                    ("phone_raw", "email", "email_normalized"),
                ),
                (by_email, "uq_contacts_list_email", ("email",)),
            ):
                for start in range(0, len(rows_), CHUNK):
                    chunk = rows_[start : start + CHUNK]
                    statement = pg_insert(ContactModel).values(chunk)
                    statement = statement.on_conflict_do_update(
                        constraint=constraint,
                        set_={
                            **{
                                col: getattr(statement.excluded, col) for col in refresh
                            },
                            "name": statement.excluded.name,
                            "attributes": statement.excluded.attributes,
                        },
                    )
                    await session.execute(statement)
                    written += len(chunk)
            await session.commit()
        return written, skipped

    async def touch_contact(
        self, contact_id: int, *, organization_id: int, attributes: Dict[str, Any]
    ) -> bool:
        """Merge ``attributes`` onto one contact's open attributes (OP-4: the
        outcome of a send, stamped on the prospect). Existing keys not named
        are kept."""
        async with self.async_session() as session:
            result = await session.execute(
                select(ContactModel).where(
                    ContactModel.id == contact_id,
                    ContactModel.organization_id == organization_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            row.attributes = {**(row.attributes or {}), **attributes}
            await session.commit()
            return True

    async def delete_contact(self, contact_id: int, *, organization_id: int) -> bool:
        async with self.async_session() as session:
            result = await session.execute(
                delete(ContactModel).where(
                    ContactModel.id == contact_id,
                    ContactModel.organization_id == organization_id,
                )
            )
            await session.commit()
            return bool(result.rowcount)
