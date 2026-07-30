"""Free credit for a new account.

Two properties matter. It must actually arrive — a prepaid account with no
credit cannot make a single call, so a bonus that silently fails to land makes
the product look broken to someone who has just signed up. And it must arrive
exactly once, because the obvious way to abuse a signup bonus is to sign up
twice.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from api.db.models import CreditLedgerModel, OrganizationModel, UsdInrRateHistoryModel
from api.enums import CreditLedgerKind
from api.services.billing import signup_bonus
from api.services.billing.costing import current_balance_paise
from api.services.billing.signup_bonus import REF_TYPE, grant_signup_bonus


@pytest.fixture(autouse=True)
def five_dollars(monkeypatch):
    monkeypatch.setattr(signup_bonus, "SIGNUP_BONUS_MICROS_USD", 5_000_000)


async def _org(async_session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


async def _bonus_rows(async_session, org) -> int:
    return int(
        await async_session.scalar(
            select(func.count())
            .select_from(CreditLedgerModel)
            .where(
                CreditLedgerModel.organization_id == org.id,
                CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
                CreditLedgerModel.ref_type == REF_TYPE,
            )
        )
        or 0
    )


@pytest.mark.asyncio
class TestTheBonusArrives:
    async def test_a_new_account_gets_credit(self, async_session):
        org = await _org(async_session, "bonus")

        granted = await grant_signup_bonus(async_session, organization_id=org.id)

        assert granted > 0
        assert (
            await current_balance_paise(async_session, organization_id=org.id)
            == granted
        )

    async def test_it_is_converted_from_dollars_at_the_current_rate(
        self, async_session
    ):
        """Denominated in dollars like the list price. A rupee figure would get
        quietly cheaper in dollar terms every time the rupee weakened."""
        async_session.add(
            UsdInrRateHistoryModel(
                paise_per_usd=10_000,  # Rs 100 to the dollar, exactly
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
                source="test",
            )
        )
        await async_session.flush()
        org = await _org(async_session, "fx")

        granted = await grant_signup_bonus(async_session, organization_id=org.id)

        # $5 at Rs 100 = Rs 500 = 50,000 paise.
        assert granted == 50_000

    async def test_it_lands_as_trial_not_topup(self, async_session):
        """It is a gift, not a sale. Keeping the kinds apart is what lets
        revenue reporting exclude it forever."""
        org = await _org(async_session, "kind")
        await grant_signup_bonus(async_session, organization_id=org.id)

        row = await async_session.scalar(
            select(CreditLedgerModel).where(CreditLedgerModel.organization_id == org.id)
        )
        assert row.kind == CreditLedgerKind.TRIAL.value
        assert row.ref_type == REF_TYPE

    async def test_the_bonus_is_spendable(self, async_session):
        """The whole point: a brand-new account can place a call."""
        from api.services.billing import reservations

        org = await _org(async_session, "spendable")
        await grant_signup_bonus(async_session, organization_id=org.id)

        assert await reservations.has_credit(async_session, organization_id=org.id)


@pytest.mark.asyncio
class TestItIsGrantedOnce:
    async def test_a_second_grant_is_a_no_op(self, async_session):
        org = await _org(async_session, "twice")

        first = await grant_signup_bonus(async_session, organization_id=org.id)
        second = await grant_signup_bonus(async_session, organization_id=org.id)

        assert first > 0
        assert second == 0
        assert await _bonus_rows(async_session, org) == 1
        assert (
            await current_balance_paise(async_session, organization_id=org.id) == first
        )

    async def test_switching_it_off_grants_nothing(self, async_session, monkeypatch):
        monkeypatch.setattr(signup_bonus, "SIGNUP_BONUS_MICROS_USD", 0)
        org = await _org(async_session, "disabled")

        assert await grant_signup_bonus(async_session, organization_id=org.id) == 0
        assert await _bonus_rows(async_session, org) == 0


@pytest.mark.asyncio
class TestConcurrentSignup:
    """Signup can be retried and requests can race.

    Without the partial unique index both would find no bonus and both would
    grant one, which is the cheapest possible way to double your free credit.
    """

    async def test_concurrent_grants_produce_exactly_one_bonus(
        self, test_engine, setup_test_database, monkeypatch
    ):
        from sqlalchemy.ext.asyncio import async_sessionmaker

        maker = async_sessionmaker(bind=test_engine, expire_on_commit=False)
        stamp = int(datetime.now(UTC).timestamp() * 1000)

        async with maker() as setup:
            org = OrganizationModel(
                provider_id=f"org-concurrent-bonus-{stamp}", quota_decibyl_tokens=0
            )
            setup.add(org)
            await setup.flush()
            org_id = org.id
            await setup.commit()

        async def grant() -> int:
            async with maker() as session:
                # Patched per-session: the module global is read inside.
                granted = await grant_signup_bonus(session, organization_id=org_id)
                if granted:
                    await session.commit()
                return granted

        try:
            results = await asyncio.gather(*(grant() for _ in range(6)))

            async with maker() as check:
                rows = int(
                    await check.scalar(
                        select(func.count())
                        .select_from(CreditLedgerModel)
                        .where(
                            CreditLedgerModel.organization_id == org_id,
                            CreditLedgerModel.kind == CreditLedgerKind.TRIAL.value,
                            CreditLedgerModel.ref_type == REF_TYPE,
                        )
                    )
                    or 0
                )
                balance = await current_balance_paise(check, organization_id=org_id)

            assert rows == 1, f"six concurrent signups produced {rows} bonuses"
            assert sum(1 for r in results if r > 0) == 1
            assert balance == max(results)
        finally:
            async with maker() as cleanup:
                await cleanup.execute(
                    CreditLedgerModel.__table__.delete().where(
                        CreditLedgerModel.organization_id == org_id
                    )
                )
                await cleanup.execute(
                    OrganizationModel.__table__.delete().where(
                        OrganizationModel.id == org_id
                    )
                )
                await cleanup.commit()


@pytest.mark.asyncio
class TestItIsNotASale:
    async def test_no_tax_document_is_issued_for_a_bonus(self, async_session):
        """No money changed hands, so there is no supply to document. A receipt
        voucher for a gift would misstate a payment that never happened."""
        from api.db.models import TaxDocumentModel

        org = await _org(async_session, "notasale")
        await grant_signup_bonus(async_session, organization_id=org.id)

        documents = int(
            await async_session.scalar(
                select(func.count())
                .select_from(TaxDocumentModel)
                .where(TaxDocumentModel.organization_id == org.id)
            )
            or 0
        )
        assert documents == 0

    async def test_no_payment_row_is_created(self, async_session):
        from api.db.models import PaymentModel

        org = await _org(async_session, "nopayment")
        await grant_signup_bonus(async_session, organization_id=org.id)

        payments = int(
            await async_session.scalar(
                select(func.count())
                .select_from(PaymentModel)
                .where(PaymentModel.organization_id == org.id)
            )
            or 0
        )
        assert payments == 0
