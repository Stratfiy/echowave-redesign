"""Connecting a dialer: store its credentials, list, disconnect.

The vault rules of ``configuration.organization_credentials`` hold here too,
and its cipher is reused rather than copied so there is one secret and one
place it is read: encrypted at rest, never returned (only the last four
characters of the account's secret), no fallback secret, and every read and
write filtered by ``organization_id`` -- there is no lookup by id alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import DialerConnectionModel, ImportedCallModel
from api.services.configuration.organization_credentials import (
    OrganizationCredentialError,
    _cipher,
)
from api.services.dialer_import import exotel, smartflo

ADAPTERS = {exotel.VENDOR: exotel, smartflo.VENDOR: smartflo}

CONNECTED = "connected"
NEEDS_ATTENTION = "needs_attention"


class ConnectionError_(ValueError):
    """A connection could not be made. The message is for the business."""


@dataclass(frozen=True)
class DialerConnection:
    id: int
    vendor: str
    label: str | None
    key_last_four: str
    status: str
    last_error: str | None
    last_synced_at: datetime | None

    @classmethod
    def of(cls, row: DialerConnectionModel) -> "DialerConnection":
        return cls(
            id=row.id,
            vendor=row.vendor,
            label=row.label,
            key_last_four=row.key_last_four,
            status=row.status,
            last_error=row.last_error,
            last_synced_at=row.last_synced_at,
        )


def adapter_for(vendor: str):
    try:
        return ADAPTERS[vendor]
    except KeyError:
        raise ConnectionError_(
            f"{vendor!r} is not a dialer we can connect yet. Exotel and Tata "
            "Smartflo are."
        ) from None


def clean_credentials(vendor: str, credentials: dict) -> dict:
    """Only the fields the vendor needs, each present and trimmed."""
    adapter = adapter_for(vendor)
    cleaned = {}
    for field in adapter.FIELDS:
        value = str(credentials.get(field) or "").strip()
        if not value and not (vendor == exotel.VENDOR and field == "subdomain"):
            raise ConnectionError_(f"{field.replace('_', ' ')} is missing.")
        if value:
            cleaned[field] = value
    return cleaned


async def create(
    session: AsyncSession,
    *,
    organization_id: int,
    actor_user_id: int | None,
    vendor: str,
    credentials: dict,
    label: str | None = None,
) -> DialerConnection:
    cleaned = clean_credentials(vendor, credentials)
    try:
        ciphertext = _cipher().encrypt(json.dumps(cleaned).encode()).decode()
    except OrganizationCredentialError as exc:
        raise ConnectionError_(str(exc)) from exc
    row = DialerConnectionModel(
        organization_id=organization_id,
        vendor=vendor,
        label=(label or "").strip() or None,
        encrypted_credentials=ciphertext,
        key_last_four=adapter_for(vendor).secret_of(cleaned)[-4:],
        status=CONNECTED,
        created_by=actor_user_id,
    )
    session.add(row)
    await session.flush()
    return DialerConnection.of(row)


async def list_for(
    session: AsyncSession, *, organization_id: int
) -> list[DialerConnection]:
    rows = await session.scalars(
        select(DialerConnectionModel)
        .where(DialerConnectionModel.organization_id == organization_id)
        .order_by(DialerConnectionModel.id)
    )
    return [DialerConnection.of(row) for row in rows]


async def get_row(
    session: AsyncSession, *, organization_id: int, connection_id: int
) -> DialerConnectionModel | None:
    return await session.scalar(
        select(DialerConnectionModel).where(
            DialerConnectionModel.organization_id == organization_id,
            DialerConnectionModel.id == connection_id,
        )
    )


async def remove(
    session: AsyncSession, *, organization_id: int, connection_id: int
) -> bool:
    """Disconnect, and take everything imported through it along: the
    recordings first, while their keys are still readable, then the rows
    (which cascade)."""
    from api.services.dialer_import.importer import delete_recordings

    keys = await session.scalars(
        select(ImportedCallModel.recording_key).where(
            ImportedCallModel.organization_id == organization_id,
            ImportedCallModel.connection_id == connection_id,
            ImportedCallModel.recording_key.is_not(None),
        )
    )
    await delete_recordings(list(keys))
    result = await session.execute(
        delete(DialerConnectionModel).where(
            DialerConnectionModel.organization_id == organization_id,
            DialerConnectionModel.id == connection_id,
        )
    )
    return bool(result.rowcount)


def credentials_of(row: DialerConnectionModel) -> dict:
    """The plaintext, for the importer only. Never returned by a route."""
    return json.loads(_cipher().decrypt(row.encrypted_credentials.encode()))


__all__ = [
    "ADAPTERS",
    "CONNECTED",
    "NEEDS_ATTENTION",
    "ConnectionError_",
    "DialerConnection",
    "adapter_for",
    "clean_credentials",
    "create",
    "credentials_of",
    "get_row",
    "list_for",
    "remove",
]
