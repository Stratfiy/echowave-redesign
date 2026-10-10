"""There is no checkout (founder, 9 Oct 2026).

Nothing is sold, so nothing may take money or arrange to. These tests run with
the product's own settings (``checkout_closed``; conftest opens the checkout
for the rest of the suite, which exists to prove the money code is right for
the day it is reopened) and pin what the decision promises:

* every checkout route answers 410, whoever asks, and none of them is in the
  public spec;
* no service can create a Razorpay order, subscription, mandate or saved-card
  charge, so a stale client, a bookmark or a background job cannot either;
* the webhook still settles what is already in flight, and a mandate can still
  be cancelled (money stops, it never starts);
* the emails that chase a payment are off: trial, low balance, auto top-up,
  dunning and plan renewal;
* the unpaid-rental ladder is off, so a number is never suspended or released
  because billing stopped;
* the "out of credit" wall says what the daily limits say.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from api import constants
from api.db.models import OrganizationModel, PaymentMandateModel, PaymentModel
from api.enums import RecurringChargeStatus
from api.services.billing import (
    auto_topup_runner,
    dunning,
    mandates,
    no_checkout,
    payments,
    trial,
)

pytestmark = pytest.mark.checkout_closed

#: Every route that sells something or arranges to, as (method, path, body).
CLOSED_ROUTES = [
    ("GET", "/api/v1/billing/plans", None),
    ("GET", "/api/v1/billing/plan", None),
    ("POST", "/api/v1/billing/plan", {"plan_code": "everyday"}),
    ("POST", "/api/v1/billing/topup", {"amount_paise": 50000}),
    ("POST", "/api/v1/billing/topup", {"pack": "p500"}),
    ("POST", "/api/v1/billing/promo/preview", {"code": "WELCOME", "pack": "p500"}),
    ("GET", "/api/v1/billing/auto-topup", None),
    (
        "PUT",
        "/api/v1/billing/auto-topup",
        {"enabled": True, "amount_paise": 100000},
    ),
    ("POST", "/api/v1/billing/auto-topup/cancel-pending", None),
    ("GET", "/api/v1/billing/mandate", None),
    ("POST", "/api/v1/billing/mandate", None),
]


class _Razorpay:
    """Stands in for ``httpx`` inside the billing services: any use is a bug."""

    class AsyncClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Razorpay was called while there is no checkout")

    class HTTPError(Exception):
        pass

    class TimeoutException(Exception):
        pass


@pytest.fixture
def no_razorpay(monkeypatch):
    """Any attempt to reach Razorpay fails the test."""
    monkeypatch.setattr(payments, "httpx", _Razorpay)
    monkeypatch.setattr(mandates, "httpx", _Razorpay)
    monkeypatch.setattr(payments, "RAZORPAY_KEY_ID", "rzp_test_x")
    monkeypatch.setattr(payments, "RAZORPAY_KEY_SECRET", "secret")
    monkeypatch.setattr(payments, "RAZORPAY_WEBHOOK_SECRET", "whsec")


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _count(session, model, org_id: int) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.organization_id == org_id)
        )
        or 0
    )


# ---------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------


class TestEveryCheckoutRouteIsGone:
    @pytest.mark.parametrize(("method", "path", "body"), CLOSED_ROUTES)
    async def test_it_answers_410_with_no_sign_in(self, method, path, body):
        """Before auth and before validation, so who asks does not matter."""
        from api.app import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.request(method, path, json=body)

        assert response.status_code == 410, f"{method} {path}: {response.text[:200]}"
        assert "without charge" in response.json()["detail"]

    @pytest.mark.parametrize(("method", "path", "body"), CLOSED_ROUTES)
    async def test_it_answers_410_for_a_signed_in_member_too(
        self, async_session, method, path, body
    ):
        from api.app import app
        from api.db.models import UserModel
        from api.services.auth.depends import get_user

        org = await _org(async_session, "member")
        user = UserModel(provider_id="user-member", selected_organization_id=org.id)
        async_session.add(user)
        await async_session.flush()
        app.dependency_overrides[get_user] = lambda: user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.request(method, path, json=body)
        finally:
            app.dependency_overrides.pop(get_user, None)

        assert response.status_code == 410, f"{method} {path}"

    async def test_no_order_is_created_by_a_topup_request(
        self, async_session, no_razorpay
    ):
        """The one that matters: a stale client posting a top-up."""
        from api.app import app
        from api.db.models import UserModel
        from api.services.auth.depends import get_user

        org = await _org(async_session, "stale-client")
        user = UserModel(provider_id="user-stale", selected_organization_id=org.id)
        async_session.add(user)
        await async_session.flush()
        before = await _count(async_session, PaymentModel, org.id)
        app.dependency_overrides[get_user] = lambda: user
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/v1/billing/topup", json={"amount_paise": 50000}
                )
        finally:
            app.dependency_overrides.pop(get_user, None)

        assert response.status_code == 410
        assert await _count(async_session, PaymentModel, org.id) == before == 0

    async def test_the_closed_routes_are_not_in_the_public_spec(self):
        from api.app import app

        paths = app.openapi()["paths"]
        for _, path, _body in CLOSED_ROUTES:
            assert path not in paths, f"{path} is documented but gone"

    async def test_what_stays_open_is_not_behind_the_gate(self):
        """The webhook (refunds and in-flight events), cancelling a mandate,
        and the record of what was paid."""
        from api.app import app

        stays = {
            ("POST", "/api/v1/billing/razorpay/webhook"),
            ("POST", "/api/v1/billing/mandate/cancel"),
            ("GET", "/api/v1/billing/documents"),
            ("GET", "/api/v1/billing/documents/{document_id}"),
            ("GET", "/api/v1/billing/documents/{document_id}/pdf"),
            ("POST", "/api/v1/billing/documents/{document_id}/email"),
            ("GET", "/api/v1/billing/payments"),
            ("GET", "/api/v1/billing/profile"),
            ("PUT", "/api/v1/billing/profile"),
        }
        found = set()
        for route in app.routes:
            path = getattr(route, "path", "")
            for method in getattr(route, "methods", None) or ():
                if (method, path) in stays:
                    found.add((method, path))
                    calls = [d.call for d in route.dependant.dependencies]
                    assert no_checkout.gone not in calls, f"{method} {path} is gated"
        assert found == stays, f"missing routes: {sorted(stays - found)}"

    async def test_the_webhook_still_answers_a_bad_signature_with_400(self):
        from api.app import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/billing/razorpay/webhook",
                content=b"{}",
                headers={"x-razorpay-signature": "nope"},
            )
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# The services behind them
# ---------------------------------------------------------------------------


class TestNothingCanCreateAnOrderOrAMandate:
    async def test_a_topup_order_is_refused_before_razorpay(
        self, async_session, no_razorpay
    ):
        org = await _org(async_session, "order")
        with pytest.raises(no_checkout.CheckoutClosed):
            await payments.create_topup_order(
                async_session,
                organization_id=org.id,
                amount_paise=50_000,
                created_by=None,
            )
        assert await _count(async_session, PaymentModel, org.id) == 0

    async def test_a_rental_mandate_is_refused_before_razorpay(
        self, async_session, no_razorpay
    ):
        org = await _org(async_session, "rental-mandate")
        with pytest.raises(no_checkout.CheckoutClosed):
            await mandates.create_rental_mandate(async_session, organization_id=org.id)
        assert await _count(async_session, PaymentMandateModel, org.id) == 0

    async def test_a_plan_mandate_is_refused_before_razorpay(
        self, async_session, no_razorpay
    ):
        org = await _org(async_session, "plan-mandate")
        with pytest.raises(no_checkout.CheckoutClosed):
            await mandates.create_plan_mandate(async_session, organization_id=org.id)
        assert await _count(async_session, PaymentMandateModel, org.id) == 0

    async def test_a_saved_card_is_never_charged(self, no_razorpay):
        with pytest.raises(no_checkout.CheckoutClosed):
            await payments.charge_saved_token(
                order_id="order_x",
                amount_paise=59_000,
                token=object(),  # never read: the refusal comes first
                email=None,
                contact=None,
            )

    async def test_an_auto_topup_is_neither_announced_nor_charged(
        self, async_session, no_razorpay
    ):
        org = await _org(async_session, "auto")
        with pytest.raises(no_checkout.CheckoutClosed):
            await auto_topup_runner.schedule(
                async_session, org=org, decision=object(), recipients=[]
            )
        with pytest.raises(no_checkout.CheckoutClosed):
            await auto_topup_runner.execute(async_session, org=org, attempt=object())

    async def test_the_auto_topup_sweep_does_nothing(self, monkeypatch):
        from api.tasks import auto_topup as sweep

        async def boom(*args, **kwargs):
            raise AssertionError("the sweep read accounts while there is no checkout")

        monkeypatch.setattr(sweep, "_enabled_accounts", boom)
        assert await sweep.sweep_auto_topups(None) is None


class TestTheWebhookStillSettlesWhatIsInFlight:
    async def test_a_payment_already_on_its_way_is_credited(self, async_session):
        """An order opened before the checkout closed is still paid when its
        money lands; closing the routes must not strand a customer's payment."""
        secret = "whsec_in_flight"
        org = await _org(async_session, "in-flight")
        async_session.add(
            PaymentModel(
                organization_id=org.id,
                provider=payments.PROVIDER,
                order_id="order_IF",
                amount_paise=50_000,
                status="created",
            )
        )
        await async_session.flush()
        body = json.dumps(
            {
                "event": "payment.captured",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_IF",
                            "order_id": "order_IF",
                            "amount": 50_000,
                            "currency": "INR",
                            "status": "captured",
                        }
                    }
                },
            }
        ).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        import api.services.billing.payments as payments_module

        original = payments_module.RAZORPAY_WEBHOOK_SECRET
        payments_module.RAZORPAY_WEBHOOK_SECRET = secret
        try:
            await payments.handle_webhook(
                async_session, raw_body=body, signature=signature
            )
        finally:
            payments_module.RAZORPAY_WEBHOOK_SECRET = original

        row = await async_session.scalar(
            select(PaymentModel).where(PaymentModel.order_id == "order_IF")
        )
        assert row.status == "paid"


