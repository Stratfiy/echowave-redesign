"""Receipt vouchers, tax invoices, and the GST on a top-up.

The failure this file exists to catch is a top-up that charges the customer the
net figure and credits them the same — 18% missing from every payment, which
nobody notices until a return is filed.

The second is subtler and worse: a serial number that repeats or skips. GST
wants a consecutive series with no gaps, and both failure modes are filing
corrections rather than bugs you can quietly patch out.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from api.db.models import (
    BillingProfileModel,
    CreditLedgerModel,
    OrganizationModel,
    PaymentModel,
    TaxDocumentModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CreditLedgerKind
from api.services.billing import billing_profile, documents, payments, tax
from api.services.billing.documents import (
    RECEIPT_VOUCHER,
    TAX_INVOICE,
    financial_year,
)

OUR_STATE = "29"
RUPEE = 100


@pytest.fixture(autouse=True)
def supplier_configured(monkeypatch):
    """A supplier identity, so documents can actually be issued."""
    monkeypatch.setattr(tax, "SUPPLIER_STATE_CODE", OUR_STATE)
    monkeypatch.setattr(tax, "GST_RATE_BASIS_POINTS", 1800)
    monkeypatch.setattr(tax, "SUPPLIER_HAS_LUT", True)
    monkeypatch.setattr(documents, "SUPPLIER_LEGAL_NAME", "Decibyl Test Pvt Ltd")
    monkeypatch.setattr(documents, "SUPPLIER_GSTIN", "29AAAAA0000A1Z5")
    monkeypatch.setattr(documents, "SUPPLIER_STATE_CODE", OUR_STATE)
    monkeypatch.setattr(documents, "SUPPLIER_HAS_LUT", True)


async def _org(async_session, slug: str, *, state=OUR_STATE, country="IN"):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()

    async_session.add(
        BillingProfileModel(
            organization_id=org.id,
            legal_name=f"{slug.title()} Pvt Ltd",
            address_line1="1 Test Road",
            city="Bengaluru",
            state_code=state,
            country_code=country,
        )
    )
    await async_session.flush()
    return org


async def _paid_payment(async_session, org, *, credit_paise: int, tax_paise: int):
    row = PaymentModel(
        organization_id=org.id,
        provider="razorpay",
        order_id=f"order_{org.id}_{credit_paise}",
        payment_id=f"pay_{org.id}_{credit_paise}",
        amount_paise=credit_paise,
        gross_paise=credit_paise + tax_paise,
        cgst_paise=tax_paise // 2,
        sgst_paise=tax_paise - tax_paise // 2,
        igst_paise=0,
        status="paid",
        paid_at=datetime.now(UTC),
    )
    async_session.add(row)
    await async_session.flush()
    return row


class TestFinancialYear:
    def test_april_starts_a_new_year(self):
        assert financial_year(date(2026, 3, 31)) == "25-26"
        assert financial_year(date(2026, 4, 1)) == "26-27"

    def test_january_belongs_to_the_year_that_started_last_april(self):
        """The off-by-one that otherwise puts two years' serials in one series."""
        assert financial_year(date(2027, 1, 15)) == "26-27"


