"""Prepaid top-ups via Razorpay.

Almost every test here asserts that something does *not* credit an account.
That is the shape of the risk: the happy path is exercised by anyone who buys
credit once, whereas a forged signature, a replayed webhook, or an inflated
amount is exercised by someone who is trying, and only ever once, successfully.

The four rules in ``api/services/billing/payments.py`` each get a test:

1. only a signature-verified webhook credits,
2. a replayed webhook is a no-op,
3. the amount comes from the order, not the payload,
4. no webhook secret means no credit at all.
"""

import hashlib
import hmac
import json

import pytest
from sqlalchemy import func, select

from api.db.models import CreditLedgerModel, OrganizationModel, PaymentModel
from api.enums import CreditLedgerKind
from api.services.billing import payments
from api.services.billing.costing import current_balance_paise

WEBHOOK_SECRET = "whsec_test_do_not_use_in_production"


@pytest.fixture(autouse=True)
def configured_webhook(monkeypatch):
    """Most tests need a working secret. The one that doesn't unsets it."""
    monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)


async def _org(async_session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


async def _order(async_session, org, *, order_id: str, amount_paise: int):
    """A pending order, as ``create_topup_order`` would have left it."""
    row = PaymentModel(
        organization_id=org.id,
        provider=payments.PROVIDER,
        order_id=order_id,
        amount_paise=amount_paise,
        status="created",
    )
    async_session.add(row)
    await async_session.flush()
    return row


def _event(
    *, order_id: str, payment_id: str, amount: int, event: str = "payment.captured"
) -> bytes:
    """A webhook body shaped like Razorpay's, serialised once.

    Returned as bytes rather than a dict because the signature covers the exact
    bytes — re-serialising a dict at the call site is precisely the mistake the
    implementation is written to avoid.
    """
    return json.dumps(
        {
            "event": event,
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": amount,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
        }
    ).encode()


def _sign(raw_body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()


async def _ledger_total(async_session, org) -> int:
    total = await async_session.scalar(
        select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0)).where(
            CreditLedgerModel.organization_id == org.id
        )
    )
    return int(total or 0)


@pytest.mark.asyncio
class TestSignatureVerification:
    """Rule 1: only a signature-verified webhook credits an account."""

    async def test_a_correct_signature_verifies(self):
        body = _event(order_id="order_A", payment_id="pay_A", amount=50_000)
        assert payments.verify_webhook_signature(raw_body=body, signature=_sign(body))

    async def test_a_signature_from_a_different_secret_is_rejected(self):
        body = _event(order_id="order_A", payment_id="pay_A", amount=50_000)
        forged = _sign(body, secret="whatever-the-attacker-guessed")
        assert not payments.verify_webhook_signature(raw_body=body, signature=forged)

    async def test_a_tampered_body_is_rejected(self):
        """The signature is over the body, so changing the body invalidates it.

        This is what stops someone replaying a real ₹100 webhook with the
        amount edited to ₹100,000.
        """
        original = _event(order_id="order_A", payment_id="pay_A", amount=10_000)
        signature = _sign(original)
        tampered = _event(order_id="order_A", payment_id="pay_A", amount=10_000_000)

        assert not payments.verify_webhook_signature(
            raw_body=tampered, signature=signature
        )

    async def test_a_missing_signature_header_is_rejected(self):
        body = _event(order_id="order_A", payment_id="pay_A", amount=50_000)
        assert not payments.verify_webhook_signature(raw_body=body, signature=None)

    async def test_an_unverified_webhook_credits_nothing(self, async_session):
        org = await _org(async_session, "unverified")
        await _order(async_session, org, order_id="order_U", amount_paise=50_000)
        body = _event(order_id="order_U", payment_id="pay_U", amount=50_000)

        with pytest.raises(payments.PaymentError):
            await payments.handle_webhook(
                async_session, raw_body=body, signature="deadbeef"
            )

        assert await _ledger_total(async_session, org) == 0


@pytest.mark.asyncio
class TestNoSecretMeansNoCredit:
    """Rule 4: a deployment that forgot the secret refuses, it does not accept.

    Falling back to "no secret configured, so skip verification" would leave an
    unauthenticated endpoint that credits any account any amount.
    """

    async def test_verification_fails_when_the_secret_is_unset(self, monkeypatch):
        monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", None)
        body = _event(order_id="order_A", payment_id="pay_A", amount=50_000)

        # Even a signature that is correct under *some* secret cannot verify,
        # because there is nothing to verify against.
        assert not payments.verify_webhook_signature(
            raw_body=body, signature=_sign(body)
        )

    async def test_a_webhook_arriving_with_no_secret_set_credits_nothing(
        self, async_session, monkeypatch
    ):
        org = await _org(async_session, "nosecret")
        await _order(async_session, org, order_id="order_N", amount_paise=50_000)
        body = _event(order_id="order_N", payment_id="pay_N", amount=50_000)
        signature = _sign(body)

        monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", None)

        with pytest.raises(payments.PaymentError):
            await payments.handle_webhook(
                async_session, raw_body=body, signature=signature
            )

        assert await _ledger_total(async_session, org) == 0

    async def test_webhook_is_configured_reports_it(self, monkeypatch):
        assert payments.webhook_is_configured() is True
        monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", None)
        assert payments.webhook_is_configured() is False