# ---------------------------------------------------------------------------
# The emails
# ---------------------------------------------------------------------------


class TestTheMoneyEmailsAreOff:
    async def test_the_trial_is_off_whatever_the_flag_says(self, monkeypatch):
        monkeypatch.setattr(constants, "TRIAL_PLAN_ENABLED", True)
        monkeypatch.setattr(constants, "FREE_MODE_ENABLED", False)
        assert trial.applies(None) is False
        assert trial.applies(123) is False

    async def test_the_trial_notices_send_nothing(self, monkeypatch):
        monkeypatch.setattr(constants, "TRIAL_PLAN_ENABLED", True)
        assert await trial.send_notices() == {"checked": 0, "sent": 0}

    async def test_the_trial_flag_itself_is_off_in_the_product(self):
        import os
        import subprocess
        import sys

        # A fresh interpreter, because conftest and monkeypatch have both
        # touched this module's constants by now.
        env = {k: v for k, v in os.environ.items() if k != "TRIAL_PLAN_ENABLED"}
        env["TRIAL_PLAN_ENABLED"] = "true"
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "from api import constants; "
                "print(constants.TRIAL_PLAN_ENABLED, constants.CHECKOUT_OPEN)",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert out[-2:] == ["False", "False"], "the environment must not reopen it"

    async def test_the_low_balance_job_warns_nobody(self, monkeypatch):
        from api.tasks import low_balance

        async def boom(*args, **kwargs):
            raise AssertionError("looked for accounts to warn")

        monkeypatch.setattr(low_balance, "_spending_accounts", boom)
        assert await low_balance.notify_low_balances() == {
            "considered": 0,
            "warned": 0,
            "skipped": 0,
            "failed": 0,
        }

    async def test_no_dunning_email_is_composed_or_sent(self, monkeypatch):
        from api.services.billing import dunning_email
        from api.tasks import rental_billing

        def boom(**kwargs):
            raise AssertionError("composed a dunning notice")

        monkeypatch.setattr(dunning_email, "compose", boom)
        sent = await rental_billing.notify_dunning(
            organization_id=1,
            charge_id=1,
            state=dunning.DunningState(
                status=RecurringChargeStatus.SUSPENDED,
                days_overdue=9,
                should_suspend=True,
                should_warn=True,
                may_release=False,
                message="x",
            ),
            phone_number="+918041234567",
            amount_paise=55_900,
        )
        assert sent is False

    async def test_the_plan_authorisation_email_is_not_sent(self, monkeypatch):
        from api.routes import payments as routes
        from api.services.messaging import announce

        async def boom(**kwargs):
            raise AssertionError("announced a plan authorisation")

        monkeypatch.setattr(announce, "announce", boom)
        await routes._confirm_plan_authorisation(
            {"organization_id": 1, "mandate_id": 1, "price_paise": 100}
        )


