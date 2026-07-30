"""Monthly tax invoices.

A prepaid top-up is taxed on receipt and evidenced by a receipt voucher. The
tax invoice is the other half: it states what the advance was actually consumed
against, which is what a customer needs to claim input credit and what we need
to adjust the advance in a return.

No money moves. Nobody is charged by this job — the usage was already paid for
out of prepaid credit, and the tax on it was already collected with the advance.

Runs on the 2nd of each month for the month just ended, and covers **IST**
calendar months, because that is the month our customers and our returns are
both counted in. Running on the 1st would race with calls still being costed
from the last hours of the 31st.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.models import OrganizationModel
from api.services.billing.documents import (
    DocumentError,
    issue_tax_invoice,
    supplier_is_configured,
)
from api.services.billing.rollup import IST


def previous_month(today: date) -> tuple[date, date]:
    """The full calendar month before ``today``, inclusive at both ends."""
    first_of_this_month = today.replace(day=1)
    last_of_previous = first_of_this_month - timedelta(days=1)
    return last_of_previous.replace(day=1), last_of_previous


async def issue_monthly_tax_invoices(_ctx, *, for_month: date | None = None) -> None:
    """Invoice every account with usage in the month just ended.

    Idempotent: a unique index on (organization, kind, period start) means a
    retried run returns the invoice already issued rather than a second one. A
    duplicate invoice is a filing correction, not something you can delete.

    Accounts with no usage get no invoice, which is most accounts in most
    months — an invoice for nothing is not a document anyone wants.
    """
    if not supplier_is_configured():
        logger.error(
            "Skipping monthly tax invoices: SUPPLIER_LEGAL_NAME and SUPPLIER_GSTIN "
            "are not set, so nothing issued would be a valid tax invoice."
        )
        return

    today = for_month or datetime.now(IST).date()
    period_start, period_end = previous_month(today)

    async with db_client.async_session() as session:
        organization_ids = list(
            (await session.scalars(select(OrganizationModel.id))).all()
        )

    issued = 0
    skipped = 0
    failed = 0
    for organization_id in organization_ids:
        # One session per account, committed as it goes. A single transaction
        # over every account on the platform would mean one bad profile rolls
        # back a month of everyone else's invoices — and the serial numbers with
        # them, which is the one thing that must not develop gaps.
        try:
            async with db_client.async_session() as session:
                document = await issue_tax_invoice(
                    session,
                    organization_id=organization_id,
                    period_start=period_start,
                    period_end=period_end,
                )
                if document is None:
                    skipped += 1
                    continue
                await session.commit()
                issued += 1
        except (DocumentError, ValueError) as exc:
            # Most likely an export account with no LUT on file, or a profile
            # whose country and GSTIN disagree. Logged per account and carried
            # on: one unbillable account must not stop the rest.
            failed += 1
            logger.error(
                "Could not issue a tax invoice to org {} for {}: {}",
                organization_id,
                period_start.isoformat()[:7],
                exc,
            )

    logger.info(
        "Monthly tax invoices for {}: {} issued, {} with no usage, {} failed",
        period_start.isoformat()[:7],
        issued,
        skipped,
        failed,
    )
