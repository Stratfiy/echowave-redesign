"""Refer a friend, and the reasons it does not pay out immediately.

A referrer earns 20% of the first payment made by the account they referred.
The interesting decisions are all about *when* that becomes spendable, because
the obvious answer is wrong in a way that costs real money.

Card disputes arrive weeks after the payment. A scheme that credits at
settlement and reverses never is a way to buy credit at a discount with a
stolen card: pay, refer yourself a friend, spend the reward, charge back the
payment. So an award is earned at settlement and **spendable after a hold**,
and cancelling one before it matures writes nothing at all — no ledger row, no
correction, nothing to explain.

Until it matures the award is not in the credit ledger. That is the property
the whole design rests on: the ledger means money that is yours *now*, and
every balance query in the system depends on it. An award sitting there with an
availability date would make each of those queries wrong by exactly the amount
nobody could spend.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import OrganizationModel, ReferralAwardModel
from api.services.billing import referrals


async def _org(async_session, slug):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


@pytest.mark.asyncio
class TestTheCode:
    async def test_a_code_is_minted_on_first_ask_and_then_stable(
        self, db_session, async_session
    ):
        org = await _org(async_session, "code")

        first = await referrals.ensure_code(async_session, organization_id=org.id)
        second = await referrals.ensure_code(async_session, organization_id=org.id)

        assert first == second
        assert len(first) == referrals.CODE_LENGTH

    async def test_the_alphabet_has_no_lookalikes(self, db_session, async_session):
        """A code gets read aloud and typed from a screenshot. One that turns
        into a *different valid code* when O is read as 0 pays the wrong
        person, and nothing about that failure is visible to anyone."""
        org = await _org(async_session, "alphabet")
        code = await referrals.ensure_code(async_session, organization_id=org.id)

        assert not set(code) & set("O0I1L")

    async def test_codes_are_normalised_before_lookup(self, db_session, async_session):
        """They arrive lower-cased by autocorrect, with spaces from a paste."""
        referrer = await _org(async_session, "norm-referrer")
        referred = await _org(async_session, "norm-referred")
        code = await referrals.ensure_code(async_session, organization_id=referrer.id)

        await referrals.attribute(
            async_session,
            organization_id=referred.id,
            code=f"  {code.lower()} ",
        )

        assert referred.referred_by_organization_id == referrer.id


@pytest.mark.asyncio
class TestAttribution:
    async def test_an_account_cannot_refer_itself(self, db_session, async_session):
        """Otherwise the scheme pays 20% back on every first payment anybody
        makes, which is not a referral scheme but a discount."""
        org = await _org(async_session, "self")
        code = await referrals.ensure_code(async_session, organization_id=org.id)

        with pytest.raises(referrals.ReferralError):
            await referrals.attribute(async_session, organization_id=org.id, code=code)

    async def test_a_referrer_cannot_be_changed_once_set(
        self, db_session, async_session
    ):
        """The award is paid against this weeks later. An attribution that
        could move would pay whoever happened to hold it at the time."""
        first = await _org(async_session, "first-ref")
        second = await _org(async_session, "second-ref")
        referred = await _org(async_session, "switcher")

        await referrals.attribute(
            async_session,
            organization_id=referred.id,
            code=await referrals.ensure_code(async_session, organization_id=first.id),
        )

        with pytest.raises(referrals.ReferralError):
            await referrals.attribute(
                async_session,
                organization_id=referred.id,
                code=await referrals.ensure_code(
                    async_session, organization_id=second.id
                ),
            )

    async def test_an_unknown_code_is_refused(self, db_session, async_session):
        org = await _org(async_session, "unknown")
        with pytest.raises(referrals.ReferralError):
            await referrals.attribute(
                async_session, organization_id=org.id, code="ZZZZZZZZ"
            )


@pytest.mark.asyncio
class TestTheAward:
    async def _referred_pair(self, async_session, slug):
        referrer = await _org(async_session, f"{slug}-referrer")
        referred = await _org(async_session, f"{slug}-referred")
        await referrals.attribute(
            async_session,
            organization_id=referred.id,
            code=await referrals.ensure_code(
                async_session, organization_id=referrer.id
            ),
        )
        return referrer, referred

    async def test_twenty_percent_of_the_first_payment(self, db_session, async_session):
        referrer, referred = await self._referred_pair(async_session, "share")

        award = await referrals.award_for_payment(
            async_session,
            organization_id=referred.id,
            payment_id="pay_1",
            net_paise=100_000,
        )

        assert award is not None
        assert award.amount_paise == 20_000
        assert award.referrer_organization_id == referrer.id

    async def test_only_the_first_payment_earns(self, db_session, async_session):
        """The reward is for the referral, not for every payment that account
        goes on to make."""
        _, referred = await self._referred_pair(async_session, "once")

        await referrals.award_for_payment(
            async_session,
            organization_id=referred.id,
            payment_id="pay_1",
            net_paise=100_000,
        )
        second = await referrals.award_for_payment(
            async_session,
            organization_id=referred.id,
            payment_id="pay_2",
            net_paise=100_000,
        )

        assert second is None

    async def test_an_account_with_no_referrer_awards_nothing_quietly(
        self, db_session, async_session
    ):
        """None rather than an exception. This is the ordinary case on almost
        every payment, and a settlement path that raised on it would turn the
        common case into an error somebody has to swallow."""
        org = await _org(async_session, "unreferred")

        assert (
            await referrals.award_for_payment(
                async_session,
                organization_id=org.id,
                payment_id="pay_x",
                net_paise=100_000,
            )
            is None
        )

    async def test_the_award_is_not_in_the_ledger_yet(self, db_session, async_session):
        """The property everything else rests on. The credit ledger means
        money that is yours now; an award sitting there with a date on it would
        make every balance query wrong by the amount nobody can spend."""
        import sqlalchemy as sa

        from api.db.models import CreditLedgerModel

        referrer, referred = await self._referred_pair(async_session, "notyet")
        await referrals.award_for_payment(
            async_session,
            organization_id=referred.id,
            payment_id="pay_hold",
            net_paise=100_000,
        )

        rows = (
            (
                await async_session.execute(
                    sa.select(CreditLedgerModel).where(
                        CreditLedgerModel.organization_id == referrer.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


@pytest.mark.asyncio
class TestTheHold:
    async def _awarded(self, async_session, slug):
        referrer = await _org(async_session, f"{slug}-r")
        referred = await _org(async_session, f"{slug}-d")
        await referrals.attribute(
            async_session,
            organization_id=referred.id,
            code=await referrals.ensure_code(
                async_session, organization_id=referrer.id
            ),
        )
        await referrals.award_for_payment(
            async_session,
            organization_id=referred.id,
            payment_id=f"pay-{slug}",
            net_paise=100_000,
        )
        return referrer, referred

    async def test_nothing_is_credited_before_the_hold_expires(
        self, db_session, async_session
    ):
        await self._awarded(async_session, "early")

        credited = await referrals.credit_matured(async_session)

        assert credited == []

    async def test_it_lands_in_the_ledger_once_the_hold_passes(
        self, db_session, async_session
    ):
        import sqlalchemy as sa

        from api.db.models import CreditLedgerModel

        referrer, _ = await self._awarded(async_session, "mature")
        later = datetime.now(UTC) + timedelta(days=referrals.HOLD_DAYS + 1)

        credited = await referrals.credit_matured(async_session, now=later)
        assert len(credited) == 1

        row = (
            await async_session.execute(
                sa.select(CreditLedgerModel).where(
                    CreditLedgerModel.organization_id == referrer.id
                )
            )
        ).scalar_one()
        assert row.delta_paise == 20_000
        assert row.kind == "referral"

    async def test_crediting_twice_pays_once(self, db_session, async_session):
        """The job is retried. Idempotence here is the difference between a
        restart being free and a restart paying every referrer again."""
        await self._awarded(async_session, "idem")
        later = datetime.now(UTC) + timedelta(days=referrals.HOLD_DAYS + 1)

        first = await referrals.credit_matured(async_session, now=later)
        second = await referrals.credit_matured(async_session, now=later)

        assert len(first) == 1
        assert second == []

    async def test_a_refund_before_maturity_leaves_no_trace(
        self, db_session, async_session
    ):
        """The whole reason for the hold. Nothing was credited, so nothing has
        to be reversed, and there is no correction to explain to anybody."""
        import sqlalchemy as sa

        from api.db.models import CreditLedgerModel

        referrer, _ = await self._awarded(async_session, "clawback")

        cancelled = await referrals.cancel_for_payment(
            async_session, payment_id="pay-clawback"
        )
        assert cancelled == 20_000

        later = datetime.now(UTC) + timedelta(days=referrals.HOLD_DAYS + 1)
        assert await referrals.credit_matured(async_session, now=later) == []

        rows = (
            (
                await async_session.execute(
                    sa.select(CreditLedgerModel).where(
                        CreditLedgerModel.organization_id == referrer.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []

    async def test_a_refund_after_maturity_is_left_for_a_human(
        self, db_session, async_session
    ):
        """Once credited the money may already be spent. Clawing it back
        automatically would take calls off an account that did nothing wrong;
        a reversing adjustment is somebody's decision, not a webhook's."""
        await self._awarded(async_session, "late")
        later = datetime.now(UTC) + timedelta(days=referrals.HOLD_DAYS + 1)
        await referrals.credit_matured(async_session, now=later)

        assert (
            await referrals.cancel_for_payment(async_session, payment_id="pay-late")
            is None
        )


@pytest.mark.asyncio
class TestTheSummary:
    async def test_held_credited_and_cancelled_are_reported_separately(
        self, db_session, async_session
    ):
        """Three figures rather than a total, because they answer different
        questions: what is mine, what is coming, and what did not survive."""
        referrer = await _org(async_session, "sum-r")
        code = await referrals.ensure_code(async_session, organization_id=referrer.id)
        for index in range(2):
            referred = await _org(async_session, f"sum-d{index}")
            await referrals.attribute(
                async_session, organization_id=referred.id, code=code
            )
            await referrals.award_for_payment(
                async_session,
                organization_id=referred.id,
                payment_id=f"pay-sum-{index}",
                net_paise=50_000,
            )
        await referrals.cancel_for_payment(async_session, payment_id="pay-sum-0")

        result = await referrals.summary(async_session, organization_id=referrer.id)

        assert result["signed_up"] == 2
        assert result["pending_paise"] == 10_000
        assert result["cancelled_paise"] == 10_000
        assert result["credited_paise"] == 0

    async def test_a_cancelled_award_is_not_counted_as_pending(
        self, db_session, async_session
    ):
        """It is neither coming nor mine. Counting it in either would have the
        referral screen promise money that will never arrive."""
        referrer = await _org(async_session, "cancel-r")
        referred = await _org(async_session, "cancel-d")
        await referrals.attribute(
            async_session,
            organization_id=referred.id,
            code=await referrals.ensure_code(
                async_session, organization_id=referrer.id
            ),
        )
        await referrals.award_for_payment(
            async_session,
            organization_id=referred.id,
            payment_id="pay-cancel",
            net_paise=100_000,
        )
        await referrals.cancel_for_payment(async_session, payment_id="pay-cancel")

        result = await referrals.summary(async_session, organization_id=referrer.id)
        assert result["pending_paise"] == 0
        assert result["cancelled_paise"] == 20_000


@pytest.mark.asyncio
class TestTheDatabaseHoldsTheRule:
    async def test_one_award_per_referred_account_is_a_constraint(
        self, db_session, async_session
    ):
        """Not a check in the service. Two concurrent webhooks for the same
        order both pass that check and both insert."""
        import sqlalchemy as sa

        referrer = await _org(async_session, "uniq-r")
        referred = await _org(async_session, "uniq-d")
        now = datetime.now(UTC)
        async_session.add(
            ReferralAwardModel(
                referrer_organization_id=referrer.id,
                referred_organization_id=referred.id,
                payment_id="pay-a",
                amount_paise=1,
                earned_at=now,
                available_at=now,
            )
        )
        await async_session.flush()

        # Inside a savepoint: the violation is the point, but letting it kill
        # the outer transaction would take the rest of the suite's teardown
        # with it.
        with pytest.raises(sa.exc.IntegrityError):
            async with async_session.begin_nested():
                async_session.add(
                    ReferralAwardModel(
                        referrer_organization_id=referrer.id,
                        referred_organization_id=referred.id,
                        payment_id="pay-b",
                        amount_paise=1,
                        earned_at=now,
                        available_at=now,
                    )
                )
                await async_session.flush()
