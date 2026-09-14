"""The first onboarding step: a proved address pays 150 credits.

The entry points signup and the verify route call are unchanged; what they
pay is now the first tranche of the Free allowance (KAN-132) rather than a
dollar figure converted on the day. Two properties still matter most:

* it lands as a gift, never a sale, so revenue reporting can exclude it; and
* it is granted exactly once, however many requests race for it.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from api.db.models import CreditLedgerModel, OrganizationModel
from api.enums import CreditLedgerKind
from api.services.billing import onboarding_credits
from api.services.billing.costing import current_balance_paise
from api.services.billing.signup_bonus import REF_TYPE, grant_signup_bonus

VERIFY_EMAIL_PAISE = 150 * 50


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
    async def test_a_new_account_gets_the_first_tranche(self, async_session):
        org = await _org(async_session, "bonus")

        granted = await grant_signup_bonus(async_session, organization_id=org.id)

        assert granted == VERIFY_EMAIL_PAISE
        assert (
            await current_balance_paise(async_session, organization_id=org.id)
            == granted
        )

    async def test_it_lands_as_trial_not_topup(self, async_session):
        """It is a gift, not a sale. Keeping the kinds apart is what lets
        revenue reporting exclude it forever."""
        org = await _org(async_session, "kind")
        await grant_signup_bonus(async_session, organization_id=org.id)

        row = await async_session.scalar(
            select(CreditLedgerModel).where(CreditLedgerModel.organization_id == org.id)
        )
        assert row.kind == CreditLedgerKind.TRIAL.value
        # The historical ref, so accounts paid before KAN-132 count as done.
        assert row.ref_type == REF_TYPE == "signup_bonus"

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
        monkeypatch.setattr(onboarding_credits, "ENABLED", False)
        org = await _org(async_session, "disabled")

        assert await grant_signup_bonus(async_session, organization_id=org.id) == 0
        assert await _bonus_rows(async_session, org) == 0

    async def test_our_own_accounts_get_nothing(self, async_session):
        org = OrganizationModel(
            provider_id="org-ours", quota_decibyl_tokens=0, internal_billing=True
        )
        async_session.add(org)
        await async_session.flush()

        assert await grant_signup_bonus(async_session, organization_id=org.id) == 0


@pytest.mark.asyncio
class TestConcurrentSignup:
    """Signup can be retried and requests can race.

    Without the partial unique index both would find no bonus and both would
    grant one, which is the cheapest possible way to double your free credit.
    """

    async def test_concurrent_grants_produce_exactly_one_bonus(
        self, test_engine, setup_test_database
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