@pytest.mark.asyncio
class TestCrediting:
    async def test_a_verified_capture_credits_the_account(self, async_session):
        org = await _org(async_session, "credit")
        await _order(async_session, org, order_id="order_C", amount_paise=50_000)
        body = _event(order_id="order_C", payment_id="pay_C", amount=50_000)

        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        assert result["status"] == "credited"
        assert result["credited_paise"] == 50_000
        assert await current_balance_paise(async_session, organization_id=org.id) == (
            50_000
        )

    async def test_the_ledger_row_is_a_topup_pointing_at_the_payment(
        self, async_session
    ):
        org = await _org(async_session, "ledgerref")
        await _order(async_session, org, order_id="order_L", amount_paise=25_000)
        body = _event(order_id="order_L", payment_id="pay_L", amount=25_000)

        await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        entry = await async_session.scalar(
            select(CreditLedgerModel).where(CreditLedgerModel.organization_id == org.id)
        )
        assert entry.kind == CreditLedgerKind.TOPUP.value
        assert entry.delta_paise == 25_000
        # Walkable in both directions during a reconciliation.
        assert entry.ref_type == "payment"
        assert entry.ref_id == "pay_L"

        payment = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.order_id == "order_L")
        )
        assert payment.status == "paid"
        assert payment.payment_id == "pay_L"
        assert payment.paid_at is not None
        assert payment.credit_ledger_id == entry.id

    async def test_paise_are_not_converted(self, async_session):
        """Razorpay quotes INR in paise and so does the ledger.

        Asserted explicitly because a factor-of-100 slip in either direction is
        the single most expensive bug this module could carry, and it would
        look entirely plausible in review.
        """
        org = await _org(async_session, "units")
        # ₹500.00
        await _order(async_session, org, order_id="order_P", amount_paise=50_000)
        body = _event(order_id="order_P", payment_id="pay_P", amount=50_000)

        await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        assert await _ledger_total(async_session, org) == 50_000


@pytest.mark.asyncio
class TestIdempotency:
    """Rule 2: Razorpay delivers at least once, not exactly once."""

    async def test_a_replayed_webhook_does_not_credit_twice(self, async_session):
        org = await _org(async_session, "replay")
        await _order(async_session, org, order_id="order_R", amount_paise=50_000)
        body = _event(order_id="order_R", payment_id="pay_R", amount=50_000)
        signature = _sign(body)

        first = await payments.handle_webhook(
            async_session, raw_body=body, signature=signature
        )
        second = await payments.handle_webhook(
            async_session, raw_body=body, signature=signature
        )

        assert first["status"] == "credited"
        assert second["status"] == "already_credited"
        assert await _ledger_total(async_session, org) == 50_000

        rows = await async_session.scalar(
            select(func.count())
            .select_from(CreditLedgerModel)
            .where(CreditLedgerModel.organization_id == org.id)
        )
        assert rows == 1

    async def test_a_second_payment_id_on_one_order_does_not_credit_again(
        self, async_session
    ):
        """An order is for one payment.

        The idempotency check used to be "status is paid *and* the payment id
        matches", so a second captured payment id against an order already
        credited fell straight through it into the crediting branch. The top-up
        uniqueness index does not cover this one — a different payment id is a
        different ref, so the second ledger row is legitimately distinct — which
        is why the refusal has to be here.
        """
        org = await _org(async_session, "twoids")
        await _order(async_session, org, order_id="order_T", amount_paise=50_000)

        first = _event(order_id="order_T", payment_id="pay_T1", amount=50_000)
        await payments.handle_webhook(
            async_session, raw_body=first, signature=_sign(first)
        )
        assert await _ledger_total(async_session, org) == 50_000

        second = _event(order_id="order_T", payment_id="pay_T2", amount=50_000)
        result = await payments.handle_webhook(
            async_session, raw_body=second, signature=_sign(second)
        )

        assert result["status"] == "already_credited"
        assert await _ledger_total(async_session, org) == 50_000

    async def test_the_database_refuses_a_duplicate_topup_credit(self, async_session):
        """The backstop the row lock cannot provide on its own.

        Two overlapping deliveries handled by different API processes hold
        different sessions, so the application-level check is all that stands
        between them. This asserts the partial unique index behind it, by
        writing the second credit directly — which is what a lost race would
        have produced.
        """
        from sqlalchemy.exc import IntegrityError

        org = await _org(async_session, "dupidx")
        await _order(async_session, org, order_id="order_D", amount_paise=50_000)
        body = _event(order_id="order_D", payment_id="pay_D", amount=50_000)
        await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        # In a savepoint so the expected IntegrityError rolls back only this
        # insert, leaving the session usable for the assertion after it.
        with pytest.raises(IntegrityError):
            async with async_session.begin_nested():
                async_session.add(
                    CreditLedgerModel(
                        organization_id=org.id,
                        delta_paise=50_000,
                        kind=CreditLedgerKind.TOPUP.value,
                        ref_type="payment",
                        ref_id="pay_D",
                        balance_after_paise=100_000,
                        note="the second credit a lost race would have written",
                    )
                )
                await async_session.flush()

        # The first credit stands; the duplicate never landed.
        assert await _ledger_total(async_session, org) == 50_000

    async def test_a_late_failure_does_not_uncredit_a_paid_order(self, async_session):
        """A customer whose first attempt failed and second succeeded can
        produce a late ``payment.failed`` for the same order. Marking the row
        failed then would contradict a credit that has already been given."""
        org = await _org(async_session, "latefail")
        await _order(async_session, org, order_id="order_F", amount_paise=50_000)

        captured = _event(order_id="order_F", payment_id="pay_F2", amount=50_000)
        await payments.handle_webhook(
            async_session, raw_body=captured, signature=_sign(captured)
        )

        failed = _event(
            order_id="order_F",
            payment_id="pay_F1",
            amount=50_000,
            event="payment.failed",
        )
        await payments.handle_webhook(
            async_session, raw_body=failed, signature=_sign(failed)
        )

        payment = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.order_id == "order_F")
        )
        assert payment.status == "paid"
        assert await _ledger_total(async_session, org) == 50_000


