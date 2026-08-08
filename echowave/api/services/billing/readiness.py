"""Whether this deployment can actually take money correctly, right now.

Everything else in this package *does* the billing: payments credit, the cost
engine costs, documents get numbered. This module answers the question that
none of them can, because each is individually behaving correctly while the
deployment as a whole is losing money or accruing a tax liability:

**are the settings this code depends on actually present in this environment?**

Two misconfigurations here are worse than an outage, and both were reproduced
against a real deployment (see PRODUCTION-CHECKLIST.md §2). Neither raises:

1. **Unset supplier identity.** ``issue_receipt_voucher`` returns ``None``
   rather than raising when ``SUPPLIER_LEGAL_NAME`` / ``SUPPLIER_GSTIN`` are
   missing, and that is the right call — a captured payment must not be rolled
   back over an absent environment variable. The consequence is that money is
   taken, credit is issued, and **no tax document is ever produced**, for as
   long as nobody looks. Under GST an advance is taxable on receipt, so every
   such payment is an accumulating compliance failure with a log line as its
   only trace.

2. **An empty price book.** A call whose providers have no rate on file is
   recorded as *uncosted*, not free. The platform fee still bills, so the
   dashboard reports a plausible number — 100% margin — rather than an error.

The pattern both share is the one worth naming: **the dangerous failure is the
one that produces a plausible number rather than an exception.** So the checks
below are deliberately split between configuration (is the setting there) and
evidence (did the thing actually happen), and the evidence checks are the ones
that matter. A configured supplier identity proves nothing on its own; a
captured payment with a voucher against it proves the whole path works.

The headline evidence check is designed to read **zero**: captured payments
carrying no receipt voucher. Any other value is an incident, not a statistic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.constants import (
    RAZORPAY_WEBHOOK_SECRET,
    SUPPLIER_ADDRESS,
    SUPPLIER_GSTIN,
    SUPPLIER_LEGAL_NAME,
    SUPPLIER_STATE_CODE,
)
from api.db.models import (
    PaymentModel,
    ProviderRateModel,
    TaxDocumentModel,
    WorkflowRunModel,
)
from api.services.readiness import (
    ACTION_REQUIRED,
    READY,
    UNKNOWN,
    Check,
    Readiness,
)

#: Document kind that acknowledges an advance. Duplicated from
#: services/billing/documents.py rather than imported, because importing that
#: module pulls in the whole issuing path for what is a read-only check.
RECEIPT_VOUCHER = "receipt_voucher"


def _supplier_checks() -> list[Check]:
    """Can this deployment issue a tax document at all.

    ``SUPPLIER_LEGAL_NAME`` and ``SUPPLIER_GSTIN`` are the two the issuing path
    actually gates on (``documents.supplier_is_configured``). State code and
    address are reported separately: without them a document can still be
    issued, but it will be wrong — the state code is what decides CGST+SGST
    versus IGST, so an absent one silently misclassifies every supply.
    """
    checks: list[Check] = []

    can_issue = bool(SUPPLIER_LEGAL_NAME.strip() and SUPPLIER_GSTIN.strip())
    checks.append(
        Check(
            key="supplier_identity",
            title="Supplier identity is configured",
            status=READY if can_issue else ACTION_REQUIRED,
            detail=(
                f"Documents are issued as {SUPPLIER_LEGAL_NAME} ({SUPPLIER_GSTIN})."
                if can_issue
                else (
                    "SUPPLIER_LEGAL_NAME and SUPPLIER_GSTIN are not both set. "
                    "Payments will be captured and credited, and no receipt "
                    "voucher will be issued for any of them — silently, with "
                    "only a log line."
                )
            ),
            reference="CGST Act s31(3)(d) — receipt voucher on advance",
            remedy=""
            if can_issue
            else (
                "Set SUPPLIER_LEGAL_NAME and SUPPLIER_GSTIN in .env (quote any "
                "value containing a space or comma) and restart the api "
                "container. Then re-check that a test payment produces a "
                "numbered voucher at GET /billing/documents."
            ),
        )
    )

    # Only meaningful once we can issue at all; reporting "no state code" on a
    # deployment that issues nothing is noise on top of the real finding.
    if can_issue:
        if SUPPLIER_STATE_CODE.strip():
            checks.append(
                Check(
                    key="supplier_place_of_supply",
                    title="Supplier state code is set",
                    status=READY,
                    detail=(
                        f"State code {SUPPLIER_STATE_CODE} decides intra-state "
                        "(CGST+SGST) versus inter-state (IGST)."
                    ),
                    reference="IGST Act s7, s8 — place of supply",
                )
            )
        else:
            checks.append(
                Check(
                    key="supplier_place_of_supply",
                    title="Supplier state code is set",
                    status=ACTION_REQUIRED,
                    detail=(
                        "No state code, and none derivable from the GSTIN. Every "
                        "supply will be classified against an empty origin, so "
                        "the CGST/SGST versus IGST split on issued documents is "
                        "not trustworthy."
                    ),
                    reference="IGST Act s7, s8 — place of supply",
                    remedy="Set SUPPLIER_STATE_CODE to the first two digits of your GSTIN.",
                )
            )

        if not SUPPLIER_ADDRESS.strip():
            checks.append(
                Check(
                    key="supplier_address",
                    title="Supplier address is published on documents",
                    status=ACTION_REQUIRED,
                    detail="No address is set, so issued documents carry a blank where the statutory field goes.",
                    reference="CGST Rules r46 — particulars of a tax invoice",
                    remedy="Set SUPPLIER_ADDRESS in .env (quote it — it contains commas).",
                )
            )

    checks.append(
        Check(
            key="webhook_secret",
            title="Razorpay webhook signature secret is set",
            status=READY if RAZORPAY_WEBHOOK_SECRET else ACTION_REQUIRED,
            detail=(
                "Top-ups are credited only on a signature-verified webhook."
                if RAZORPAY_WEBHOOK_SECRET
                else (
                    "RAZORPAY_WEBHOOK_SECRET is unset, so top-ups are refused "
                    "outright. That is deliberate — the alternative is charging "
                    "a customer and crediting nobody — but it means nobody can "
                    "buy credit on this deployment."
                )
            ),
            reference="HANDOVER.md §3 rule 8 — only a verified webhook credits",
            remedy=""
            if RAZORPAY_WEBHOOK_SECRET
            else "Set RAZORPAY_WEBHOOK_SECRET to the secret configured on the Razorpay webhook.",
        )
    )

    return checks


async def _payment_evidence(session: AsyncSession) -> list[Check]:
    """The check that catches the silent voucher failure after it has happened.

    Configuration says whether the next payment will produce a document. This
    says whether the ones already taken did — which is the number that matters,
    because it is the accrued liability rather than the risk of one.
    """
    checks: list[Check] = []

    paid = (
        await session.execute(
            select(func.count())
            .select_from(PaymentModel)
            .where(PaymentModel.status == "paid")
        )
    ).scalar() or 0

    if not paid:
        checks.append(
            Check(
                key="payments_have_vouchers",
                title="Every captured payment has a receipt voucher",
                status=UNKNOWN,
                detail=(
                    "No payment has been captured yet, so the issuing path is "
                    "unproven. Expected on a new deployment."
                ),
                reference="CGST Act s31(3)(d)",
                remedy=(
                    "Make one small live top-up and re-check. This is the only "
                    "check that proves payment, credit and document issuance "
                    "work end to end."
                ),
            )
        )
        return checks

    # A LEFT JOIN rather than a NOT IN: payment_id is nullable on the document
    # side for older rows, and NOT IN over a set containing NULL matches nothing
    # at all — which would report every payment as documented.
    missing = (
        await session.execute(
            select(func.count())
            .select_from(PaymentModel)
            .outerjoin(
                TaxDocumentModel,
                (TaxDocumentModel.payment_id == PaymentModel.id)
                & (TaxDocumentModel.kind == RECEIPT_VOUCHER),
            )
            .where(
                PaymentModel.status == "paid",
                TaxDocumentModel.id.is_(None),
            )
        )
    ).scalar() or 0

    checks.append(
        Check(
            key="payments_have_vouchers",
            title="Every captured payment has a receipt voucher",
            status=READY if missing == 0 else ACTION_REQUIRED,
            detail=(
                f"All {paid} captured payments carry a numbered receipt voucher."
                if missing == 0
                else (
                    f"{missing} of {paid} captured payments have no receipt "
                    "voucher. Each is an advance that was taxable on receipt and "
                    "for which no document was issued."
                )
            ),
            reference="CGST Act s31(3)(d) — receipt voucher on advance",
            remedy=""
            if missing == 0
            else (
                "Set the supplier identity variables, then issue the missing "
                "vouchers for the affected payments. Check the api logs for "
                "'no receipt voucher issued' to see how far back it goes."
            ),
        )
    )

    return checks


async def _price_book_evidence(
    session: AsyncSession, *, now: datetime | None = None
) -> list[Check]:
    """Whether there is anything to cost a call against.

    The failure this catches does not look like a failure: with no rates on
    file every call still bills its platform fee, so revenue looks right and
    provider cost reads zero. Margin is then reported as 100%, and a
    misconfiguration is indistinguishable from a very good month.
    """
    now = now or datetime.now(UTC)
    checks: list[Check] = []

    live_rates = (
        await session.execute(
            select(func.count())
            .select_from(ProviderRateModel)
            .where(ProviderRateModel.effective_to.is_(None))
        )
    ).scalar() or 0

    checks.append(
        Check(
            key="price_book_seeded",
            title="Provider rates are on file",
            status=READY if live_rates else ACTION_REQUIRED,
            detail=(
                f"{live_rates} provider rates are in force."
                if live_rates
                else (
                    "The price book is empty. Every call will be recorded as "
                    "uncosted, cost estimates will return only the platform fee "
                    "as though it were the whole price, and the dashboard will "
                    "report 100% margin rather than an error."
                )
            ),
            reference="HANDOVER.md §3 rule 6 — usage with no rate is uncosted, not free",
            remedy=""
            if live_rates
            else (
                "Run: docker compose exec api python -m scripts.seed_provider_rates "
                "--confirm — then correct any rate you have negotiated separately "
                "at /superadmin/billing/rate-card."
            ),
        )
    )

    # Evidence, as opposed to the configuration check above: rates can be
    # present and still not cover what is actually being used. uncosted_usage
    # is written per call, and SQL NULL means "costed before we tracked this",
    # which is why the filter is on a non-empty JSON array rather than on
    # NOT NULL.
    uncosted = (
        await session.execute(
            select(func.count())
            .select_from(WorkflowRunModel)
            .where(
                WorkflowRunModel.costed_at.isnot(None),
                WorkflowRunModel.uncosted_usage.isnot(None),
                func.json_array_length(WorkflowRunModel.uncosted_usage) > 0,
            )
        )
    ).scalar() or 0

    costed = (
        await session.execute(
            select(func.count())
            .select_from(WorkflowRunModel)
            .where(WorkflowRunModel.costed_at.isnot(None))
        )
    ).scalar() or 0

    if not costed:
        checks.append(
            Check(
                key="calls_fully_costed",
                title="Costed calls have a rate for every component",
                status=UNKNOWN,
                detail="No call has been costed yet, so no margin figure has been produced.",
                reference="HANDOVER.md §3 rule 6",
                remedy="Place one call and re-check once it has been costed.",
            )
        )
    else:
        checks.append(
            Check(
                key="calls_fully_costed",
                title="Costed calls have a rate for every component",
                status=READY if uncosted == 0 else ACTION_REQUIRED,
                detail=(
                    f"All {costed} costed calls had a rate on file for every component."
                    if uncosted == 0
                    else (
                        f"{uncosted} of {costed} costed calls used a provider we "
                        "hold no rate for. That cost is real money we paid and "
                        "did not record, so reported margin is overstated."
                    )
                ),
                reference="HANDOVER.md §3 rule 6",
                remedy=""
                if uncosted == 0
                else (
                    "Read the unpriced providers off /admin/billing/unit-economics "
                    "and add a rate for each at /superadmin/billing/rate-card."
                ),
            )
        )

    return checks


async def _worker_check() -> Check:
    """The dependency that makes every other billing number stale.

    Costing, rollup refresh and monthly invoicing are all ARQ jobs. When that
    worker dies the API keeps answering and the dashboard keeps serving the
    last figures it had, so this belongs in the billing assessment rather than
    only in the health endpoint: without it, a deployment whose worker stopped
    yesterday reports billing as ready.
    """
    from api.services.worker_health import worker_health

    health = await worker_health()
    alive = health.get("alive")

    if alive is True:
        status, remedy = READY, ""
    elif alive is False:
        status, remedy = (
            ACTION_REQUIRED,
            (
                "Restart the api container and confirm the ARQ worker starts. "
                "Until it does, completed calls are not being costed and the "
                "monthly invoice run will not happen."
            ),
        )
    else:
        status, remedy = (
            UNKNOWN,
            "Check Redis is reachable and that the worker has started at least once.",
        )

    return Check(
        key="background_worker_alive",
        title="The background worker is running",
        status=status,
        detail=health.get("detail", ""),
        reference="HANDOVER.md §8 — one container runs everything",
        remedy=remedy,
    )


async def assess(session: AsyncSession, *, now: datetime | None = None) -> Readiness:
    """Every billing check, in the order someone should work through them."""
    checks: list[Check] = []
    checks.extend(_supplier_checks())
    checks.append(await _worker_check())
    checks.extend(await _payment_evidence(session))
    checks.extend(await _price_book_evidence(session, now=now))
    return Readiness(checks=tuple(checks))
