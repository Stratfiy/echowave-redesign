"""Pre-declaration of calling numbers to the telecom provider (FD-2).

TRAI's TCCCP Third Amendment (18 September 2026) requires that a number used
for automated voice calls in India -- an auto-dialler, a robocall, which is
what every call an agent places is -- has been declared to the telecom
provider first. The provider does the declaring; this module is the
account's **record** that it happened, and the **gate** that reads it.

The record is one row per number per workspace (``number_predeclarations``):
a status, the day it was declared, the provider's reference. It is keyed on
the number, not on a phone-number row, because a provider's calling numbers
live in its configuration's credentials and need not have a row here.

The gate sits where the calling number is chosen, on every path that places
an automated call: the campaign dialler's number pool, the outbound helper,
the public agent, the editor's test call. With ``TRAI_PREDECLARATION_ENFORCED``
on, an Indian calling number is used only when its record says *declared*;
an account whose only Indian numbers are undeclared is refused with the
reason, before a run is created or a slot spent. Numbers outside India pass:
the duty is India's. A verified test call to the account's own handset is
exempt, for the reason the calling window is (see ``dnd.py``): the person the
rule protects is not the account's own phone.

Off by default. Switching it on refuses calls on a deployment whose numbers
are not yet recorded, which is a decision, not an upgrade.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from api import constants
from api.services.compliance import dnd

DECLARED = "declared"
PENDING = "pending"
WITHDRAWN = "withdrawn"
STATUSES = (DECLARED, PENDING, WITHDRAWN)
NOT_DECLARED = "not_declared"

INDIA = "91"


class NotPredeclared(dnd.CallRefused):
    """The calling number has not been declared to its provider."""


def enforced() -> bool:
    return constants.TRAI_PREDECLARATION_ENFORCED


def normalise(number: str | None) -> str | None:
    """E.164 with the leading +, or None for something undialable."""
    return dnd.to_dialable(dnd.normalise_number(number))


def is_indian(number: str | None) -> bool:
    key = dnd.normalise_number(number)
    return bool(key) and key.startswith(INDIA) and len(key) == len(INDIA) + 10


async def declared_numbers(organization_id: int) -> set[str]:
    """The workspace's numbers whose record says declared, E.164."""
    from sqlalchemy import select

    from api.db import db_client
    from api.db.models import NumberPredeclarationModel

    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(NumberPredeclarationModel.address_normalized).where(
                    NumberPredeclarationModel.organization_id == organization_id,
                    NumberPredeclarationModel.status == DECLARED,
                )
            )
        ).all()
    return {str(r) for r in rows}


def _refusal(undeclared: list[str]) -> NotPredeclared:
    shown = ", ".join(undeclared[:3]) + (" and more" if len(undeclared) > 3 else "")
    return NotPredeclared(
        f"{shown} {'has' if len(undeclared) == 1 else 'have'} not been declared "
        "to the telecom provider for automated calls (TRAI TCCCP). Record the "
        "declaration under the number's telephony configuration, or call from "
        "a declared number. This call was not placed."
    )


async def allowed_from_numbers(
    organization_id: int, candidates: list[str] | None, *, automated: bool = True
) -> list[str]:
    """The calling numbers this call may use, out of ``candidates``.

    Everything, when the gate is off, the call is not automated, or none of
    the candidates is Indian. Otherwise the Indian candidates are kept only
    when declared; if that leaves nothing at all, the refusal names the
    undeclared numbers. Fails **closed**: a record that cannot be read is not
    evidence the number was declared.
    """
    numbers = [n for n in (candidates or []) if n]
    if not enforced() or not automated or not numbers:
        return numbers
    indian = [n for n in numbers if is_indian(n)]
    if not indian:
        return numbers
    try:
        declared = await declared_numbers(organization_id)
    except Exception as exc:  # noqa: BLE001 - fail closed, see the docstring
        logger.error(
            "Could not read pre-declarations for org {}: {}. Refusing.",
            organization_id,
            exc,
        )
        raise _refusal(indian) from exc
    kept = [n for n in numbers if not is_indian(n) or normalise(n) in declared]
    if not kept:
        raise _refusal([n for n in indian if normalise(n) not in declared])
    return kept