@pytest.mark.asyncio
class TestAmountComesFromTheOrder:
    """Rule 3: the payload says what it likes; the order says what was owed."""

    async def test_an_inflated_amount_credits_only_the_order_amount(
        self, async_session
    ):
        org = await _org(async_session, "inflated")
        # The customer opened an order for ₹100.
        await _order(async_session, org, order_id="order_I", amount_paise=10_000)
        # The payload claims ₹1,00,000 — correctly signed, because signing
        # proves the message came from Razorpay, not that our order agrees.
        body = _event(order_id="order_I", payment_id="pay_I", amount=10_000_000)

        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        assert result["credited_paise"] == 10_000
        assert await _ledger_total(async_session, org) == 10_000

    async def test_a_short_payment_credits_only_what_was_paid(self, async_session):
        """The other direction. A partial capture credits the smaller figure —
        crediting the order amount would give away the difference."""
        org = await _org(async_session, "short")
        await _order(async_session, org, order_id="order_S", amount_paise=50_000)
        body = _event(order_id="order_S", payment_id="pay_S", amount=30_000)

        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        assert result["credited_paise"] == 30_000
        assert await _ledger_total(async_session, org) == 30_000

    async def test_a_short_payment_on_a_taxed_order_credits_the_net_proportion(
        self, async_session
    ):
        """Half the gross buys half the net credit, not the gross minus the
        whole order's tax.

        Subtracting the full tax from a part payment was wrong in the customer's
        disfavour and got worse as the shortfall grew: on this order it credited
        ₹410 for a ₹590 payment, and a ₹200 payment would have bought ₹20. It
        also left the tax inside the part payment accounted to nobody.
        """
        org = await _org(async_session, "shorttax")
        order = await _order(
            async_session, org, order_id="order_ST", amount_paise=100_000
        )
        order.gross_paise = 118_000
        await async_session.flush()

        body = _event(order_id="order_ST", payment_id="pay_ST", amount=59_000)
        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        # 59000 * 100000 / 118000 == 50000 exactly: half the gross, half the net.
        assert result["credited_paise"] == 50_000
        assert await _ledger_total(async_session, org) == 50_000

    async def test_a_zero_amount_is_refused(self, async_session):
        org = await _org(async_session, "zero")
        await _order(async_session, org, order_id="order_Z", amount_paise=50_000)
        body = _event(order_id="order_Z", payment_id="pay_Z", amount=0)

        with pytest.raises(payments.PaymentError):
            await payments.handle_webhook(
                async_session, raw_body=body, signature=_sign(body)
            )

        assert await _ledger_total(async_session, org) == 0