# ---------------------------------------------------------------------------
# The wall, in the words of the limits
# ---------------------------------------------------------------------------


class TestOutOfCreditSaysWhatTheLimitsSay:
    def test_it_is_the_daily_limit_message_and_names_no_price(self):
        from api.services import quota_service, quotas

        for mode in ("plivo", "textchat", None):
            message = quota_service.no_credit_message(mode=mode)
            assert message.startswith("You have used today's ")
            assert "resets at" in message
            for word in ("credit", "₹", "balance", "Add ", "top up", "plan"):
                assert word not in message, f"{word!r} in {message!r}"

        # And it is quotas.message itself, not a lookalike.
        limit = quotas.base_limit(quotas.VOICE_MINUTES)
        assert quota_service.no_credit_message(mode="plivo") == quotas.message(
            quotas.Usage(
                kind=quotas.VOICE_MINUTES,
                used=limit,
                limit=limit,
                resets_at=quotas.resets_at(),
            )
        )

    def test_a_chat_is_told_about_messages_not_calls(self):
        from api.services import quota_service

        text = quota_service.no_credit_message(mode="textchat")
        assert "message" in text and "voice minute" not in text
        call = quota_service.no_credit_message(mode="plivo")
        assert "voice minute" in call


# ---------------------------------------------------------------------------
# Phone numbers
# ---------------------------------------------------------------------------