async def choose(
    organization_id: int, candidates: list[str] | None, *, automated: bool = True
) -> str | None:
    """One calling number for this call, or None to let the provider choose.

    None whenever the gate changes nothing, so a provider that picks its own
    number keeps doing so and nothing about a non-Indian deployment moves.
    """
    numbers = [n for n in (candidates or []) if n]
    if not enforced() or not automated or not any(is_indian(n) for n in numbers):
        return None
    allowed = await allowed_from_numbers(organization_id, numbers, automated=automated)
    return random.choice(allowed)


async def assert_from_number(
    organization_id: int, number: str | None, *, automated: bool = True
) -> None:
    """A calling number somebody already chose: refuse it if undeclared."""
    if not number:
        return
    await allowed_from_numbers(organization_id, [number], automated=automated)


# --- the record ----------------------------------------------------------------


@dataclass(frozen=True)
class Declaration:
    address_normalized: str
    status: str
    declared_at: datetime | None
    reference: str | None
    note: str | None
    declared_by: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "address_normalized": self.address_normalized,
            "status": self.status,
            "declared_at": self.declared_at.isoformat() if self.declared_at else None,
            "reference": self.reference,
            "note": self.note,
            "declared_by": self.declared_by,
        }


def _view(row: Any) -> Declaration:
    return Declaration(
        address_normalized=row.address_normalized,
        status=row.status,
        declared_at=row.declared_at,
        reference=row.reference,
        note=row.note,
        declared_by=row.declared_by,
    )


async def list_declarations(organization_id: int) -> list[Declaration]:
    from sqlalchemy import select

    from api.db import db_client
    from api.db.models import NumberPredeclarationModel

    async with db_client.async_session() as session:
        rows = (
            await session.scalars(
                select(NumberPredeclarationModel)
                .where(NumberPredeclarationModel.organization_id == organization_id)
                .order_by(NumberPredeclarationModel.address_normalized)
            )
        ).all()
    return [_view(r) for r in rows]


async def status_by_number(organization_id: int) -> dict[str, str]:
    return {
        d.address_normalized: d.status for d in await list_declarations(organization_id)
    }


async def record(
    *,
    organization_id: int,
    number: str,
    status: str,
    reference: str | None = None,
    note: str | None = None,
    declared_at: datetime | None = None,
    user_id: int | None = None,
) -> Declaration:
    """Write or update a number's record. Declared without a date is dated
    now; a status other than declared keeps whatever date it had."""
    from sqlalchemy import select

    from api.db import db_client
    from api.db.models import NumberPredeclarationModel

    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    key = normalise(number)
    if not key:
        raise ValueError("That is not a phone number.")
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(NumberPredeclarationModel).where(
                NumberPredeclarationModel.organization_id == organization_id,
                NumberPredeclarationModel.address_normalized == key,
            )
        )
        if row is None:
            row = NumberPredeclarationModel(
                organization_id=organization_id, address_normalized=key
            )
            session.add(row)
        row.status = status
        row.reference = (reference or "").strip()[:120] or None
        row.note = (note or "").strip() or None
        row.declared_by = user_id
        if status == DECLARED:
            row.declared_at = declared_at or row.declared_at or datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(row)
        return _view(row)


async def forget(*, organization_id: int, number: str) -> bool:
    from sqlalchemy import select

    from api.db import db_client
    from api.db.models import NumberPredeclarationModel

    key = normalise(number)
    if not key:
        return False
    async with db_client.async_session() as session:
        row = await session.scalar(
            select(NumberPredeclarationModel).where(
                NumberPredeclarationModel.organization_id == organization_id,
                NumberPredeclarationModel.address_normalized == key,
            )
        )
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        return True
