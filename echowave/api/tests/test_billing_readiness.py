"""Whether this deployment can take money correctly, as opposed to whether the
billing code is correct.

Every other billing test checks that a calculation is right. These check that
the settings the calculation depends on are actually present in *this*
environment, which is a different question and the one that was answered wrong
on a real deployment: a signature-verified payment was credited to the ledger
and issued zero tax documents, because two environment variables were unset.

Two properties matter more than any individual check:

* **A fresh install must not read as ready.** No payment has been captured, so
  the issuing path is unproven. Reporting a pass on absence of evidence is the
  specific failure this module exists to prevent.
* **The evidence checks must catch what the configuration checks cannot.**
  Setting the supplier identity today does nothing about the payments already
  taken without one. Those are an accrued liability, and only a query finds
  them.
"""

from datetime import UTC, datetime

import pytest

from api.db.models import (
    OrganizationModel,
    PaymentModel,
    ProviderRateModel,
    TaxDocumentModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.services.billing import readiness
from api.services.billing.readiness import (
    ACTION_REQUIRED,
    READY,
    UNKNOWN,
    assess,
)

CONFIGURED = {
    "SUPPLIER_LEGAL_NAME": "NAUTOMATION LABS PRIVATE LIMITED",
    "SUPPLIER_GSTIN": "33AALCN7211L1ZB",
    "SUPPLIER_STATE_CODE": "33",
    "SUPPLIER_ADDRESS": "No.86/16, Hosur 635109, Tamil Nadu",
    "RAZORPAY_WEBHOOK_SECRET": "whsec_test",
}


def _by_key(assessment):
    return {check.key: check for check in assessment.checks}


@pytest.fixture
def configured(monkeypatch):
    """A deployment with the supplier identity and webhook secret set."""
    for name, value in CONFIGURED.items():
        monkeypatch.setattr(readiness, name, value)


@pytest.fixture
def unconfigured(monkeypatch):
    """The state a fresh .env is actually in: nothing set."""
    for name in CONFIGURED:
        monkeypatch.setattr(
            readiness, name, "" if name != "RAZORPAY_WEBHOOK_SECRET" else None
        )


async def _org(async_session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


async def _paid_payment(async_session, org, *, order: str) -> PaymentModel:
    payment = PaymentModel(
        organization_id=org.id,
        order_id=order,
        payment_id=f"pay_{order}",
        amount_paise=100_000,
        gross_paise=118_000,
        status="paid",
        paid_at=datetime.now(UTC),
    )
    async_session.add(payment)
    await async_session.flush()
    return payment


def _document(payment, *, kind: str, number: str) -> TaxDocumentModel:
    """A minimally valid issued document against a payment.

    The money columns are NOT NULL, so they are filled with the figures a
    ₹1,180 inter-state advance would actually produce rather than zeros — a
    voucher that says nothing was taxable would not have been issued.
    """
    return TaxDocumentModel(
        organization_id=payment.organization_id,
        kind=kind,
        number=number,
        financial_year="26-27",
        taxable_paise=100_000,
        igst_paise=18_000,
        total_paise=118_000,
        supply_type="inter_state",
        place_of_supply="36",
        rate_basis_points=1800,
        payment_id=payment.id,
    )


async def _voucher_for(async_session, payment, *, number: str) -> TaxDocumentModel:
    document = _document(payment, kind="receipt_voucher", number=number)
    async_session.add(document)
    await async_session.flush()
    return document


@pytest.mark.asyncio
class TestSupplierIdentity:
    """The misconfiguration that takes money and issues no tax document.

    It is worth testing precisely because nothing fails when it is wrong: the
    payment succeeds, the ledger is credited, and the only trace is a log line.
    """

    async def test_an_unset_supplier_identity_is_action_required(
        self, async_session, unconfigured
    ):
        checks = _by_key(await assess(async_session))
        assert checks["supplier_identity"].status == ACTION_REQUIRED

    async def test_it_says_that_payments_will_still_be_taken(
        self, async_session, unconfigured
    ):
        """The detail has to convey that this is not an outage.

        An operator who reads "billing is broken" will investigate. One who
        reads nothing at all takes money for a month.
        """
        checks = _by_key(await assess(async_session))
        detail = checks["supplier_identity"].detail.lower()
        assert "credited" in detail
        assert "no receipt voucher" in detail

    async def test_a_configured_identity_is_ready(self, async_session, configured):
        checks = _by_key(await assess(async_session))
        assert checks["supplier_identity"].status == READY

    async def test_a_name_without_a_gstin_cannot_issue(
        self, async_session, configured, monkeypatch
    ):
        """Both are required — the issuing path gates on the pair."""
        monkeypatch.setattr(readiness, "SUPPLIER_GSTIN", "")
        checks = _by_key(await assess(async_session))
        assert checks["supplier_identity"].status == ACTION_REQUIRED

    async def test_a_missing_state_code_is_reported_separately(
        self, async_session, configured, monkeypatch
    ):
        """A document can be issued without it, and will be misclassified.

        Place of supply decides CGST+SGST versus IGST, so this is wrong output
        rather than absent output — a different failure from having no identity
        at all, and it needs its own line.
        """
        monkeypatch.setattr(readiness, "SUPPLIER_STATE_CODE", "")
        checks = _by_key(await assess(async_session))
        assert checks["supplier_identity"].status == READY
        assert checks["supplier_place_of_supply"].status == ACTION_REQUIRED

    async def test_place_of_supply_is_not_reported_when_nothing_can_issue(
        self, async_session, unconfigured
    ):
        """Noise on top of the real finding helps nobody."""
        checks = _by_key(await assess(async_session))
        assert "supplier_place_of_supply" not in checks


@pytest.mark.asyncio
class TestPaymentEvidence:
    """Configuration says the next payment will be documented. This says
    whether the ones already taken were."""

    async def test_a_fresh_deployment_is_unknown_not_ready(
        self, async_session, configured
    ):
        """The whole point. Nothing has been captured, so the path is unproven,
        and 'no failures yet' is not the same as 'works'."""
        checks = _by_key(await assess(async_session))
        assert checks["payments_have_vouchers"].status == UNKNOWN

    async def test_a_captured_payment_without_a_voucher_is_action_required(
        self, async_session, configured
    ):
        org = await _org(async_session, "no-voucher")
        await _paid_payment(async_session, org, order="order_1")

        checks = _by_key(await assess(async_session))
        assert checks["payments_have_vouchers"].status == ACTION_REQUIRED

    async def test_a_captured_payment_with_a_voucher_is_ready(
        self, async_session, configured
    ):
        org = await _org(async_session, "with-voucher")
        payment = await _paid_payment(async_session, org, order="order_2")
        await _voucher_for(async_session, payment, number="RV/26-27/000001")

        checks = _by_key(await assess(async_session))
        assert checks["payments_have_vouchers"].status == READY

    async def test_it_counts_the_undocumented_payments(self, async_session, configured):
        """The count is the accrued liability, so it has to be right rather
        than merely non-zero."""
        org = await _org(async_session, "mixed")
        documented = await _paid_payment(async_session, org, order="order_3")
        await _voucher_for(async_session, documented, number="RV/26-27/000002")
        await _paid_payment(async_session, org, order="order_4")
        await _paid_payment(async_session, org, order="order_5")

        checks = _by_key(await assess(async_session))
        assert checks["payments_have_vouchers"].status == ACTION_REQUIRED
        assert "2 of 3" in checks["payments_have_vouchers"].detail

    async def test_an_unpaid_order_is_not_owed_a_voucher(
        self, async_session, configured
    ):
        """A row is created when the order is, before the customer pays. Only a
        captured payment is an advance, so counting created orders here would
        report a liability that does not exist."""
        org = await _org(async_session, "unpaid")
        async_session.add(
            PaymentModel(
                organization_id=org.id,
                order_id="order_6",
                amount_paise=100_000,
                status="created",
            )
        )
        await async_session.flush()

        checks = _by_key(await assess(async_session))
        assert checks["payments_have_vouchers"].status == UNKNOWN

    async def test_a_tax_invoice_does_not_satisfy_the_receipt_voucher_check(
        self, async_session, configured
    ):
        """Different documents discharging different obligations. A monthly
        invoice does not acknowledge the advance that preceded it."""
        org = await _org(async_session, "wrong-kind")
        payment = await _paid_payment(async_session, org, order="order_7")
        async_session.add(
            _document(payment, kind="tax_invoice", number="INV/26-27/000001")
        )
        await async_session.flush()

        checks = _by_key(await assess(async_session))
        assert checks["payments_have_vouchers"].status == ACTION_REQUIRED


@pytest.mark.asyncio
class TestPriceBook:
    """The misconfiguration that reports 100% margin instead of an error."""

    async def test_an_empty_price_book_is_action_required(
        self, async_session, configured
    ):
        checks = _by_key(await assess(async_session))
        assert checks["price_book_seeded"].status == ACTION_REQUIRED

    async def test_it_says_the_margin_will_read_wrong_rather_than_fail(
        self, async_session, configured
    ):
        checks = _by_key(await assess(async_session))
        assert "100% margin" in checks["price_book_seeded"].detail

    async def test_a_seeded_price_book_is_ready(self, async_session, configured):
        async_session.add(
            ProviderRateModel(
                provider="sarvam",
                model="",
                component="stt",
                unit="minute",
                rate_mpaise=50_000,
                effective_from=datetime.now(UTC),
            )
        )
        await async_session.flush()

        checks = _by_key(await assess(async_session))
        assert checks["price_book_seeded"].status == READY

    async def test_a_superseded_rate_does_not_count_as_being_on_file(
        self, async_session, configured
    ):
        """Rates are closed rather than deleted, so a price book that has been
        fully retired still has rows in it. Counting those would report a
        deployment as priced when nothing is in force."""
        now = datetime.now(UTC)
        async_session.add(
            ProviderRateModel(
                provider="plivo",
                model="",
                component="telephony",
                unit="minute",
                rate_mpaise=3_000,
                effective_from=now,
                effective_to=now,
            )
        )
        await async_session.flush()

        checks = _by_key(await assess(async_session))
        assert checks["price_book_seeded"].status == ACTION_REQUIRED


@pytest.mark.asyncio
class TestUncostedCalls:
    """Rates can be on file and still not cover what was used."""

    async def _run(self, async_session, slug, *, uncosted, costed=True):
        user = UserModel(provider_id=f"user-{slug}")
        org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
        async_session.add_all([user, org])
        await async_session.flush()
        workflow = WorkflowModel(
            name=f"wf-{slug}",
            user_id=user.id,
            organization_id=org.id,
            workflow_definition={},
            template_context_variables={},
            call_disposition_codes={},
        )
        async_session.add(workflow)
        await async_session.flush()
        run = WorkflowRunModel(
            name="run",
            workflow_id=workflow.id,
            mode="twilio",
            usage_info={},
            cost_info={},
            initial_context={},
            gathered_context={},
            is_completed=True,
            costed_at=datetime.now(UTC) if costed else None,
            uncosted_usage=uncosted,
        )
        async_session.add(run)
        await async_session.flush()
        return run

    async def test_no_costed_calls_is_unknown(self, async_session, configured):
        checks = _by_key(await assess(async_session))
        assert checks["calls_fully_costed"].status == UNKNOWN

    async def test_a_fully_costed_call_is_ready(self, async_session, configured):
        await self._run(async_session, "clean", uncosted=[])
        checks = _by_key(await assess(async_session))
        assert checks["calls_fully_costed"].status == READY

    async def test_a_call_with_unpriced_usage_is_action_required(
        self, async_session, configured
    ):
        await self._run(async_session, "unpriced", uncosted=["llm:openai/gpt-5"])
        checks = _by_key(await assess(async_session))
        assert checks["calls_fully_costed"].status == ACTION_REQUIRED

    async def test_it_says_margin_is_overstated_rather_than_that_a_call_failed(
        self, async_session, configured
    ):
        """The call was fine. The number derived from it is not."""
        await self._run(async_session, "overstated", uncosted=["tts:sarvam"])
        checks = _by_key(await assess(async_session))
        assert "overstated" in checks["calls_fully_costed"].detail

    async def test_an_uncosted_call_is_not_counted_as_missing_a_rate(
        self, async_session, configured
    ):
        """A call the cost engine has not reached yet says nothing about the
        price book, and reporting it here would make a costing backlog look
        like a pricing gap."""
        await self._run(async_session, "pending", uncosted=None, costed=False)
        checks = _by_key(await assess(async_session))
        assert checks["calls_fully_costed"].status == UNKNOWN


@pytest.mark.asyncio
class TestTheWholeAssessment:
    async def test_a_fresh_unconfigured_deployment_has_blocking_findings(
        self, async_session, unconfigured
    ):
        assessment = await assess(async_session)
        assert len(assessment.blocking) > 0

    async def test_every_unmet_check_says_what_to_do(self, async_session, unconfigured):
        """A finding an operator cannot act on is a complaint, not a check."""
        assessment = await assess(async_session)
        for check in assessment.blocking:
            assert check.remedy, f"{check.key} has no remedy"

    async def test_every_finding_carries_a_reference(self, async_session, unconfigured):
        """So a reviewer can follow the obligation up rather than trust us."""
        assessment = await assess(async_session)
        for check in assessment.checks:
            assert check.reference, f"{check.key} has no reference"

    async def test_a_configured_deployment_with_evidence_has_nothing_blocking(
        self, async_session, configured
    ):
        org = await _org(async_session, "complete")
        payment = await _paid_payment(async_session, org, order="order_ok")
        await _voucher_for(async_session, payment, number="RV/26-27/000009")
        async_session.add(
            ProviderRateModel(
                provider="sarvam",
                model="",
                component="tts",
                unit="1k_chars",
                rate_mpaise=300_000,
                effective_from=datetime.now(UTC),
            )
        )
        await async_session.flush()

        assessment = await assess(async_session)
        assert assessment.blocking == ()