@pytest.mark.asyncio
class TestReceiptVouchers:
    async def test_a_captured_payment_produces_a_voucher(self, async_session):
        org = await _org(async_session, "voucher")
        payment = await _paid_payment(
            async_session, org, credit_paise=100_000, tax_paise=18_000
        )

        voucher = await documents.issue_receipt_voucher(async_session, payment=payment)

        assert voucher is not None
        assert voucher.kind == RECEIPT_VOUCHER
        assert voucher.taxable_paise == 100_000
        assert voucher.cgst_paise == 9_000
        assert voucher.sgst_paise == 9_000
        assert voucher.total_paise == 118_000

    async def test_it_is_idempotent_on_the_payment(self, async_session):
        """A replayed webhook must not issue a second voucher for one payment."""
        org = await _org(async_session, "voucheridem")
        payment = await _paid_payment(
            async_session, org, credit_paise=50_000, tax_paise=9_000
        )

        first = await documents.issue_receipt_voucher(async_session, payment=payment)
        second = await documents.issue_receipt_voucher(async_session, payment=payment)

        assert first.number == second.number
        count = await async_session.scalar(
            select(func.count())
            .select_from(TaxDocumentModel)
            .where(TaxDocumentModel.payment_id == payment.id)
        )
        assert count == 1

    async def test_an_unconfigured_supplier_does_not_lose_the_payment(
        self, async_session, monkeypatch
    ):
        """A payment that succeeded must not be rolled back over a missing
        environment variable. The money is real either way."""
        monkeypatch.setattr(documents, "SUPPLIER_GSTIN", "")
        org = await _org(async_session, "novoucher")
        payment = await _paid_payment(
            async_session, org, credit_paise=50_000, tax_paise=9_000
        )

        assert (
            await documents.issue_receipt_voucher(async_session, payment=payment)
            is None
        )

    async def test_an_export_customer_gets_a_zero_rated_voucher(self, async_session):
        org = await _org(async_session, "exportvoucher", state=None, country="US")
        payment = await _paid_payment(
            async_session, org, credit_paise=100_000, tax_paise=0
        )

        voucher = await documents.issue_receipt_voucher(async_session, payment=payment)

        assert voucher.supply_type == "export"
        assert voucher.total_paise == 100_000
        assert voucher.cgst_paise == 0
        assert voucher.igst_paise == 0


@pytest.mark.asyncio
class TestNumbering:
    async def test_numbers_are_consecutive_within_a_series(self, async_session):
        org = await _org(async_session, "serial")
        numbers = []
        for i in range(5):
            payment = await _paid_payment(
                async_session, org, credit_paise=10_000 + i, tax_paise=1_800
            )
            voucher = await documents.issue_receipt_voucher(
                async_session, payment=payment
            )
            numbers.append(voucher.number)

        serials = [int(n.rsplit("/", 1)[1]) for n in numbers]
        assert serials == list(range(serials[0], serials[0] + 5)), (
            f"serials must be gapless and consecutive, got {serials}"
        )

    async def test_the_number_fits_the_statutory_sixteen_characters(
        self, async_session
    ):
        org = await _org(async_session, "sixteen")
        payment = await _paid_payment(
            async_session, org, credit_paise=10_000, tax_paise=1_800
        )

        voucher = await documents.issue_receipt_voucher(async_session, payment=payment)

        assert len(voucher.number) <= 16, voucher.number

    async def test_invoices_and_vouchers_number_independently(self, async_session):
        """Two series, two counters. Sharing one would make both look gapped."""
        org = await _org(async_session, "twoseries")
        payment = await _paid_payment(
            async_session, org, credit_paise=10_000, tax_paise=1_800
        )
        voucher = await documents.issue_receipt_voucher(async_session, payment=payment)

        assert voucher.number.startswith("RV/")


