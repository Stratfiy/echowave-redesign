"""Commitments a person approved tracking, and "who owes me" (handoff 6,
Follow-up; founder request).

**Tracked only after approval.** From a conversation, a commitment is a
``track_commitment`` card; confirming it creates the row, once -- a unique
index on the approving card means the same Confirm from two channels tracks
it once. A person adding one themselves (the API) is the approval.

**The follow-up is a card.** ``follow_up_commitment`` proposes the send as
an ordinary ``run_tool`` card carrying the commitment's id, and links the
card here. The card already holds the contract: one send after retries
(compare-and-swap), cancel before it fires stops it, a changed message is
a new version needing a new Confirm, and an unanswered provider is
``outcome unknown``. So the delivery state is read live from the card,
never copied, and can never disagree with it.

**Private by default.** The owner sees their own; a teammate sees one only
after the owner shares it with the workspace.
"""

from __future__ import annotations

import re
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from api.db import db_client
from api.db.agents_models import CommitmentModel
from api.services import features
from api.services.helpers import sharing

FLAG = "follow_up_ledger"
OWED_TO_ME = "owed_to_me"
I_OWE = "i_owe"
DIRECTIONS = (OWED_TO_ME, I_OWE)
OPEN = "open"
SETTLED = "settled"
CANCELLED = "cancelled"
STATUSES = (OPEN, SETTLED, CANCELLED)

Invalid = sharing.Invalid


class Conflict(Exception):
    def __init__(self, current: CommitmentModel | None):
        super().__init__("This changed since you looked at it.")
        self.current = current


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


@dataclass(frozen=True)
class Fields:
    direction: str
    counterparty: str
    description: str
    contact: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    due_on: date | None = None

    def as_args(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "counterparty": self.counterparty,
            "description": self.description,
            "contact": self.contact,
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "due_on": self.due_on.isoformat() if self.due_on else None,
        }


def _minor(amount: Any) -> int | None:
    if amount in (None, ""):
        return None
    text = re.sub(r"[,\s₹]|INR|Rs\.?", "", str(amount), flags=re.IGNORECASE)
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise Invalid("Give the amount as a number.") from None
    if value <= 0 or value > Decimal("1000000000000"):
        raise Invalid("Give an amount above zero.")
    return int((value * 100).to_integral_value())


def clean(raw: dict[str, Any]) -> Fields:
    direction = str(raw.get("direction") or OWED_TO_ME).strip().lower()
    if direction not in DIRECTIONS:
        raise Invalid("Say whether they owe you or you owe them.")
    counterparty = re.sub(r"\s+", " ", str(raw.get("counterparty") or "")).strip()[:200]
    if not counterparty:
        raise Invalid("Say who it is with.")
    description = str(raw.get("description") or "").strip()[:2000]
    if not description:
        raise Invalid("Say what was promised.")
    amount_minor = (
        int(raw["amount_minor"])
        if raw.get("amount_minor") not in (None, "")
        else _minor(raw.get("amount"))
    )
    currency = None
    if amount_minor is not None:
        currency = str(raw.get("currency") or "INR").strip().upper()[:3]
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise Invalid("Use a three-letter currency code, such as INR.")
    due_on = None
    if raw.get("due_on"):
        try:
            due_on = date.fromisoformat(str(raw["due_on"])[:10])
        except ValueError:
            raise Invalid("Give the due date as YYYY-MM-DD.") from None
    contact = str(raw.get("contact") or "").strip()[:320] or None
    return Fields(
        direction=direction,
        counterparty=counterparty,
        description=description,
        contact=contact,
        amount_minor=amount_minor,
        currency=currency,
        due_on=due_on,
    )


def money(amount_minor: int | None, currency: str | None) -> str | None:
    if amount_minor is None:
        return None
    value = Decimal(amount_minor) / 100
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"₹{text}" if currency in (None, "INR") else f"{currency} {text}"


def label(f: Fields) -> str:
    amount = money(f.amount_minor, f.currency)
    due = f" by {f.due_on.isoformat()}" if f.due_on else ""
    what = f"{amount} for {f.description}" if amount else f.description
    if f.direction == OWED_TO_ME:
        return f"Track: {f.counterparty} owes you {what}{due}"[:200]
    return f"Track: you owe {f.counterparty} {what}{due}"[:200]


async def create(
    fields: Fields,
    *,
    organization_id: int,
    user_id: int,
    approved_card_id: int | None = None,
    visibility: str = sharing.PRIVATE,
) -> CommitmentModel:
    """Track it. With ``approved_card_id``, at most once per card."""
    now = datetime.now(UTC)
    values = dict(
        uuid=str(uuid_lib.uuid4()),
        organization_id=organization_id,
        owner_user_id=user_id,
        visibility=sharing.clean(visibility),
        direction=fields.direction,
        counterparty=fields.counterparty,
        contact=fields.contact,
        description=fields.description,
        amount_minor=fields.amount_minor,
        currency=fields.currency,
        due_on=fields.due_on,
        status=OPEN,
        approved_card_id=approved_card_id,
        revision=1,
        created_at=now,
        updated_at=now,
    )
    async with db_client.async_session() as session:
        if approved_card_id is None:
            row = CommitmentModel(**values)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row
        statement = (
            insert(CommitmentModel)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["organization_id", "approved_card_id"],
                index_where=CommitmentModel.approved_card_id.isnot(None),
            )
        )
        await session.execute(statement)
        await session.commit()
        return await session.scalar(
            select(CommitmentModel).where(
                CommitmentModel.organization_id == organization_id,
                CommitmentModel.approved_card_id == approved_card_id,
            )
        )


