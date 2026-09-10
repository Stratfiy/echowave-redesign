"""One line on the invoice for a WhatsApp message sent from the platform sender.

Shaped like ``embedding_ingestion.py``: a direct ledger debit keyed on the
message, exactly once, with the vendor's cost recorded beside the charge in
the row's note so a margin query has both halves. A message on the account's
own Twilio sender is not charged here — Twilio bills the account directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api import constants
from api.db.models import CreditLedgerModel
from api.enums import CreditLedgerKind

REF_TYPE = "whatsapp_message"


def price_paise() -> int:
    return max(0, int(constants.WHATSAPP_MESSAGE_PRICE_PAISE))


def vendor_cost_paise() -> int:
    return max(0, int(constants.WHATSAPP_MESSAGE_COST_PAISE))


async def _balance_paise(session: AsyncSession, *, organization_id: int) -> int:
    return int(
        await session.scalar(
            select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
                CreditLedgerModel.organization_id == organization_id
            )
        )
        or 0
    )


async def debit_message(
    session: AsyncSession,
    *,
    organization_id: int,
    message_id: str,
    workflow_run_id: int | None = None,
    node_name: str = "",
) -> int:
    """Charge one sent message. Returns the paise debited (0 if already done).

    Keyed on the provider's message id, so a post-call task re-run after a
    crash between the send and this write debits nothing a second time.
    """
    charge = price_paise()
    if charge <= 0 or not message_id:
        return 0
    existing = await session.scalar(
        select(CreditLedgerModel.id).where(
            CreditLedgerModel.organization_id == organization_id,
            CreditLedgerModel.kind == CreditLedgerKind.MESSAGE.value,
            CreditLedgerModel.ref_type == REF_TYPE,
            CreditLedgerModel.ref_id == message_id[:64],
        )
    )
    if existing is not None:
        logger.debug("Message {} already debited", message_id)
        return 0
    balance = await _balance_paise(session, organization_id=organization_id)
    label = "WhatsApp message"
    if node_name:
        label += f" ({node_name})"
    if workflow_run_id:
        label += f" after call {workflow_run_id}"
    session.add(
        CreditLedgerModel(
            organization_id=organization_id,
            delta_paise=-charge,
            kind=CreditLedgerKind.MESSAGE.value,
            ref_type=REF_TYPE,
            ref_id=message_id[:64],
            balance_after_paise=balance - charge,
            note=f"{label}; vendor cost {vendor_cost_paise()} paise",
            created_at=datetime.now(UTC),
        )
    )
    # Flushed at once so a second call in the same transaction sees the row
    # and debits nothing, which is the whole point of keying on the id.
    await session.flush()
    return charge
