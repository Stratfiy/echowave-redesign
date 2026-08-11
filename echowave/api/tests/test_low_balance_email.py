"""Warning a customer before the credit runs out.

The dunning schedule suspends a number seven days after a rental it could not
collect. That policy is right and it is silent, which makes the experience
wrong: the money was almost always there and nobody said it was needed.

Three properties, in the order they matter:

* **one notice per account per day per severity** — the claim is made before
  the send, so a re-run or a second worker sends nothing
* **a dead SMTP server never breaks billing** — every send returns a boolean;
  nothing here raises into a job whose real work is money
* **the threshold is runway, with an absolute floor** — an account spending
  ₹5,000 a day and one spending ₹50 need warning at very different balances,
  and an account with no burn rate at all still needs one
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from api.db.models import (
    CreditLedgerModel,
    DailyOrganizationRollupModel,
    NotificationModel,
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
)
from api.enums import CreditLedgerKind, OrganizationRole
from api.services.billing import low_balance
from api.tasks import low_balance as job


async def _account(session, slug: str, *, balance_paise: int, email: str | None):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}", email=email)
    session.add_all([org, user])
    await session.flush()
    session.add(
        OrganizationMembershipModel(
            organization_id=org.id,
            user_id=user.id,
            role=OrganizationRole.OWNER.value,
        )
    )
    session.add(
        CreditLedgerModel(
            organization_id=org.id,
            delta_paise=balance_paise,
            kind=CreditLedgerKind.TOPUP.value,
            balance_after_paise=balance_paise,
        )
    )
    await session.flush()
    return org


async def _spend(session, org, *, per_day_paise: int, days: int = 7):
    today = datetime.now(UTC).date()
    for offset in range(days):
        session.add(
            DailyOrganizationRollupModel(
                organization_id=org.id,
                day=today - timedelta(days=offset),
                calls=1,
                billable_minutes=1,
                charged_paise=per_day_paise,
                provider_cost_paise=0,
                margin_paise=per_day_paise,
            )
        )
    await session.flush()


class TestThePolicy:
    """Pure. No database, no SMTP — just the decision."""

    def test_ten_days_of_runway_is_a_warning(self):
        # ₹1,000 balance at ₹100/day.
        assessment = low_balance.assess(
            balance_paise=100_000, daily_burn_paise=10_000
        )
        assert assessment.level == low_balance.LOW
        assert assessment.days_remaining == 10

    def test_three_days_of_runway_is_critical(self):
        assessment = low_balance.assess(
            balance_paise=30_000, daily_burn_paise=10_000
        )
        assert assessment.level == low_balance.CRITICAL

    def test_a_month_of_runway_says_nothing(self):
        assessment = low_balance.assess(
            balance_paise=300_000, daily_burn_paise=10_000
        )
        assert assessment.level is None

    def test_a_big_balance_is_still_a_warning_at_a_big_burn_rate(self):
        """The reason runway is the threshold rather than an amount. ₹40,000
        looks healthy and is four days at this rate."""
        assessment = low_balance.assess(
            balance_paise=4_000_000, daily_burn_paise=1_000_000
        )
        assert assessment.level == low_balance.LOW

    def test_a_quiet_account_still_gets_the_floor(self):
        """No burn rate means no runway to divide by. "Infinite days" on ₹120
        of credit is not a useful answer to anybody."""
        assessment = low_balance.assess(balance_paise=12_000, daily_burn_paise=0)
        assert assessment.level == low_balance.LOW
        assert assessment.days_remaining is None

    def test_a_negative_balance_is_critical_not_skipped(self):
        """It happens — a call in flight when the balance hits zero finishes
        and is charged. It is the most urgent version of this warning, not a
        strange case to ignore."""
        assessment = low_balance.assess(balance_paise=-500, daily_burn_paise=0)
        assert assessment.level == low_balance.CRITICAL

    def test_a_healthy_quiet_account_is_left_alone(self):
        assessment = low_balance.assess(balance_paise=500_000, daily_burn_paise=0)
        assert assessment.level is None

    def test_severity_and_day_are_what_make_two_notices_the_same(self):
        day = date(2026, 8, 10)
        assert low_balance.dedupe_key("low", day) != low_balance.dedupe_key(
            "critical", day
        )
        assert low_balance.dedupe_key("low", day) != low_balance.dedupe_key(
            "low", date(2026, 8, 11)
        )


class TestTheMessage:
    def test_it_states_the_consequence_not_just_the_number(self):
        """"Your balance is low" is not actionable."""
        assessment = low_balance.assess(balance_paise=50_000, daily_burn_paise=10_000)
        _subject, body = low_balance.compose(
            assessment=assessment,
            account_name="Acme",
            top_up_url="https://app.decibyl.ai/billing",
            has_numbers=False,
        )
        assert "Calls stop when the balance reaches zero" in body
        assert "https://app.decibyl.ai/billing" in body

    def test_the_number_suspension_warning_only_appears_if_they_have_a_number(
        self,
    ):
        """Telling an account with no numbers that their number will be
        suspended is the kind of detail that costs trust."""
        assessment = low_balance.assess(balance_paise=50_000, daily_burn_paise=10_000)
        _s, without = low_balance.compose(
            assessment=assessment,
            account_name="Acme",
            top_up_url="u",
            has_numbers=False,
        )
        _s, with_number = low_balance.compose(
            assessment=assessment,
            account_name="Acme",
            top_up_url="u",
            has_numbers=True,
        )
        assert "suspends the number" not in without
        assert "suspends the number" in with_number

    def test_an_unmeasurable_runway_is_said_rather_than_invented(self):
        assessment = low_balance.assess(balance_paise=12_000, daily_burn_paise=0)
        _s, body = low_balance.compose(
            assessment=assessment,
            account_name="Acme",
            top_up_url="u",
            has_numbers=False,
        )
        assert "not placed enough calls" in body

    def test_critical_says_so_in_the_subject(self):
        low = low_balance.assess(balance_paise=100_000, daily_burn_paise=10_000)
        critical = low_balance.assess(balance_paise=20_000, daily_burn_paise=10_000)
        low_subject, _ = low_balance.compose(
            assessment=low, account_name="Acme", top_up_url="u", has_numbers=False
        )
        critical_subject, _ = low_balance.compose(
            assessment=critical, account_name="Acme", top_up_url="u", has_numbers=False
        )
        assert low_subject != critical_subject
        assert "Action needed" in critical_subject


@pytest.fixture
def captured_email(monkeypatch):
    """Intercept sends. Nothing in this suite may open a socket."""
    from api.services.messaging.email import SendResult

    sent: list[dict] = []

    async def _send(*, to, subject, body_text, **kwargs):
        sent.append({"to": [to], "subject": subject, "text": body_text})
        return SendResult(ok=True)

    monkeypatch.setattr(job.email, "send_email", _send)
    monkeypatch.setattr(job.email, "email_is_configured", lambda: True)
    return sent


class TestTheJob:
    async def test_a_spending_account_near_zero_is_warned(
        self, db_session, async_session, captured_email
    ):
        org = await _account(
            async_session, "lb-warn", balance_paise=50_000, email="ops@acme.test"
        )
        await _spend(async_session, org, per_day_paise=10_000)

        counters = await job.notify_low_balances()

        assert counters["warned"] == 1
        assert captured_email[0]["to"] == ["ops@acme.test"]

    async def test_a_healthy_account_is_not_warned(
        self, db_session, async_session, captured_email
    ):
        org = await _account(
            async_session, "lb-healthy", balance_paise=5_000_000, email="a@acme.test"
        )
        await _spend(async_session, org, per_day_paise=10_000)

        counters = await job.notify_low_balances()

        assert counters["warned"] == 0
        assert captured_email == []

    async def test_running_twice_sends_once(
        self, db_session, async_session, captured_email
    ):
        """The property that makes a missed cron harmless. Without it, every
        worker restart re-sends every warning."""
        org = await _account(
            async_session, "lb-twice", balance_paise=50_000, email="b@acme.test"
        )
        await _spend(async_session, org, per_day_paise=10_000)

        await job.notify_low_balances()
        second = await job.notify_low_balances()

        assert len(captured_email) == 1
        assert second["warned"] == 0

    async def test_getting_worse_earns_a_second_notice_the_same_day(
        self, db_session, async_session, captured_email
    ):
        """The one case where a repeat is information rather than noise."""
        org = await _account(
            async_session, "lb-worse", balance_paise=100_000, email="c@acme.test"
        )
        await _spend(async_session, org, per_day_paise=10_000)

        await job.notify_low_balances()
        assert len(captured_email) == 1

        # Spend most of what is left; the account crosses into critical.
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=-90_000,
                kind=CreditLedgerKind.USAGE.value,
                balance_after_paise=10_000,
            )
        )
        await async_session.flush()

        await job.notify_low_balances()

        assert len(captured_email) == 2
        assert "Action needed" in captured_email[1]["subject"]

    async def test_an_account_with_no_email_is_skipped_not_failed(
        self, db_session, async_session, captured_email
    ):
        org = await _account(
            async_session, "lb-noemail", balance_paise=50_000, email=None
        )
        await _spend(async_session, org, per_day_paise=10_000)

        counters = await job.notify_low_balances()

        assert counters["warned"] == 0
        assert counters["failed"] == 0
        assert captured_email == []

    async def test_a_dormant_account_is_never_considered(
        self, db_session, async_session, captured_email
    ):
        """A quiet account with nothing in it must not be emailed every
        morning for ever."""
        await _account(
            async_session, "lb-dormant", balance_paise=0, email="d@acme.test"
        )

        counters = await job.notify_low_balances()

        assert counters["considered"] == 0
        assert captured_email == []

    async def test_a_failed_send_is_recorded_not_retried_into_a_second_email(
        self, db_session, async_session, monkeypatch
    ):
        from api.services.messaging.email import SendResult

        async def _fail(*, to, subject, body_text, **kwargs):
            return SendResult(ok=False, error="mail server said no")

        monkeypatch.setattr(job.email, "send_email", _fail)
        monkeypatch.setattr(job.email, "email_is_configured", lambda: True)

        org = await _account(
            async_session, "lb-failed", balance_paise=50_000, email="e@acme.test"
        )
        await _spend(async_session, org, per_day_paise=10_000)

        counters = await job.notify_low_balances()

        assert counters["failed"] == 1
        from sqlalchemy import select

        from api.db import db_client

        async with db_client.async_session() as session:
            record = await session.scalar(
                select(NotificationModel).where(
                    NotificationModel.organization_id == org.id
                )
            )
        assert record is not None
        assert record.sent is False
        assert record.error

    async def test_no_smtp_host_is_a_no_op_not_a_failure(
        self, db_session, async_session, monkeypatch
    ):
        """A billing worker must not fail because a courtesy could not be
        sent."""
        monkeypatch.setattr(job.email, "email_is_configured", lambda: False)

        org = await _account(
            async_session, "lb-nosmtp", balance_paise=50_000, email="f@acme.test"
        )
        await _spend(async_session, org, per_day_paise=10_000)

        counters = await job.notify_low_balances()

        assert counters == {
            "considered": 0,
            "warned": 0,
            "skipped": 0,
            "failed": 0,
        }