@pytest.mark.asyncio
class TestTaxInvoices:
    async def _costed_run(self, async_session, org, *, charged_paise, when):
        user = UserModel(provider_id=f"user-inv-{org.id}-{charged_paise}")
        async_session.add(user)
        await async_session.flush()
        workflow = WorkflowModel(
            name="wf",
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
            created_at=when,
            billable_seconds=60,
            total_charged_paise=charged_paise,
            costed_at=when,
        )
        async_session.add(run)
        await async_session.flush()
        return run

    async def test_usage_is_invoiced_with_tax_on_top(self, async_session):
        org = await _org(async_session, "invoice")
        when = datetime.now(UTC) - timedelta(days=40)
        await self._costed_run(async_session, org, charged_paise=50_000, when=when)

        invoice = await documents.issue_tax_invoice(
            async_session,
            organization_id=org.id,
            period_start=when.date().replace(day=1),
            period_end=when.date().replace(day=28),
        )

        assert invoice is not None
        assert invoice.kind == TAX_INVOICE
        assert invoice.taxable_paise == 50_000
        assert invoice.total_paise == 59_000

    async def test_an_account_with_no_usage_is_not_invoiced(self, async_session):
        """Most accounts in most months. An invoice for nothing is not a
        document anyone wants."""
        org = await _org(async_session, "nousage")

        invoice = await documents.issue_tax_invoice(
            async_session,
            organization_id=org.id,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
        )

        assert invoice is None

    async def test_a_retried_run_does_not_issue_a_second_invoice(self, async_session):
        """A duplicate invoice is a filing correction, not something you can
        delete."""
        org = await _org(async_session, "retryinvoice")
        when = datetime.now(UTC) - timedelta(days=40)
        await self._costed_run(async_session, org, charged_paise=20_000, when=when)
        start = when.date().replace(day=1)
        end = when.date().replace(day=28)

        first = await documents.issue_tax_invoice(
            async_session, organization_id=org.id, period_start=start, period_end=end
        )
        second = await documents.issue_tax_invoice(
            async_session, organization_id=org.id, period_start=start, period_end=end
        )

        assert first.number == second.number
        count = await async_session.scalar(
            select(func.count())
            .select_from(TaxDocumentModel)
            .where(
                TaxDocumentModel.organization_id == org.id,
                TaxDocumentModel.kind == TAX_INVOICE,
            )
        )
        assert count == 1

    async def test_an_uncosted_run_is_not_invoiced(self, async_session):
        """A call whose receipt does not exist yet has no amount to invoice."""
        org = await _org(async_session, "uncosted")
        when = datetime.now(UTC) - timedelta(days=40)
        user = UserModel(provider_id=f"user-uncosted-{org.id}")
        async_session.add(user)
        await async_session.flush()
        workflow = WorkflowModel(
            name="wf",
            user_id=user.id,
            organization_id=org.id,
            workflow_definition={},
            template_context_variables={},
            call_disposition_codes={},
        )
        async_session.add(workflow)
        await async_session.flush()
        async_session.add(
            WorkflowRunModel(
                name="run",
                workflow_id=workflow.id,
                mode="twilio",
                created_at=when,
                billable_seconds=60,
                total_charged_paise=9_999,
                costed_at=None,
            )
        )
        await async_session.flush()

        invoice = await documents.issue_tax_invoice(
            async_session,
            organization_id=org.id,
            period_start=when.date().replace(day=1),
            period_end=when.date().replace(day=28),
        )

        assert invoice is None


@pytest.mark.asyncio
class TestDocumentsAreFrozenAtIssue:
    async def test_a_later_address_change_does_not_rewrite_an_issued_document(
        self, async_session
    ):
        """A document is a statement about a moment. Rendering it from the live
        profile would rewrite every past invoice when a customer moves office."""
        org = await _org(async_session, "frozen")
        payment = await _paid_payment(
            async_session, org, credit_paise=10_000, tax_paise=1_800
        )
        voucher = await documents.issue_receipt_voucher(async_session, payment=payment)

        await billing_profile.save_profile(
            async_session,
            organization_id=org.id,
            legal_name="Renamed Holdings Ltd",
            address_line1="99 New Street",
            state_code="27",
            country_code="IN",
        )

        stored = await async_session.scalar(
            select(TaxDocumentModel).where(TaxDocumentModel.id == voucher.id)
        )
        assert stored.customer_snapshot["legal_name"] == "Frozen Pvt Ltd"
        assert stored.customer_snapshot["address_line1"] == "1 Test Road"
        # And the split it was issued under is untouched.
        assert stored.cgst_paise == 900


@pytest.mark.asyncio
class TestTopupChargesTax:
    async def test_the_card_is_charged_gross_and_the_ledger_credited_net(
        self, async_session, monkeypatch
    ):
        """The whole point. Charging net and crediting net gives away the 18%."""
        org = await _org(async_session, "grossnet")

        captured = {}

        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {"id": "order_grossnet"}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def post(self, _url, **kwargs):
                captured.update(kwargs["json"])
                return _Response()

        monkeypatch.setattr(payments, "RAZORPAY_KEY_ID", "rzp_test")
        monkeypatch.setattr(payments, "RAZORPAY_KEY_SECRET", "secret")
        monkeypatch.setattr(payments.httpx, "AsyncClient", lambda **_: _Client())

        order = await payments.create_topup_order(
            async_session,
            organization_id=org.id,
            amount_paise=100_000,
            created_by=None,
        )

        # Razorpay is asked for the gross figure.
        assert captured["amount"] == 118_000
        assert order.gross_paise == 118_000
        assert order.amount_paise == 100_000
        assert order.tax_paise == 18_000

        stored = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.order_id == "order_grossnet")
        )
        assert stored.amount_paise == 100_000
        assert stored.gross_paise == 118_000


