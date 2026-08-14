"""The ARQ job that emails a tax document's PDF to the account it belongs to.

Issuing the document must never depend on this succeeding -- it runs after the
document is already committed, so what matters here is that it degrades
correctly: no SMTP configured, no billing email on file, and a document that
no longer exists are all "nothing to send", not exceptions.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from api.db.models import (
    BillingProfileModel,
    OrganizationModel,
    PaymentModel,
    UserModel,
)
from api.services.billing import documents, tax
from api.services.messaging.email import SendResult
from api.tasks import email_tax_document as task_module

OUR_STATE = "29"


@pytest.fixture(autouse=True)
def supplier_configured(monkeypatch):
    monkeypatch.setattr(tax, "SUPPLIER_STATE_CODE", OUR_STATE)
    monkeypatch.setattr(tax, "GST_RATE_BASIS_POINTS", 1800)
    monkeypatch.setattr(documents, "SUPPLIER_LEGAL_NAME", "Decibyl Test Pvt Ltd")
    monkeypatch.setattr(documents, "SUPPLIER_GSTIN", "29AAAAA0000A1Z5")
    monkeypatch.setattr(documents, "SUPPLIER_STATE_CODE", OUR_STATE)


@pytest.fixture(autouse=True)
def _task_uses_the_test_session(monkeypatch, async_session):
    """The task opens its own session via `db_client.async_session()`, which
    is a different connection than the test's transaction-isolated fixture --
    invisible to each other, same as any other real connection pair. Point the
    task at the fixture's session instead of a fresh one, the same way the
    task would see whatever the real db_client resolves to in production."""

    @asynccontextmanager
    async def _session_cm():
        yield async_session

    monkeypatch.setattr(task_module.db_client, "async_session", _session_cm)


async def _org_with_email(async_session, slug: str, *, billing_email: str | None):
    user = UserModel(provider_id=f"user-{slug}")
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add_all([user, org])
    await async_session.flush()
    async_session.add(
        BillingProfileModel(
            organization_id=org.id,
            legal_name=f"{slug.title()} Pvt Ltd",
            address_line1="1 Test Road",
            city="Bengaluru",
            state_code=OUR_STATE,
            country_code="IN",
            billing_email=billing_email,
        )
    )
    await async_session.flush()
    return org


async def _voucher(async_session, org):
    payment = PaymentModel(
        organization_id=org.id,
        provider="razorpay",
        order_id=f"order_{org.id}",
        payment_id=f"pay_{org.id}",
        amount_paise=100_000,
        gross_paise=118_000,
        cgst_paise=9_000,
        sgst_paise=9_000,
        igst_paise=0,
        status="paid",
        paid_at=datetime.now(UTC),
    )
    async_session.add(payment)
    await async_session.flush()
    voucher = await documents.issue_receipt_voucher(async_session, payment=payment)
    await async_session.commit()
    return voucher


@pytest.mark.asyncio
class TestEmailTaxDocument:
    async def test_unconfigured_email_sends_nothing(self, async_session, monkeypatch):
        org = await _org_with_email(
            async_session, "unconfigured", billing_email="ops@acme.example"
        )
        voucher = await _voucher(async_session, org)

        monkeypatch.setattr(task_module, "email_is_configured", lambda: False)
        sent = []
        monkeypatch.setattr(
            task_module, "send_document", lambda *a, **kw: sent.append(kw) or None
        )

        await task_module.email_tax_document(None, voucher.id)
        assert sent == []

    async def test_no_billing_email_on_file_sends_nothing(
        self, async_session, monkeypatch
    ):
        org = await _org_with_email(async_session, "noemail", billing_email=None)
        voucher = await _voucher(async_session, org)

        monkeypatch.setattr(task_module, "email_is_configured", lambda: True)
        calls = []

        async def _fake_send(document, **kwargs):
            calls.append(kwargs)
            return SendResult(ok=True)

        monkeypatch.setattr(task_module, "send_document", _fake_send)

        await task_module.email_tax_document(None, voucher.id)
        assert calls == []

    async def test_a_missing_document_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(task_module, "email_is_configured", lambda: True)
        await task_module.email_tax_document(None, 999_999_999)

    async def test_a_configured_recipient_gets_the_pdf_attached(
        self, async_session, monkeypatch
    ):
        org = await _org_with_email(
            async_session, "configured", billing_email="billing@acme.example"
        )
        voucher = await _voucher(async_session, org)

        monkeypatch.setattr(task_module, "email_is_configured", lambda: True)
        calls = []

        async def _fake_send(document, **kwargs):
            calls.append(kwargs)
            return SendResult(ok=True)

        monkeypatch.setattr(task_module, "send_document", _fake_send)

        await task_module.email_tax_document(None, voucher.id)

        assert len(calls) == 1
        call = calls[0]
        assert call["recipient"] == "billing@acme.example"
        assert call["pdf_bytes"].startswith(b"%PDF")
