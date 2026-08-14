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
    PUBLIC_BASE_URL,
    RAZORPAY_KEY_ID,
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
    NEEDS_A_HUMAN,
    READY,
    UNKNOWN,
    Check,
    Readiness,
)

#: The path Razorpay posts to. Duplicated from routes/payments.py rather than
#: imported, because importing the router to read one string pulls the whole
#: payment path into a read-only check.
WEBHOOK_PATH = "/api/v1/billing/razorpay/webhook"

#: Razorpay key prefixes. The distinction is the whole of this check: test keys
#: work perfectly, produce orders, fire webhooks, and take no money.
LIVE_KEY_PREFIX = "rzp_live_"
TEST_KEY_PREFIX = "rzp_test_"

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


def _key_mode_check() -> Check:
    """Whether the keys on this box can take real money.

    A test key is not a broken key. It creates orders, renders a checkout, and
    fires signature-valid webhooks that credit the ledger — the entire path
    passes with no money having moved. That is why this is a check and not a
    line in a runbook: a deployment can look completely healthy, with credited
    accounts and issued vouchers, and be taking nothing.
    """
    key = (RAZORPAY_KEY_ID or "").strip()

    if not key:
        return Check(
            key="razorpay_key_mode",
            title="Razorpay keys are live keys",
            status=ACTION_REQUIRED,
            detail=(
                "RAZORPAY_KEY_ID is unset, so no order can be created and "
                "nobody can buy credit on this deployment."
            ),
            reference="DEPLOY-ENV.md — payments",
            remedy=(
                "Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET from the Razorpay "
                "dashboard, using the Live keys once your account is activated."
            ),
        )

    if key.startswith(TEST_KEY_PREFIX):
        return Check(
            key="razorpay_key_mode",
            title="Razorpay keys are live keys",
            status=ACTION_REQUIRED,
            detail=(
                "This deployment is configured with Razorpay **test** keys. "
                "Checkout will work, webhooks will verify, and credit will "
                "land in the ledger — and no money will be collected for any "
                "of it."
            ),
            reference="DEPLOY-ENV.md — payments",
            remedy=(
                "Activate the Razorpay account, then replace RAZORPAY_KEY_ID, "
                "RAZORPAY_KEY_SECRET and RAZORPAY_WEBHOOK_SECRET with the Live "
                "values. The webhook secret is per-mode: reusing the test one "
                "against live keys rejects every webhook."
            ),
        )

    if key.startswith(LIVE_KEY_PREFIX):
        return Check(
            key="razorpay_key_mode",
            title="Razorpay keys are live keys",
            status=READY,
            detail=f"Live keys are configured ({key[: len(LIVE_KEY_PREFIX) + 4]}…).",
            reference="DEPLOY-ENV.md — payments",
        )

    return Check(
        key="razorpay_key_mode",
        title="Razorpay keys are live keys",
        status=UNKNOWN,
        detail=(
            "RAZORPAY_KEY_ID does not start with rzp_live_ or rzp_test_, so "
            "which mode this deployment is in cannot be read from it."
        ),
        reference="DEPLOY-ENV.md — payments",
        remedy="Confirm in the Razorpay dashboard which mode these keys belong to.",
    )