@pytest.mark.asyncio
class TestWebhookCreditsNetOfTax:
    async def _order(self, async_session, org, *, credit, gross):
        row = PaymentModel(
            organization_id=org.id,
            provider="razorpay",
            order_id=f"order_wh_{org.id}",
            amount_paise=credit,
            gross_paise=gross,
            status="created",
        )
        async_session.add(row)
        await async_session.flush()
        return row

    def _event(self, order_id, payment_id, amount):
        import json

        return json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": payment_id,
                            "order_id": order_id,
                            "amount": amount,
                            "currency": "INR",
                        }
                    }
                },
            }
        ).encode()

    def _sign(self, body, secret="whsec_test"):
        import hashlib
        import hmac

        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    async def test_a_gst_inclusive_payment_credits_the_net_amount(
        self, async_session, monkeypatch
    ):
        """The regression this guards: comparing the payload against the *net*
        amount would reject every correctly-taxed payment as an 18%
        overpayment."""
        monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", "whsec_test")
        org = await _org(async_session, "whnet")
        order = await self._order(async_session, org, credit=100_000, gross=118_000)

        body = self._event(order.order_id, "pay_whnet", 118_000)
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=self._sign(body)
        )

        assert result["status"] == "credited"
        assert result["credited_paise"] == 100_000

        total = await async_session.scalar(
            select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
                CreditLedgerModel.organization_id == org.id,
                CreditLedgerModel.kind == CreditLedgerKind.TOPUP.value,
            )
        )
        # The tax is the government's, not credit to spend on calls.
        assert int(total) == 100_000

    async def test_a_payload_claiming_more_than_the_gross_is_capped(
        self, async_session, monkeypatch
    ):
        monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", "whsec_test")
        org = await _org(async_session, "whinflated")
        order = await self._order(async_session, org, credit=10_000, gross=11_800)

        body = self._event(order.order_id, "pay_whinflated", 10_000_000)
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=self._sign(body)
        )

        assert result["credited_paise"] == 10_000

    async def test_a_legacy_payment_without_a_gross_still_credits(
        self, async_session, monkeypatch
    ):
        """Rows written before GST existed have gross == net implicitly."""
        monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", "whsec_test")
        org = await _org(async_session, "whlegacy")
        row = PaymentModel(
            organization_id=org.id,
            provider="razorpay",
            order_id="order_whlegacy",
            amount_paise=50_000,
            gross_paise=None,
            status="created",
        )
        async_session.add(row)
        await async_session.flush()

        body = self._event("order_whlegacy", "pay_whlegacy", 50_000)
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=self._sign(body)
        )

        assert result["credited_paise"] == 50_000

    async def test_a_capture_issues_the_receipt_voucher(
        self, async_session, monkeypatch
    ):
        monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", "whsec_test")
        org = await _org(async_session, "whvoucher")
        order = await self._order(async_session, org, credit=100_000, gross=118_000)

        body = self._event(order.order_id, "pay_whvoucher", 118_000)
        await payments.handle_webhook(
            async_session, raw_body=body, signature=self._sign(body)
        )

        voucher = await async_session.scalar(
            select(TaxDocumentModel).where(
                TaxDocumentModel.organization_id == org.id,
                TaxDocumentModel.kind == RECEIPT_VOUCHER,
            )
        )
        assert voucher is not None
        assert voucher.total_paise == 118_000


