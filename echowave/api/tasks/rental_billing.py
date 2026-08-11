"""Scheduled billing of recurring charges, and nightly number reconciliation.

Both run daily rather than monthly. A monthly job that fails has a month of
silence before anyone notices; a daily job that finds nothing due does almost
no work, and the one that finds a missed month bills it on the next tick. The
period arithmetic in ``rentals`` is what makes running daily correct — it bills
by period boundary, not by "today is the first".
"""

from loguru import logger

from api.services.billing import rentals
from api.services.telephony import number_lifecycle


async def charge_recurring_rentals(_ctx) -> None:
    """Collect every rental period that has come due.

    Idempotent by construction: a period already billed collides on
    ``uq_recurring_charge_period`` and is skipped. Running this twice in a day,
    or re-running it over a day it already processed, charges nobody twice.
    """
    counters = await rentals.run_due_charges()
    if counters["failed"]:
        logger.warning(
            "{} rental charge(s) could not be collected. Accounts are moving "
            "through the dunning schedule; see services/billing/dunning.py",
            counters["failed"],
        )


async def reconcile_carrier_numbers(_ctx) -> None:
    """Diff our number inventory against the carrier's, and report drift.

    Reports only — it does not release or purchase anything. Both directions of
    drift can be caused by a partial failure elsewhere, and a job that
    automatically "corrected" them could release a number a customer is using
    because a carrier API call timed out.
    """
    reports = await number_lifecycle.reconcile_all()

    orphaned = [n for r in reports for n in r.at_carrier_only]
    missing = [n for r in reports for n in r.in_database_only]

    if orphaned:
        logger.error(
            "RECONCILIATION: {} number(s) are rented at the carrier with no "
            "active record here — we are paying for them and billing nobody: "
            "{}",
            len(orphaned),
            ", ".join(sorted(orphaned)[:20]),
        )
    if missing:
        logger.error(
            "RECONCILIATION: {} number(s) are active here but not at the "
            "carrier — inbound calls to them will fail: {}",
            len(missing),
            ", ".join(sorted(missing)[:20]),
        )

    failed = [r for r in reports if r.error]
    if failed:
        logger.warning(
            "RECONCILIATION: {} carrier account(s) could not be read; their "
            "numbers were not checked this run",
            len(failed),
        )

    if reports and not orphaned and not missing and not failed:
        logger.info(
            "RECONCILIATION: {} carrier account(s) agree with our inventory",
            len(reports),
        )