async def _webhook_reachability_check(*, probe: bool) -> Check:
    """Whether Razorpay's servers can actually reach the endpoint that credits.

    This is the gap that stays invisible until the first real customer pays.
    Every other part of the payment path is exercised by tests; none of them
    can tell you that a load balancer forwards ``/api/v1/billing/razorpay/…``
    or that the URL registered in the dashboard is the one this deployment
    answers on. The failure mode is precisely the one the payment code was
    written to avoid: the customer is charged and nobody is credited.

    The probe posts a deliberately invalid signature to our own public URL and
    expects **400**. A 400 proves the request traversed DNS, TLS and the load
    balancer and was rejected by our signature check — which is the whole path
    Razorpay's POST takes, minus a valid signature. A 404 or a timeout means
    the route is not exposed where the dashboard is pointing.

    What it cannot prove is that the secret on this box matches the dashboard's,
    or that the dashboard is subscribed to ``payment.captured``. Those are
    reported separately as obligations rather than guessed at.
    """
    if not PUBLIC_BASE_URL:
        return Check(
            key="webhook_reachable",
            title="Razorpay can reach the webhook endpoint",
            status=ACTION_REQUIRED,
            detail=(
                "PUBLIC_BASE_URL is unset, so there is no public URL to "
                "register with Razorpay and none to test."
            ),
            reference="DEPLOY-ENV.md — PUBLIC_BASE_URL",
            remedy=(
                "Set PUBLIC_BASE_URL to the origin this deployment is reached "
                f"at, then register {{PUBLIC_BASE_URL}}{WEBHOOK_PATH} in the "
                "Razorpay dashboard."
            ),
        )

    url = f"{PUBLIC_BASE_URL.rstrip('/')}{WEBHOOK_PATH}"

    if not probe:
        return Check(
            key="webhook_reachable",
            title="Razorpay can reach the webhook endpoint",
            status=UNKNOWN,
            detail=(
                f"The endpoint should be registered as {url}. Not tested — "
                "reachability is only checked when explicitly asked for, "
                "because it makes an outbound request."
            ),
            reference="HANDOVER.md §3 rule 8 — only a verified webhook credits",
            remedy=(
                "Re-check with ?probe=true, or run: python -m "
                "scripts.verify_payment_round_trip"
            ),
        )

    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.post(
                url,
                content=b'{"event":"readiness.probe"}',
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": "readiness-probe-not-a-signature",
                },
            )
    except Exception as exc:
        return Check(
            key="webhook_reachable",
            title="Razorpay can reach the webhook endpoint",
            status=ACTION_REQUIRED,
            detail=(
                f"{url} could not be reached from this deployment: {exc}. "
                "Razorpay will fare no better, and a webhook it cannot deliver "
                "is a customer charged and not credited."
            ),
            reference="HANDOVER.md §3 rule 8",
            remedy=(
                "Check DNS, TLS and that the load balancer forwards "
                f"{WEBHOOK_PATH} to the api container."
            ),
        )

    if response.status_code == 400:
        return Check(
            key="webhook_reachable",
            title="Razorpay can reach the webhook endpoint",
            status=READY,
            detail=(
                f"{url} answered 400 to an unsigned probe — the route is "
                "publicly reachable and is rejecting bad signatures."
            ),
            reference="HANDOVER.md §3 rule 8",
        )

    if response.status_code == 404:
        return Check(
            key="webhook_reachable",
            title="Razorpay can reach the webhook endpoint",
            status=ACTION_REQUIRED,
            detail=(
                f"{url} returned 404. Whatever is serving that origin does not "
                "route the webhook path to this application."
            ),
            reference="HANDOVER.md §3 rule 8",
            remedy=(
                f"Check the reverse proxy forwards {WEBHOOK_PATH} to the api "
                "container, and that PUBLIC_BASE_URL names the origin Razorpay "
                "is configured to post to."
            ),
        )

    return Check(
        key="webhook_reachable",
        title="Razorpay can reach the webhook endpoint",
        status=ACTION_REQUIRED,
        detail=(
            f"{url} answered {response.status_code} to an unsigned probe. It "
            "should answer 400: anything else means the request is not "
            "arriving at the signature check."
        ),
        reference="HANDOVER.md §3 rule 8",
        remedy=(
            "A 5xx usually means the api container is not behind that origin; "
            "a 2xx means something is accepting unsigned webhooks, which is "
            "considerably worse and should be investigated immediately."
        ),
    )


def _round_trip_obligation() -> Check:
    """The one thing no check can discharge: somebody has to pay, once.

    Reported as ``needs_a_human`` rather than left out, because the shape of
    the remaining risk is exactly "everything is configured and nobody has
    tried it". The secret matching the dashboard's, the subscription to
    ``payment.captured``, the account being activated for live collection —
    each is invisible from inside the process, and each fails as a charge with
    no credit.
    """
    return Check(
        key="live_round_trip_rehearsed",
        title="A real payment has been carried through end to end",
        status=NEEDS_A_HUMAN,
        detail=(
            "Configuration cannot prove that the webhook secret on this box "
            "matches the one in the dashboard, that the dashboard is "
            "subscribed to payment.captured and payment.failed, or that the "
            "account is activated for live collection. Each of those fails as "
            "a customer charged and nobody credited."
        ),
        reference="PRODUCTION-CHECKLIST.md — before the first customer",
        remedy=(
            "Buy ₹1 of credit yourself, with live keys, from the real "
            "checkout. Then confirm three things: the payment reads 'paid' at "
            "GET /billing/payments, the credit appears in the ledger balance, "
            "and a numbered receipt voucher exists at GET /billing/documents. "
            "Run: python -m scripts.verify_payment_round_trip --order <id>"
        ),
    )


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


async def assess(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    probe_network: bool = False,
) -> Readiness:
    """Every billing check, in the order someone should work through them.

    ``probe_network`` makes the webhook reachability check actually reach out.
    Off by default: readiness is polled, and a check that opens a connection
    every time it is read is a check that gets switched off.
    """
    checks: list[Check] = []
    checks.extend(_supplier_checks())
    checks.append(_key_mode_check())
    checks.append(await _webhook_reachability_check(probe=probe_network))
    checks.append(await _worker_check())
    checks.extend(await _payment_evidence(session))
    checks.extend(await _price_book_evidence(session, now=now))
    checks.append(_round_trip_obligation())
    return Readiness(checks=tuple(checks))
