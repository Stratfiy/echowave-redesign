"""Rental billing: proration, idempotence, and the dunning schedule.

The two properties worth more than the rest:

* **A period is billed once.** The monthly cron is safe to run twice, safe to
  run late, and safe to re-run over a month it already processed. A double
  charge on a ₹399 rental is small; a double charge that nobody notices until a
  customer reconciles their statement is not.
* **Nothing releases a number early.** Day 45 is a permission, not a trigger,
  and the tests below assert both halves of that.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from api.db import db_client
from api.db.models import (
    CreditLedgerModel,
    OrganizationModel,
    RecurringChargeModel,
    RecurringChargePeriodModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
)
from api.enums import (
    CreditLedgerKind,
    PhoneNumberStatus,
    RecurringChargeStatus,
)
from api.services.billing import dunning, rentals

PRICE = 39900  # ₹399
COST = 25000  # ₹250


# ---------------------------------------------------------------------------
# Proration (pure)
# ---------------------------------------------------------------------------


class TestProration:
    def test_a_full_month_is_the_full_price(self):
        assert (
            rentals.prorated_amount(
                full_price_paise=PRICE,
                period_start=datetime(2026, 4, 1, tzinfo=UTC),
                period_end=datetime(2026, 5, 1, tzinfo=UTC),
            )
            == PRICE
        )

    def test_half_a_month_is_about_half_the_price(self):
        # April has 30 days; the 16th leaves 15.
        amount = rentals.prorated_amount(
            full_price_paise=PRICE,
            period_start=datetime(2026, 4, 16, tzinfo=UTC),
            period_end=datetime(2026, 5, 1, tzinfo=UTC),
        )
        assert amount == round(PRICE * 15 / 30)

    def test_a_number_bought_on_the_last_day_still_pays_one_day(self):
        """Not free, and not a division by zero."""
        amount = rentals.prorated_amount(
            full_price_paise=PRICE,
            period_start=datetime(2026, 4, 30, 23, 55, tzinfo=UTC),
            period_end=datetime(2026, 5, 1, tzinfo=UTC),
        )
        assert 0 < amount <= PRICE // 20

    def test_february_is_shorter_so_a_day_costs_more(self):
        feb = rentals.prorated_amount(
            full_price_paise=PRICE,
            period_start=datetime(2026, 2, 27, tzinfo=UTC),
            period_end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        apr = rentals.prorated_amount(
            full_price_paise=PRICE,
            period_start=datetime(2026, 4, 27, tzinfo=UTC),
            period_end=datetime(2026, 5, 1, tzinfo=UTC),
        )
        # Two days of February vs four of April. The point is that
        # days_in_month is read per month rather than assumed to be 30.
        assert feb == round(PRICE * 2 / 28)
        assert apr == round(PRICE * 4 / 30)

    def test_month_end_rolls_over_a_year_boundary(self):
        assert rentals.month_end(datetime(2026, 12, 9, tzinfo=UTC)) == datetime(
            2027, 1, 1, tzinfo=UTC
        )

    def test_proration_uses_half_up_not_bankers_rounding(self):
        """Money that has to reconcile exactly cannot depend on parity."""
        # 3 days of a 30-day month at a price whose tenth is a half-paise.
        amount = rentals.prorated_amount(
            full_price_paise=5,
            period_start=datetime(2026, 4, 28, tzinfo=UTC),
            period_end=datetime(2026, 5, 1, tzinfo=UTC),
        )
        assert amount == 1  # 5*3/30 = 0.5 -> 1, not 0


# ---------------------------------------------------------------------------
# Dunning schedule (pure)
# ---------------------------------------------------------------------------


class TestDunningSchedule:
    def _at(self, days: int):
        first = datetime(2026, 4, 1, tzinfo=UTC)
        return dunning.evaluate(
            first_failed_at=first,
            current_status=RecurringChargeStatus.PAST_DUE,
            now=first + timedelta(days=days),
        )

    def test_day_zero_is_past_due_and_still_calling(self):
        state = self._at(0)
        assert state.status is RecurringChargeStatus.PAST_DUE
        assert not state.should_suspend

    def test_day_six_still_calls(self):
        assert not self._at(6).should_suspend

    def test_day_seven_suspends(self):
        state = self._at(7)
        assert state.status is RecurringChargeStatus.SUSPENDED
        assert state.should_suspend
        assert not state.may_release

    def test_days_fifteen_and_twenty_five_warn(self):
        assert self._at(15).should_warn
        assert self._at(25).should_warn

    def test_day_forty_four_may_not_be_released(self):
        assert not self._at(44).may_release

    def test_day_forty_five_becomes_eligible(self):
        state = self._at(45)
        assert state.status is RecurringChargeStatus.PENDING_RELEASE
        assert state.may_release

    def test_eligibility_is_permission_not_instruction(self):
        """Nothing in this module releases anything; it only permits."""
        assert self._at(60).may_release
        # The policy exposes no action, only a verdict.
        assert not hasattr(dunning, "release")

    def test_a_first_failure_never_permits_release(self):
        assert not dunning.may_release(
            first_failed_at=datetime(2026, 4, 1, tzinfo=UTC),
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )

    def test_nothing_outstanding_means_nothing_to_do(self):
        state = dunning.evaluate(
            first_failed_at=None,
            current_status=RecurringChargeStatus.SUSPENDED,
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )
        assert state.status is RecurringChargeStatus.ACTIVE
        assert not state.should_suspend
        assert not state.may_release

    def test_a_paid_up_charge_can_never_be_released(self):
        assert not dunning.may_release(
            first_failed_at=None, now=datetime(2030, 1, 1, tzinfo=UTC)
        )

    def test_the_clock_runs_from_the_first_failure_not_the_latest(self):
        """Otherwise a daily retry resets the clock and nothing ever escalates."""
        first = datetime(2026, 4, 1, tzinfo=UTC)
        assert dunning.days_overdue(first, now=first + timedelta(days=30)) == 30


# ---------------------------------------------------------------------------
# Charging (database)
# ---------------------------------------------------------------------------


async def _org_with_balance(session, slug: str, paise: int) -> int:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    if paise:
        session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=paise,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=paise,
            )
        )
        await session.flush()
    return org.id


async def _number(session, organization_id: int, slug: str) -> int:
    config = TelephonyConfigurationModel(
        organization_id=organization_id,
        name=f"cfg-{slug}",
        provider="plivo",
        credentials={"auth_id": "MA", "auth_token": "t"},
        is_platform_managed=True,
    )
    session.add(config)
    await session.flush()
    number = TelephonyPhoneNumberModel(
        organization_id=organization_id,
        telephony_configuration_id=config.id,
        address=f"+9180{slug[-6:].rjust(6, '0')}",
        address_normalized=f"+9180{slug[-6:].rjust(6, '0')}",
        address_type="phone",
        country_code="IN",
        status=PhoneNumberStatus.ACTIVE.value,
        carrier_number_id="cn-1",
    )
    session.add(number)
    await session.flush()
    return number.id


class TestChargingIsIdempotent:
    async def test_a_double_run_charges_once(self, db_session, async_session):
        """The property the monthly cron depends on."""
        org_id = await _org_with_balance(async_session, "dbl", 200000)
        number_id = await _number(async_session, org_id, "dbl001")
        charge = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
        )

        now = datetime.now(UTC)
        first = await rentals.charge_period(charge.id, now=now)
        second = await rentals.charge_period(charge.id, now=now)

        assert first.charged
        assert not second.charged

        async with db_client.async_session() as session:
            periods = await session.scalar(
                select(func.count(RecurringChargePeriodModel.id)).where(
                    RecurringChargePeriodModel.recurring_charge_id == charge.id
                )
            )
            debits = await session.scalar(
                select(func.count(CreditLedgerModel.id)).where(
                    CreditLedgerModel.organization_id == org_id,
                    CreditLedgerModel.kind == CreditLedgerKind.RENTAL.value,
                )
            )
        assert periods == 1
        assert debits == 1

    async def test_the_whole_cron_run_is_safe_to_repeat(
        self, db_session, async_session
    ):
        org_id = await _org_with_balance(async_session, "cron", 200000)
        number_id = await _number(async_session, org_id, "cron01")
        await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
        )

        first = await rentals.run_due_charges()
        second = await rentals.run_due_charges()

        assert first["charged"] == 1
        assert second["charged"] == 0

    async def test_opening_a_rental_twice_returns_the_same_charge(
        self, db_session, async_session
    ):
        """A provisioning retry converges on one charge, not two."""
        org_id = await _org_with_balance(async_session, "reopen", 0)
        number_id = await _number(async_session, org_id, "reopn1")

        a = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
        )
        b = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
        )
        assert a.id == b.id


class TestFirstPeriodIsProrated:
    async def test_the_first_charge_is_prorated_and_the_next_is_not(
        self, db_session, async_session
    ):
        org_id = await _org_with_balance(async_session, "prorate", 500000)
        number_id = await _number(async_session, org_id, "prort1")

        # Bought mid-April.
        bought = datetime(2026, 4, 16, tzinfo=UTC)
        charge = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
            now=bought,
        )

        first = await rentals.charge_period(charge.id, now=bought)
        assert first.charged
        assert first.prorated
        assert first.amount_paise == round(PRICE * 15 / 30)

        # The next period is the whole of May.
        second = await rentals.charge_period(
            charge.id, now=datetime(2026, 5, 1, tzinfo=UTC)
        )
        assert second.charged
        assert not second.prorated
        assert second.amount_paise == PRICE

    async def test_periods_are_contiguous_with_no_gap_or_overlap(
        self, db_session, async_session
    ):
        org_id = await _org_with_balance(async_session, "contig", 500000)
        number_id = await _number(async_session, org_id, "contg1")
        charge = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
            now=datetime(2026, 4, 16, tzinfo=UTC),
        )

        await rentals.charge_period(charge.id, now=datetime(2026, 4, 16, tzinfo=UTC))
        await rentals.charge_period(charge.id, now=datetime(2026, 5, 2, tzinfo=UTC))
        await rentals.charge_period(charge.id, now=datetime(2026, 6, 2, tzinfo=UTC))

        async with db_client.async_session() as session:
            periods = list(
                (
                    await session.scalars(
                        select(RecurringChargePeriodModel)
                        .where(
                            RecurringChargePeriodModel.recurring_charge_id == charge.id
                        )
                        .order_by(RecurringChargePeriodModel.period_start)
                    )
                ).all()
            )

        assert len(periods) == 3
        for earlier, later in zip(periods, periods[1:]):
            assert earlier.period_end == later.period_start

    async def test_a_missed_month_is_billed_not_forgiven(
        self, db_session, async_session
    ):
        """A cron that did not run for six weeks catches up rather than
        silently skipping the month it missed."""
        org_id = await _org_with_balance(async_session, "missed", 500000)
        number_id = await _number(async_session, org_id, "missd1")
        charge = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )

        await rentals.charge_period(charge.id, now=datetime(2026, 4, 1, tzinfo=UTC))
        # Nothing ran in May. June's tick bills May first.
        outcome = await rentals.charge_period(
            charge.id, now=datetime(2026, 6, 15, tzinfo=UTC)
        )

        assert outcome.charged
        async with db_client.async_session() as session:
            row = await session.get(RecurringChargeModel, charge.id)
        assert row.current_period_start == datetime(2026, 5, 1, tzinfo=UTC)


class TestCostAndPriceAreBothRecorded:
    async def test_both_sides_of_the_margin_are_stored(self, db_session, async_session):
        """Recording only the price is how a margin figure starts lying."""
        org_id = await _org_with_balance(async_session, "margin", 200000)
        number_id = await _number(async_session, org_id, "margn1")
        charge = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )
        await rentals.charge_period(charge.id, now=datetime(2026, 4, 1, tzinfo=UTC))

        async with db_client.async_session() as session:
            period = await session.scalar(
                select(RecurringChargePeriodModel).where(
                    RecurringChargePeriodModel.recurring_charge_id == charge.id
                )
            )
        assert period.charged_paise == PRICE
        assert period.cost_paise == COST
        assert rentals.monthly_margin_paise(PRICE, COST) == PRICE - COST

    async def test_the_ledger_entry_is_a_rental_not_usage(
        self, db_session, async_session
    ):
        """A statement must not tell a customer they were charged for calls
        they never made."""
        org_id = await _org_with_balance(async_session, "kind", 200000)
        number_id = await _number(async_session, org_id, "kind01")
        charge = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )
        await rentals.charge_period(charge.id, now=datetime(2026, 4, 1, tzinfo=UTC))

        async with db_client.async_session() as session:
            entry = await session.scalar(
                select(CreditLedgerModel).where(
                    CreditLedgerModel.organization_id == org_id,
                    CreditLedgerModel.kind == CreditLedgerKind.RENTAL.value,
                )
            )
        assert entry is not None
        assert entry.delta_paise == -PRICE
        assert entry.ref_type == "recurring_charge_period"


class TestInsufficientBalance:
    async def test_an_unpayable_charge_becomes_past_due_and_keeps_calling(
        self, db_session, async_session
    ):
        org_id = await _org_with_balance(async_session, "broke", 100)
        number_id = await _number(async_session, org_id, "broke1")
        charge = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
        )

        outcome = await rentals.charge_period(charge.id)
        assert not outcome.charged
        assert outcome.reason == "insufficient_balance"

        async with db_client.async_session() as session:
            row = await session.get(RecurringChargeModel, charge.id)
            number = await session.get(TelephonyPhoneNumberModel, number_id)
        assert row.status == RecurringChargeStatus.PAST_DUE.value
        assert row.first_failed_at is not None
        # Day 0: calls still work.
        assert number.status == PhoneNumberStatus.ACTIVE.value

    async def test_a_week_overdue_suspends_the_number_but_keeps_it(
        self, db_session, async_session
    ):
        org_id = await _org_with_balance(async_session, "susp", 100)
        number_id = await _number(async_session, org_id, "susp01")
        charge = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )

        await rentals.charge_period(charge.id, now=datetime(2026, 4, 1, tzinfo=UTC))
        await rentals.charge_period(charge.id, now=datetime(2026, 4, 9, tzinfo=UTC))

        async with db_client.async_session() as session:
            row = await session.get(RecurringChargeModel, charge.id)
            number = await session.get(TelephonyPhoneNumberModel, number_id)

        assert row.status == RecurringChargeStatus.SUSPENDED.value
        assert number.status == PhoneNumberStatus.SUSPENDED.value
        # Still held at the carrier, and still the customer's.
        assert number.carrier_number_id == "cn-1"
        assert number.released_at is None

    async def test_paying_restores_the_number_and_clears_the_clock(
        self, db_session, async_session
    ):
        org_id = await _org_with_balance(async_session, "recover", 100)
        number_id = await _number(async_session, org_id, "recvr1")
        charge = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )
        await rentals.charge_period(charge.id, now=datetime(2026, 4, 1, tzinfo=UTC))
        await rentals.charge_period(charge.id, now=datetime(2026, 4, 9, tzinfo=UTC))

        # They top up.
        async with db_client.async_session() as session:
            session.add(
                CreditLedgerModel(
                    organization_id=org_id,
                    delta_paise=200000,
                    kind=CreditLedgerKind.TOPUP.value,
                    balance_after_paise=200100,
                )
            )
            await session.commit()

        await rentals.charge_period(charge.id, now=datetime(2026, 4, 10, tzinfo=UTC))

        async with db_client.async_session() as session:
            row = await session.get(RecurringChargeModel, charge.id)
            number = await session.get(TelephonyPhoneNumberModel, number_id)

        assert row.status == RecurringChargeStatus.ACTIVE.value
        assert row.first_failed_at is None  # the clock resets completely
        assert row.failure_count == 0
        assert number.status == PhoneNumberStatus.ACTIVE.value

    async def test_suspension_never_touches_the_customers_own_switch(
        self, db_session, async_session
    ):
        """is_active is theirs. Flipping it would silently keep the number off
        after they paid."""
        org_id = await _org_with_balance(async_session, "switch", 100)
        number_id = await _number(async_session, org_id, "switc1")
        charge = await rentals.open_number_rental(
            organization_id=org_id,
            phone_number_id=number_id,
            cost_paise=COST,
            price_paise=PRICE,
            now=datetime(2026, 4, 1, tzinfo=UTC),
        )
        await rentals.charge_period(charge.id, now=datetime(2026, 4, 1, tzinfo=UTC))
        await rentals.charge_period(charge.id, now=datetime(2026, 4, 20, tzinfo=UTC))

        async with db_client.async_session() as session:
            number = await session.get(TelephonyPhoneNumberModel, number_id)
        assert number.is_active is True