class TestTheRentalLadderIsOff:
    NOW = datetime(2026, 11, 1, tzinfo=UTC)

    @pytest.mark.parametrize("days", [0, 7, 15, 25, 45, 400])
    def test_a_failing_charge_is_only_ever_past_due(self, days):
        state = dunning.evaluate(
            first_failed_at=self.NOW - timedelta(days=days),
            current_status=RecurringChargeStatus.ACTIVE,
            now=self.NOW,
        )
        assert state.status is RecurringChargeStatus.PAST_DUE
        assert state.should_suspend is False
        assert state.should_warn is False
        assert state.may_release is False
        assert state.message == ""

    def test_nothing_is_ever_eligible_for_release(self):
        assert (
            dunning.may_release(
                first_failed_at=self.NOW - timedelta(days=4000), now=self.NOW
            )
            is False
        )

    def test_the_ladder_is_a_constant_that_ships_off(self):
        import os
        import subprocess
        import sys

        env = {k: v for k, v in os.environ.items()}
        env["RENTAL_SUSPEND_AND_RELEASE_ENABLED"] = "true"
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "from api import constants; "
                "print(constants.RENTAL_SUSPEND_AND_RELEASE_ENABLED, "
                "constants.REQUIRE_MANDATE_FOR_NUMBERS)",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert out[-2:] == ["False", "False"]