@pytest.mark.asyncio
class TestEventsWeDoNotAct_On:
    async def test_an_unknown_order_is_acknowledged_not_credited(self, async_session):
        """Either a different environment sharing the endpoint, or a probe.

        Acknowledged rather than errored so Razorpay stops retrying, but no
        account is created and nothing is credited.
        """
        body = _event(order_id="order_nobody", payment_id="pay_X", amount=50_000)

        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        assert result["status"] == "unknown_order"
        total = await async_session.scalar(
            select(func.coalesce(func.sum(CreditLedgerModel.delta_paise), 0))
        )
        assert int(total or 0) == 0

    async def test_an_event_we_do_not_handle_is_ignored_quietly(self, async_session):
        """Returning an error would have Razorpay retry it forever."""
        body = json.dumps({"event": "subscription.charged", "payload": {}}).encode()

        result = await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        assert result["status"] == "ignored"

    async def test_a_body_that_is_not_json_is_refused(self, async_session):
        body = b"not json at all"
        with pytest.raises(payments.PaymentError):
            await payments.handle_webhook(
                async_session, raw_body=body, signature=_sign(body)
            )

    async def test_a_capture_missing_ids_is_refused(self, async_session):
        body = json.dumps(
            {
                "event": "payment.captured",
                "payload": {"payment": {"entity": {"amount": 50_000}}},
            }
        ).encode()

        with pytest.raises(payments.PaymentError):
            await payments.handle_webhook(
                async_session, raw_body=body, signature=_sign(body)
            )


@pytest.mark.asyncio
class TestOrderCreation:
    async def test_it_refuses_when_api_keys_are_missing(
        self, async_session, monkeypatch
    ):
        monkeypatch.setattr(payments, "RAZORPAY_KEY_ID", None)
        monkeypatch.setattr(payments, "RAZORPAY_KEY_SECRET", None)
        org = await _org(async_session, "nokeys")

        with pytest.raises(payments.PaymentNotConfigured):
            await payments.create_topup_order(
                async_session,
                organization_id=org.id,
                amount_paise=50_000,
                created_by=None,
            )

    async def test_it_refuses_an_amount_below_the_minimum(
        self, async_session, monkeypatch
    ):
        monkeypatch.setattr(payments, "RAZORPAY_KEY_ID", "rzp_test_key")
        monkeypatch.setattr(payments, "RAZORPAY_KEY_SECRET", "secret")
        monkeypatch.setattr(payments, "MIN_TOPUP_PAISE", 10_000)
        org = await _org(async_session, "toosmall")

        with pytest.raises(payments.PaymentError):
            await payments.create_topup_order(
                async_session,
                organization_id=org.id,
                amount_paise=100,
                created_by=None,
            )

    async def test_no_row_is_written_when_the_amount_is_refused(
        self, async_session, monkeypatch
    ):
        """The bound check happens before the provider call, so a rejected
        amount leaves neither an order at Razorpay nor a row here."""
        monkeypatch.setattr(payments, "RAZORPAY_KEY_ID", "rzp_test_key")
        monkeypatch.setattr(payments, "RAZORPAY_KEY_SECRET", "secret")
        monkeypatch.setattr(payments, "MAX_TOPUP_PAISE", 50_000_000)
        org = await _org(async_session, "toolarge")

        with pytest.raises(payments.PaymentError):
            await payments.create_topup_order(
                async_session,
                organization_id=org.id,
                amount_paise=99_000_000,
                created_by=None,
            )

        count = await async_session.scalar(
            select(func.count())
            .select_from(PaymentModel)
            .where(PaymentModel.organization_id == org.id)
        )
        assert count == 0


@pytest.mark.asyncio
class TestListing:
    async def test_it_only_returns_this_accounts_payments(self, async_session):
        """Tenant isolation. A top-up history that leaked across accounts would
        disclose what a competitor spends."""
        mine = await _org(async_session, "mine")
        theirs = await _org(async_session, "theirs")
        await _order(async_session, mine, order_id="order_mine", amount_paise=10_000)
        await _order(
            async_session, theirs, order_id="order_theirs", amount_paise=99_000
        )

        rows = await payments.list_payments(async_session, organization_id=mine.id)

        assert [r["order_id"] for r in rows] == ["order_mine"]

    async def test_it_does_not_leak_the_provider_payload(self, async_session):
        """The stored payload is for disputes, not for the customer screen. It
        carries card metadata and provider internals we have no reason to
        re-serve."""
        org = await _org(async_session, "payload")
        await _order(async_session, org, order_id="order_PL", amount_paise=10_000)
        body = _event(order_id="order_PL", payment_id="pay_PL", amount=10_000)
        await payments.handle_webhook(
            async_session, raw_body=body, signature=_sign(body)
        )

        rows = await payments.list_payments(async_session, organization_id=org.id)

        assert "provider_payload" not in rows[0]
