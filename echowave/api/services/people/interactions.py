"""What Decibyl did with someone, recorded on that someone's page.

``record`` is the one hook: a call placed for a person, a document sent on
WhatsApp, a mail in or out of their Decibyl address, an app send they
approved, a meeting they captured. Each calls it with the owner (the person
Decibyl acted for), how to recognise the contact (a number, an address, or
only a name for a meeting), and one line.

It never raises and never slows its caller down by much: People is a side
record, and a call that went through must not be reported as failed because
its contact could not be written. With the flag off it does nothing.

The contact is found by number or address; failing that (a meeting names
people only) by exact name; failing that, it is added, with ``decibyl`` as
its source. The brief is not rewritten here -- ``brief_due_at`` is set a few
minutes ahead, once per window, and the sweep in ``briefs`` writes it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from api import constants
from api.db import db_client
from api.db.people_models import PersonInteractionModel, PersonModel
from api.services.people import enabled, normalise, store

CHANNELS = ("call", "whatsapp", "email", "meeting")


async def record(
    organization_id: int | None,
    owner_user_id: int | None,
    *,
    channel: str,
    line: str,
    ref: str,
    direction: str = "out",
    phone: str | None = None,
    email: str | None = None,
    name: str | None = None,
    at: datetime | None = None,
) -> PersonModel | None:
    """Add or update the contact and its interaction. None when off, when
    nobody is identified, or when it could not be written (logged)."""
    try:
        if not organization_id or not owner_user_id or not enabled(organization_id):
            return None
        return await _record(
            int(organization_id),
            int(owner_user_id),
            channel=channel,
            line=line,
            ref=ref,
            direction=direction,
            phone=phone,
            email=email,
            name=name,
            at=at,
        )
    except Exception as exc:  # noqa: BLE001 - a side record never fails its caller
        logger.warning(
            "People could not record a {} for org {}: {}", channel, organization_id, exc
        )
        return None


async def _record(
    organization_id: int,
    owner_user_id: int,
    *,
    channel: str,
    line: str,
    ref: str,
    direction: str,
    phone: str | None,
    email: str | None,
    name: str | None,
    at: datetime | None,
) -> PersonModel | None:
    assert channel in CHANNELS, channel
    number = normalise.phone(phone)
    address = normalise.email(email)
    display = normalise.text(name, normalise.MAX_NAME)
    person = None
    if number:
        person = await store.find_by_handle(
            organization_id, owner_user_id, "phone", number
        )
    if person is None and address:
        person = await store.find_by_handle(
            organization_id, owner_user_id, "email", address
        )
    if person is None and display and not (number or address):
        exact = [
            p
            for p in await store.find_by_name(organization_id, owner_user_id, display)
            if normalise.name_key(p.name) == normalise.name_key(display)
        ]
        person = exact[0] if len(exact) == 1 else None
    if person is None:
        incoming = normalise.Incoming(
            name=display,
            phones=[number] if number else [],
            emails=[address] if address else [],
        ).clean()
        if incoming is None:
            return None
        person = (
            await store.upsert(
                organization_id, owner_user_id, incoming, source="decibyl"
            )
        ).person
    elif (number and number not in (person.phones or [])) or (
        address and address not in (person.emails or [])
    ):
        person = await store.attach(
            person,
            phones=[number] if number else [],
            emails=[address] if address else [],
            source="decibyl",
        )
    when = at or store.now()
    text = normalise.text(line, 300) or channel
    async with db_client.async_session() as session:
        await session.execute(
            pg_insert(PersonInteractionModel)
            .values(
                person_id=person.id,
                organization_id=organization_id,
                owner_user_id=owner_user_id,
                channel=channel,
                direction=direction if direction in ("in", "out", "both") else "out",
                line=text,
                ref=ref[:200],
                at=when,
            )
            .on_conflict_do_update(
                index_elements=["organization_id", "owner_user_id", "ref"],
                # A later step of the same thing (the call finished) says more.
                set_={"line": text},
            )
        )
        row = (
            await session.execute(
                select(PersonModel).where(PersonModel.id == person.id).with_for_update()
            )
        ).scalar_one()
        if not row.last_interaction_at or when > row.last_interaction_at:
            row.last_interaction_at = when
        if row.brief_due_at is None:
            row.brief_due_at = store.now() + timedelta(
                seconds=constants.PEOPLE_BRIEF_DEBOUNCE_SECONDS
            )
        await session.commit()
        await session.refresh(row)
    return row