async def list_visible(
    *,
    organization_id: int,
    user_id: int,
    direction: str | None = None,
    status: str | None = OPEN,
) -> list[CommitmentModel]:
    query = select(CommitmentModel).where(
        *sharing.visible_to(
            CommitmentModel, organization_id=organization_id, user_id=user_id
        )
    )
    if direction:
        query = query.where(CommitmentModel.direction == direction)
    if status:
        query = query.where(CommitmentModel.status == status)
    query = query.order_by(
        CommitmentModel.due_on.asc().nulls_last(), CommitmentModel.id.asc()
    )
    async with db_client.async_session() as session:
        return list((await session.scalars(query)).all())


async def get_visible(
    *,
    organization_id: int,
    user_id: int,
    commitment_id: int | None = None,
    commitment_uuid: str | None = None,
) -> CommitmentModel | None:
    query = select(CommitmentModel).where(
        *sharing.visible_to(
            CommitmentModel, organization_id=organization_id, user_id=user_id
        )
    )
    if commitment_uuid is not None:
        query = query.where(CommitmentModel.uuid == commitment_uuid)
    else:
        query = query.where(CommitmentModel.id == commitment_id)
    async with db_client.async_session() as session:
        return await session.scalar(query)


async def change(
    commitment_uuid: str,
    *,
    organization_id: int,
    user_id: int,
    revision: int,
    status: str | None = None,
    visibility: str | None = None,
) -> CommitmentModel:
    """Settle, cancel, reopen or share. Only the owner; names the revision
    it read, so a second tab's stale change is refused, not applied."""
    values: dict[str, Any] = {"updated_at": datetime.now(UTC)}
    if status is not None:
        if status not in STATUSES:
            raise Invalid("Unknown status.")
        values["status"] = status
        values["settled_at"] = datetime.now(UTC) if status == SETTLED else None
    if visibility is not None:
        values["visibility"] = sharing.clean(visibility)
    async with db_client.async_session() as session:
        result = await session.execute(
            update(CommitmentModel)
            .where(
                CommitmentModel.uuid == commitment_uuid,
                CommitmentModel.revision == revision,
                *sharing.owned_by(
                    CommitmentModel, organization_id=organization_id, user_id=user_id
                ),
            )
            .values(revision=CommitmentModel.revision + 1, **values)
        )
        await session.commit()
        current = await session.scalar(
            select(CommitmentModel).where(
                CommitmentModel.uuid == commitment_uuid,
                *sharing.owned_by(
                    CommitmentModel, organization_id=organization_id, user_id=user_id
                ),
            )
        )
    if result.rowcount != 1:
        raise Conflict(current)
    return current


async def link_follow_up(
    *, organization_id: int, commitment_id: int, card_id: int
) -> None:
    async with db_client.async_session() as session:
        await session.execute(
            update(CommitmentModel)
            .where(
                CommitmentModel.id == commitment_id,
                CommitmentModel.organization_id == organization_id,
            )
            .values(follow_up_card_id=card_id, updated_at=datetime.now(UTC))
        )
        await session.commit()


#: A card's state, as a follow-up's delivery state.
DELIVERY = {
    "proposed": "awaiting_approval",
    "armed": "scheduled",
    "running": "sending",
    "done": "sent",
    "failed": "failed",
    "declined": "cancelled",
    "undone": "cancelled",
    "cancelled": "cancelled",
    "outcome_unknown": "outcome_unknown",
}


async def follow_up_state(
    row: CommitmentModel,
) -> dict[str, Any] | None:
    if not row.follow_up_card_id:
        return None
    card = await db_client.get_agent_event(
        row.follow_up_card_id, organization_id=row.organization_id
    )
    if card is None:
        return {"card_id": row.follow_up_card_id, "delivery": "unknown"}
    payload = card.payload or {}
    state = str(payload.get("state") or "proposed")
    # Anything not in the map is shown as itself rather than dropped.
    return {
        "card_id": card.id,
        "delivery": DELIVERY.get(state, state),
        "label": card.summary,
        "error": payload.get("error"),
    }


async def describe(row: CommitmentModel, *, user_id: int) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    return {
        "uuid": row.uuid,
        "id": row.id,
        "direction": row.direction,
        "counterparty": row.counterparty,
        "contact": row.contact,
        "description": row.description,
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "amount": money(row.amount_minor, row.currency),
        "due_on": row.due_on.isoformat() if row.due_on else None,
        "overdue": bool(row.status == OPEN and row.due_on and row.due_on < today),
        "status": row.status,
        "visibility": row.visibility,
        "mine": row.owner_user_id == user_id,
        "revision": row.revision,
        "follow_up": await follow_up_state(row),
    }


async def who_owes_me(*, organization_id: int, user_id: int) -> dict[str, Any]:
    rows = await list_visible(
        organization_id=organization_id, user_id=user_id, direction=OWED_TO_ME
    )
    totals: dict[str, int] = {}
    for r in rows:
        if r.amount_minor is not None:
            totals[r.currency or "INR"] = totals.get(r.currency or "INR", 0) + int(
                r.amount_minor
            )
    items = [await describe(r, user_id=user_id) for r in rows]
    return {
        "items": items,
        "totals": [
            {"currency": c, "amount_minor": v, "amount": money(v, c)}
            for c, v in sorted(totals.items())
        ],
        "overdue": sum(1 for i in items if i["overdue"]),
    }
