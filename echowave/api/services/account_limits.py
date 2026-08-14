"""The two ceilings staff can set on an account, read and written in one place.

They live in different subsystems — concurrency is enforced in Redis by the
slot allocator, the spend ceiling is measured against the ledger — but to the
person setting them they are one thing: how much of us this account is allowed
to use at once, and how much it is allowed to cost in a day. So the read and
the write are here, and the route stays thin.

Two properties this module exists to hold:

**Effective and configured are different numbers.** A screen that shows only
the effective figure cannot tell "5, because that is the default" from "5,
because somebody chose 5", and the difference decides whether raising the
default moves this account. Both are returned.

**Clearing is a first-class action.** Setting a limit back to the default has
to be possible without knowing what the default currently is — otherwise the
only way to undo an override is to retype today's default, which silently
freezes the account at that number the next time the default moves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import (
    DEFAULT_DAILY_SPEND_CEILING_PAISE,
    DEFAULT_INBOUND_CONCURRENCY,
    DEFAULT_OUTBOUND_CONCURRENCY,
)
from api.db.models import OrganizationConfigurationModel
from api.enums import OrganizationConfigurationKey

#: Ceilings on the ceilings. Not a commercial judgement — a typo guard. A
#: concurrency of 100000 is a fat finger, and the failure it produces is a
#: carrier bill rather than an error message.
MAX_CONCURRENCY = 500
MAX_DAILY_SPEND_PAISE = 100_000_000  # ₹10,00,000 in a day.


class LimitError(ValueError):
    """A limit could not be set."""


@dataclass(frozen=True)
class Limit:
    key: OrganizationConfigurationKey
    default: int
    maximum: int


LIMITS: dict[str, Limit] = {
    "inbound_concurrency": Limit(
        OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT_INBOUND,
        int(DEFAULT_INBOUND_CONCURRENCY),
        MAX_CONCURRENCY,
    ),
    "outbound_concurrency": Limit(
        OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT_OUTBOUND,
        int(DEFAULT_OUTBOUND_CONCURRENCY),
        MAX_CONCURRENCY,
    ),
    "daily_spend_ceiling_paise": Limit(
        OrganizationConfigurationKey.DAILY_SPEND_CEILING_PAISE,
        int(DEFAULT_DAILY_SPEND_CEILING_PAISE),
        MAX_DAILY_SPEND_PAISE,
    ),
}


async def _configured(session: AsyncSession, *, organization_id: int) -> dict[str, int]:
    """The overrides this account actually has, keyed by field name."""
    from sqlalchemy import select

    rows = (
        (
            await session.execute(
                select(OrganizationConfigurationModel).where(
                    OrganizationConfigurationModel.organization_id == organization_id,
                    OrganizationConfigurationModel.key.in_(
                        [limit.key.value for limit in LIMITS.values()]
                        + [OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )

    by_key: dict[str, int] = {}
    for row in rows:
        value = (row.value or {}).get("value") if isinstance(row.value, dict) else None
        if value is None:
            continue
        try:
            by_key[row.key] = int(value)
        except (TypeError, ValueError):
            continue
    return by_key


async def read(session: AsyncSession, *, organization_id: int) -> dict[str, Any]:
    """What this account's limits are, and which of them somebody chose.

    ``legacy_concurrency`` is the older direction-blind limit. It is reported
    rather than folded in, because an account that has one is still bound by it
    in both directions and a screen that hid it would explain neither the
    number being enforced nor how to change it.
    """
    by_key = await _configured(session, organization_id=organization_id)
    blind = by_key.get(OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT.value)

    result: dict[str, Any] = {
        "organization_id": organization_id,
        "legacy_concurrency": blind,
    }
    for field, limit in LIMITS.items():
        configured = by_key.get(limit.key.value)
        if configured is None and blind is not None and field.endswith("_concurrency"):
            # Matches what the slot allocator actually does: the blind limit
            # applies to both directions until a per-direction one exists.
            effective = blind
        else:
            effective = configured if configured is not None else limit.default
        result[field] = {
            "effective": effective,
            "configured": configured,
            "default": limit.default,
            "maximum": limit.maximum,
        }
    return result


async def set_limit(
    session: AsyncSession, *, organization_id: int, field: str, value: int | None
) -> None:
    """Set one limit, or clear it back to the default with ``None``.

    Writes through the session the caller owns rather than the db client's own,
    so setting three limits is one transaction and a failure on the third does
    not leave the first two applied.
    """
    limit = LIMITS.get(field)
    if limit is None:
        raise LimitError(f"Unknown limit: {field}")

    if value is None:
        await session.execute(
            delete(OrganizationConfigurationModel).where(
                OrganizationConfigurationModel.organization_id == organization_id,
                OrganizationConfigurationModel.key == limit.key.value,
            )
        )
        return

    value = int(value)
    if value < 0:
        raise LimitError("A limit cannot be negative.")
    if field.endswith("_concurrency") and value == 0:
        # Zero reads as "unlimited" on the spend ceiling and would read the
        # same way here, where it means the opposite: no call may ever start.
        # Refusing is better than either meaning applied silently.
        raise LimitError(
            "A concurrency of zero would stop every call. Clear the limit "
            "instead to fall back to the default."
        )
    if value > limit.maximum:
        raise LimitError(f"That is above the maximum of {limit.maximum}.")

    from sqlalchemy import select

    existing = (
        await session.execute(
            select(OrganizationConfigurationModel).where(
                OrganizationConfigurationModel.organization_id == organization_id,
                OrganizationConfigurationModel.key == limit.key.value,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(
            OrganizationConfigurationModel(
                organization_id=organization_id,
                key=limit.key.value,
                value={"value": value},
            )
        )
    else:
        existing.value = {"value": value}
