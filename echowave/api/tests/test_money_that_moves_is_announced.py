"""Every debit reaches the customer, and no standing instruction is wrong.

Three silences, all of which look like nothing going wrong.

**The autopay receipt.** The collection voucher has been issued since the
autopay work landed and was never sent: the route enqueued the email off
``receipt_voucher_id``, which only the prepaid top-up path sets. A bank taking
₹3,538.82 a month against silence is the most reliable way to be asked for a
chargeback, and under GST the document is owed whether or not it is delivered.

**The dunning ladder.** ``dunning.py`` has always computed a customer-facing
message for each step — retry, suspend at seven days, release-eligible at
forty-five — and nothing sent it. A number went quiet on day seven with no
warning at any point.

**The pinned plan's amount.** ``_ensure_plan`` returns a pinned id without
looking at it, so the carefully derived gross was discarded. A plan created at
₹2,999 when the gross is ₹3,538.82 collects no GST at all, monthly, by standing
instruction, and nothing in the system can detect it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from api.enums import RecurringChargeStatus
from api.services.billing import dunning, dunning_email
from api.services.billing import mandates as mandate_service


class TestTheAutopayReceiptIsSent:
    def test_the_collection_voucher_reports_an_id_to_email_it_by(self):
        """Returning only a human-readable number is why it was never sent."""
        import inspect

        from api.services.billing import payments

        source = inspect.getsource(payments._issue_collection_voucher)

        assert '"id": voucher.id' in source, (
            "the collection voucher must report its id — the route enqueues "
            "the email off it, and a number alone cannot be looked up"
        )

    def test_the_webhook_route_looks_in_both_places(self):
        """A top-up reports its voucher at the top level; a collection nests it."""
        import inspect

        from api.routes import payments as payments_route

        source = inspect.getsource(payments_route)

        assert 'result.get("voucher")' in source, (
            "the autopay branch reports its voucher under 'voucher'; reading "
            "only 'receipt_voucher_id' silently skipped every collection"
        )


class TestTheDunningLadderIsAnnounced:
    def _state(self, *, days: int) -> dunning.DunningState:
        now = datetime.now(UTC)
        return dunning.evaluate(
            first_failed_at=now - timedelta(days=days),
            current_status=RecurringChargeStatus.ACTIVE,
            now=now,
        )

    def test_a_suspension_is_announced_and_names_the_number(self):
        notice = dunning_email.compose(
            state=self._state(days=dunning.SUSPEND_AFTER_DAYS),
            charge_id=1,
            account_name="Sunrise Clinic",
            phone_number="+918047182200",
            amount_paise=49_900,
            top_up_url="https://app.decibyl.ai/billing",
        )

        assert notice is not None
        # The number, because an account with three of them needs to know which.
        assert "+918047182200" in notice.subject
        assert "paused" in notice.subject
        assert "₹499.00" in notice.body
        assert "https://app.decibyl.ai/billing" in notice.body

    def test_the_release_warning_says_it_cannot_be_undone(self):
        """The one irreversible step in the schedule."""
        notice = dunning_email.compose(
            state=self._state(days=dunning.RELEASE_ELIGIBLE_AFTER_DAYS),
            charge_id=1,
            account_name="Sunrise Clinic",
            phone_number="+918047182200",
            amount_paise=49_900,
            top_up_url="https://app.decibyl.ai/billing",
        )

        assert notice is not None
        assert "release" in notice.subject.lower()
        assert "cannot be recovered" in notice.body

    def test_a_quiet_retry_day_sends_nothing(self):
        """The job runs daily. Warning daily would train people to ignore it."""
        quiet = next(
            day
            for day in range(1, dunning.SUSPEND_AFTER_DAYS)
            if day not in dunning.WARN_AFTER_DAYS
        )

        assert (
            dunning_email.compose(
                state=self._state(days=quiet),
                charge_id=1,
                account_name="Sunrise Clinic",
                phone_number="+918047182200",
                amount_paise=49_900,
                top_up_url="https://app.decibyl.ai/billing",
            )
            is None
        )

    def test_two_days_at_the_same_step_are_one_notice(self):
        """Deduplicated on the state reached, not on the attempt."""
        first = dunning_email.compose(
            state=self._state(days=dunning.SUSPEND_AFTER_DAYS),
            charge_id=7,
            account_name="Sunrise Clinic",
            phone_number="+918047182200",
            amount_paise=49_900,
            top_up_url="https://app.decibyl.ai/billing",
        )
        again = dunning_email.compose(
            state=self._state(days=dunning.SUSPEND_AFTER_DAYS),
            charge_id=7,
            account_name="Sunrise Clinic",
            phone_number="+918047182200",
            amount_paise=49_900,
            top_up_url="https://app.decibyl.ai/billing",
        )

        assert first is not None and again is not None
        assert first.dedupe_key == again.dedupe_key

    def test_a_charge_that_recovered_is_not_chased(self):
        state = dunning.evaluate(
            first_failed_at=None,
            current_status=RecurringChargeStatus.SUSPENDED,
            now=datetime.now(UTC),
        )

        assert (
            dunning_email.compose(
                state=state,
                charge_id=1,
                account_name="Sunrise Clinic",
                phone_number="+918047182200",
                amount_paise=49_900,
                top_up_url="https://app.decibyl.ai/billing",
            )
            is None
        )


class TestThePinnedPlanCollectsTheRightAmount:
    """A standing instruction for the wrong figure is wrong every month."""

    @pytest.fixture(autouse=True)
    def _configured(self, monkeypatch):
        """Razorpay credentials, so the read is attempted rather than skipped.

        Without them ``_auth`` refuses and the check falls through its "cannot
        ask" branch — correct in production, and useless for asserting what the
        check does when it *can* ask.
        """
        monkeypatch.setattr(mandate_service, "RAZORPAY_KEY_ID", "rzp_test_x")
        monkeypatch.setattr(mandate_service, "RAZORPAY_KEY_SECRET", "secret")

    async def test_a_plan_collecting_the_wrong_amount_is_refused(self):
        def provider(request: httpx.Request) -> httpx.Response:
            # ₹2,999 — the net figure, which is the mistake this catches: a
            # plan created from the price on the pricing page rather than from
            # the grossed-up amount the customer actually owes.
            return httpx.Response(200, json={"item": {"amount": 299_900}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            with pytest.raises(mandate_service.MandateError, match="₹2,999.00"):
                await mandate_service._assert_pinned_plan_amount(
                    plan_id="plan_x",
                    expected_paise=353_882,
                    env_var="RAZORPAY_STARTER_PLAN_ID",
                    client=client,
                )

    async def test_the_right_amount_passes_quietly(self):
        def provider(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"item": {"amount": 353_882}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            await mandate_service._assert_pinned_plan_amount(
                plan_id="plan_x",
                expected_paise=353_882,
                env_var="RAZORPAY_STARTER_PLAN_ID",
                client=client,
            )

    async def test_a_provider_we_cannot_reach_does_not_block_signups(self):
        """An outage must not stop customers subscribing.

        The amount is far more likely right than not, and refusing here would
        turn the provider's bad day into ours.
        """

        def down(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": {"description": "down"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
            await mandate_service._assert_pinned_plan_amount(
                plan_id="plan_x",
                expected_paise=353_882,
                env_var="RAZORPAY_STARTER_PLAN_ID",
                client=client,
            )

    async def test_the_error_names_the_variable_to_fix(self):
        """An operator reading this has to know which env var points wrong."""

        def provider(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"item": {"amount": 100}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            with pytest.raises(
                mandate_service.MandateError, match="RAZORPAY_STARTER_PLAN_ID"
            ):
                await mandate_service._assert_pinned_plan_amount(
                    plan_id="plan_x",
                    expected_paise=353_882,
                    env_var="RAZORPAY_STARTER_PLAN_ID",
                    client=client,
                )
