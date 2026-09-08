"""Two numbers that must agree, and nothing else that notices when they don't.

Starter's price is pinned rather than derived. It used to be balance plus the
number rental, which was safe only while a plan was the sole way to hold a
number and the two prices were the same figure. They are not: ₹559 is what an
*extra* number costs, and a plan's included number is priced inside its monthly
price.

The migration that seeded the plans table derived it anyway, from a fallback for
the rental price that had already moved. A deployment whose environment carried
the current ₹559 seeded Starter at ₹3,059 rather than ₹2,999 — and the Razorpay
plan behind it collects a fixed amount by standing instruction.

Nothing else catches that. The row is valid, the plan sells, the mandate
authorises, calls connect. Only the two numbers being different is wrong, and
neither side can see the other. So the check that compares them is the whole
defence, and these are its tests.
"""

from __future__ import annotations

import pytest

from api.constants import (
    NUMBER_RENTAL_PRICE_PAISE,
    STARTER_PLAN_BALANCE_PAISE,
    STARTER_PLAN_PRICE_PAISE,
)
from api.db.models import SubscriptionPlanModel
from api.services.billing import readiness
from api.services.readiness import ACTION_REQUIRED, READY, UNKNOWN


async def _plan(session, **kw) -> SubscriptionPlanModel:
    """Set the Starter row to a known state.

    Updates the row the migration seeded rather than inserting a second one:
    `code` is unique, and the seeded row is the thing under test on a real
    deployment anyway.
    """
    from sqlalchemy import select

    base = dict(
        price_paise=STARTER_PLAN_PRICE_PAISE,
        balance_paise=STARTER_PLAN_BALANCE_PAISE,
        included_numbers=1,
        extra_number_price_paise=NUMBER_RENTAL_PRICE_PAISE,
        enabled=True,
    )
    base.update(kw)

    row = await session.scalar(
        select(SubscriptionPlanModel).where(SubscriptionPlanModel.code == "starter")
    )
    if row is None:
        row = SubscriptionPlanModel(
            code="starter", label="Starter", blurb="", sort_order=0, **base
        )
        session.add(row)
    else:
        for key, value in base.items():
            setattr(row, key, value)
    await session.flush()
    return row


async def _delete_starter(session) -> None:
    from sqlalchemy import delete

    await session.execute(
        delete(SubscriptionPlanModel).where(SubscriptionPlanModel.code == "starter")
    )
    await session.flush()


@pytest.mark.asyncio
class TestThePriceThatIsCollectedByStandingInstruction:
    async def test_a_matching_price_is_ready(self, db_session, async_session):
        await _plan(async_session)
        check = await readiness._plan_price_drift_check(async_session)
        assert check.status == READY

    async def test_the_derived_price_is_caught(self, db_session, async_session):
        """The exact failure. A deployment carrying the current extra-number
        price seeded Starter at balance + ₹559 instead of the pinned ₹2,999."""
        derived = STARTER_PLAN_BALANCE_PAISE + NUMBER_RENTAL_PRICE_PAISE
        assert derived != STARTER_PLAN_PRICE_PAISE, (
            "this test is only meaningful while the derived and pinned figures "
            "differ — if they have been made equal, the drift is gone"
        )
        await _plan(async_session, price_paise=derived)

        check = await readiness._plan_price_drift_check(async_session)
        assert check.status == ACTION_REQUIRED
        assert "3,059" in check.detail or str(derived // 100) in check.detail

    async def test_the_detail_names_both_figures(self, db_session, async_session):
        """An operator has to be able to tell which number to change without
        reading the source. Naming only one of them is how the wrong one gets
        corrected."""
        await _plan(async_session, price_paise=STARTER_PLAN_PRICE_PAISE + 10_000)
        check = await readiness._plan_price_drift_check(async_session)
        assert f"{STARTER_PLAN_PRICE_PAISE / 100:,.2f}" in check.detail
        assert check.remedy

    async def test_it_states_the_gross_the_bank_must_collect(
        self, db_session, async_session
    ):
        """The net figure is not what Razorpay is configured with, and quoting
        only the net is how a plan gets created at the wrong amount."""
        await _plan(async_session)
        check = await readiness._plan_price_drift_check(async_session)
        # ₹2,999 at 18% is ₹3,538.82 — the figure the Razorpay plan carries.
        assert "3,538.82" in check.detail

    async def test_no_plan_row_is_unknown_rather_than_ready(
        self, db_session, async_session
    ):
        """Nothing to compare is not the same as agreeing, and reporting ready
        on an absent row is the dishonesty this vocabulary exists to avoid."""
        await _delete_starter(async_session)
        check = await readiness._plan_price_drift_check(async_session)
        assert check.status == UNKNOWN


class TestTheTwoDefaultsThatDisagreed:
    def test_the_migration_fallback_matches_the_constant(self):
        """One variable, two defaults, in two files, is how they drift — and
        this pair already had. The migration's fallback for
        NUMBER_RENTAL_PRICE_PAISE was ₹499 while the constant said ₹559."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "e1f4a7c92b31_subscription_plans.py"
        ).read_text()

        assert '_int_env("NUMBER_RENTAL_PRICE_PAISE", 55_900)' in source, (
            "the migration's fallback no longer matches "
            "constants.NUMBER_RENTAL_PRICE_PAISE"
        )

    def test_the_migration_pins_the_price_rather_than_deriving_it(self):
        """The regression itself. Deriving re-prices Starter whenever the
        extra-number figure moves, and the bank keeps collecting the old one."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "e1f4a7c92b31_subscription_plans.py"
        ).read_text()

        assert "price=balance + number" not in source
        assert '_int_env("STARTER_PLAN_PRICE_PAISE"' in source
