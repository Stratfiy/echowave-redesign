"""Chase the carrier for verdicts on forwarded KYC applications.

Once we forward an application we are waiting on the licensee, and the licensee
does not call us back — the carriers we integrate with expose status on their
compliance application, not a webhook. Without a poll, an approved account would
sit blocked until someone opened the admin queue and noticed.

The poll only ever *records* what the carrier says. It cannot invent an
approval: :func:`record_carrier_verdict` runs the same state machine the admin
routes do, so a malformed or unexpected status leaves the account where it was.
"""

from loguru import logger

from api.db import db_client
from api.services.kyc import service as kyc_service
from api.services.kyc.carrier import (
    CarrierNotConfigured,
    CarrierVerdict,
    get_carrier,
)
from api.services.kyc.state import KycTransitionError


async def poll_kyc_carrier_status(_ctx) -> None:
    """Ask each carrier where our outstanding applications stand.

    One carrier failing must not stop the others, and one organization failing
    must not stop the rest of the batch — a compliance API returning 500 for a
    single application is not a reason to leave every other customer waiting
    until the next tick.
    """
    pending = await db_client.list_kyc_awaiting_carrier()
    if not pending:
        return

    moved = 0
    for record in pending:
        try:
            carrier = get_carrier(record.carrier)
        except CarrierNotConfigured:
            logger.warning(
                "Org {} is forwarded to carrier {!r}, which has no integration",
                record.organization_id,
                record.carrier,
            )
            continue

        if not getattr(carrier, "pollable", False):
            # Moved on by hand. Touching carrier_checked_at here would imply we
            # asked the licensee when nobody did.
            continue

        try:
            status = await carrier.check(record.carrier_reference)
        except CarrierNotConfigured:
            # An integration declared pollable but not finished. Nothing to
            # chase, and nothing to record.
            continue
        except Exception:
            logger.exception(
                "Could not read KYC status for org {} from carrier {}",
                record.organization_id,
                record.carrier,
            )
            continue

        if status.verdict is CarrierVerdict.PENDING:
            # Still records carrier_checked_at, so the queue can show how long
            # an application has been sitting with the licensee.
            await kyc_service.record_carrier_verdict(
                organization_id=record.organization_id,
                verdict=status.verdict,
                raw_status=status.raw_status,
            )
            continue

        try:
            await kyc_service.record_carrier_verdict(
                organization_id=record.organization_id,
                verdict=status.verdict,
                raw_status=status.raw_status,
                rejection_reason=status.rejection_reason,
            )
        except KycTransitionError:
            # The record moved between the query and now — a staff member
            # entered the verdict by hand, most likely. Theirs wins.
            logger.warning(
                "Carrier verdict {} for org {} no longer applies",
                status.verdict.value,
                record.organization_id,
            )
            continue

        moved += 1
        logger.info(
            "Carrier {} returned {} for org {}",
            record.carrier,
            status.verdict.value,
            record.organization_id,
        )

    logger.info(
        "Polled {} forwarded KYC application(s); {} moved on", len(pending), moved
    )