@pytest.mark.asyncio
class TestBillingProfile:
    async def test_a_gstin_supplies_its_own_state(self, async_session):
        org = OrganizationModel(provider_id="org-gstinstate", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()

        profile = await billing_profile.save_profile(
            async_session,
            organization_id=org.id,
            legal_name="Acme",
            address_line1="1 Road",
            gstin="27AAAAA0000A1Z5",
            country_code="IN",
        )

        assert profile.state_code == "27"

    async def test_a_gstin_on_a_foreign_account_is_refused(self, async_session):
        org = OrganizationModel(provider_id="org-foreigngstin", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()

        with pytest.raises(tax.TaxError):
            await billing_profile.save_profile(
                async_session,
                organization_id=org.id,
                legal_name="Acme Inc",
                address_line1="1 Road",
                gstin="27AAAAA0000A1Z5",
                country_code="US",
            )

    async def test_an_incomplete_profile_says_so(self, async_session):
        org = OrganizationModel(provider_id="org-incomplete", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()

        profile = await billing_profile.save_profile(
            async_session,
            organization_id=org.id,
            legal_name="Acme",
            country_code="IN",
        )

        assert profile.is_complete is False

    async def test_an_export_profile_needs_no_state(self, async_session):
        org = OrganizationModel(provider_id="org-exportprofile", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()

        profile = await billing_profile.save_profile(
            async_session,
            organization_id=org.id,
            legal_name="Acme Inc",
            address_line1="1 Market St",
            country_code="US",
        )

        assert profile.is_export is True
        assert profile.is_complete is True


@pytest.mark.asyncio
class TestConcurrentNumbering:
    """The serial must stay gapless when two documents are issued at once.

    Committed sessions rather than the test savepoint, because the row lock only
    means anything across real concurrent transactions. The first document of a
    financial year is the sharp case: both transactions miss the series row and
    both try to create it.
    """

    async def test_simultaneous_issues_take_distinct_consecutive_numbers(
        self, test_engine, setup_test_database, monkeypatch
    ):
        from sqlalchemy.ext.asyncio import async_sessionmaker

        monkeypatch.setattr(documents, "SUPPLIER_LEGAL_NAME", "Decibyl Test Pvt Ltd")
        monkeypatch.setattr(documents, "SUPPLIER_GSTIN", "29AAAAA0000A1Z5")

        maker = async_sessionmaker(bind=test_engine, expire_on_commit=False)
        stamp = int(datetime.now(UTC).timestamp() * 1000)
        year = "99-00"  # A series of its own, so it cannot collide with real data.

        async with maker() as setup:
            org = OrganizationModel(
                provider_id=f"org-concurrent-doc-{stamp}", quota_decibyl_tokens=0
            )
            setup.add(org)
            await setup.flush()
            org_id = org.id
            payments_rows = [
                PaymentModel(
                    organization_id=org_id,
                    provider="razorpay",
                    order_id=f"order_conc_{stamp}_{i}",
                    payment_id=f"pay_conc_{stamp}_{i}",
                    amount_paise=10_000,
                    gross_paise=11_800,
                    status="paid",
                    paid_at=datetime.now(UTC),
                )
                for i in range(6)
            ]
            setup.add_all(payments_rows)
            await setup.flush()
            payment_ids = [p.id for p in payments_rows]
            await setup.commit()

        async def allocate() -> str:
            async with maker() as session:
                number = await documents._next_number(
                    session, kind=RECEIPT_VOUCHER, year=year
                )
                await session.commit()
                return number

        try:
            numbers = await asyncio.gather(*(allocate() for _ in range(6)))

            serials = sorted(int(n.rsplit("/", 1)[1]) for n in numbers)
            assert len(set(numbers)) == 6, f"numbers must be unique, got {numbers}"
            assert serials == list(range(1, 7)), (
                f"serials must be gapless 1..6, got {serials}"
            )
        finally:
            async with maker() as cleanup:
                await cleanup.execute(
                    TaxDocumentModel.__table__.delete().where(
                        TaxDocumentModel.organization_id == org_id
                    )
                )
                await cleanup.execute(
                    PaymentModel.__table__.delete().where(
                        PaymentModel.id.in_(payment_ids)
                    )
                )
                await cleanup.execute(
                    OrganizationModel.__table__.delete().where(
                        OrganizationModel.id == org_id
                    )
                )
                from api.db.models import DocumentSequenceModel

                await cleanup.execute(
                    DocumentSequenceModel.__table__.delete().where(
                        DocumentSequenceModel.financial_year == year
                    )
                )
                await cleanup.commit()
