"""Agencies, and the two things that would quietly cost money.

An agency signs up its own clients and takes a flat commission on what those
clients are charged. It sees a roll-up and nothing else — no signing in as a
client, no recordings, no transcripts. Those are conversations with the
client's own customers, and an agency relationship is not consent to listen to
them.

**Commission is snapshotted, never derived.** Derived on read, renegotiating an
agency's rate would silently rewrite every past month — the same failure the
rate card and the managed markup each avoid by being effective-dated. The
accrual row is the record.

**The monthly job is re-runnable.** It will be retried, and a second run that
added rather than replaced would double what is owed to somebody who is
watching the number.
"""

from datetime import date, datetime, timedelta

import pytest

from api.db.models import (
    AgencyCommissionAccrualModel,
    DailyOrganizationRollupModel,
    OrganizationModel,
)
from api.services.billing import agency


async def _org(async_session, slug):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


async def _spend(async_session, org, day, charged):
    async_session.add(
        DailyOrganizationRollupModel(
            organization_id=org.id,
            day=day,
            calls=1,
            answered_calls=1,
            completed_calls=1,
            billable_seconds=60,
            billable_minutes=1,
            provider_cost_paise=0,
            charged_paise=charged,
            margin_paise=charged,
        )
    )
    await async_session.flush()


@pytest.mark.asyncio
class TestSettingTheRelationship:
    async def test_a_client_can_be_put_under_an_agency(self, db_session, async_session):
        agency_org = await _org(async_session, "ag")
        client = await _org(async_session, "cl")

        await agency.set_manager(
            async_session,
            client_organization_id=client.id,
            agency_organization_id=agency_org.id,
            commission_bps=1000,
        )

        assert client.managed_by_organization_id == agency_org.id
        assert client.agency_commission_bps == 1000

    async def test_an_account_cannot_manage_itself(self, db_session, async_session):
        org = await _org(async_session, "self-manage")
        with pytest.raises(agency.AgencyError):
            await agency.set_manager(
                async_session,
                client_organization_id=org.id,
                agency_organization_id=org.id,
                commission_bps=1000,
            )

    async def test_agencies_are_one_level_deep(self, db_session, async_session):
        """An agency that is itself managed makes "who earns on this call" a
        tree walk, and every answer below the first level is a commission on a
        commission nobody agreed to."""
        top = await _org(async_session, "top")
        middle = await _org(async_session, "middle")
        bottom = await _org(async_session, "bottom")

        await agency.set_manager(
            async_session,
            client_organization_id=middle.id,
            agency_organization_id=top.id,
            commission_bps=1000,
        )

        with pytest.raises(agency.AgencyError):
            await agency.set_manager(
                async_session,
                client_organization_id=bottom.id,
                agency_organization_id=middle.id,
                commission_bps=1000,
            )

    async def test_detaching_leaves_past_accruals_alone(
        self, db_session, async_session
    ):
        """The agency did earn them. Deleting the record of work already done
        is not what "no longer manages" means."""
        agency_org = await _org(async_session, "leaver-ag")
        client = await _org(async_session, "leaver-cl")
        await agency.set_manager(
            async_session,
            client_organization_id=client.id,
            agency_organization_id=agency_org.id,
            commission_bps=1000,
        )
        await _spend(async_session, client, date(2026, 3, 5), 100_000)
        await agency.accrue_month(async_session, month=date(2026, 3, 1))

        await agency.set_manager(
            async_session,
            client_organization_id=client.id,
            agency_organization_id=None,
            commission_bps=None,
        )

        result = await agency.statement(
            async_session, agency_organization_id=agency_org.id
        )
        assert result["earned_paise"] == 10_000
        assert result["clients"] == []

    async def test_an_absurd_rate_is_refused(self, db_session, async_session):
        """5000 instead of 500 is one keystroke, and at 50% an agency earns
        more on a call than the platform fee it comes out of."""
        agency_org = await _org(async_session, "rate-ag")
        client = await _org(async_session, "rate-cl")

        with pytest.raises(agency.AgencyError):
            await agency.set_manager(
                async_session,
                client_organization_id=client.id,
                agency_organization_id=agency_org.id,
                commission_bps=9000,
            )


@pytest.mark.asyncio
class TestAccrual:
    async def _managed(self, async_session, slug, bps=1000):
        agency_org = await _org(async_session, f"{slug}-ag")
        client = await _org(async_session, f"{slug}-cl")
        await agency.set_manager(
            async_session,
            client_organization_id=client.id,
            agency_organization_id=agency_org.id,
            commission_bps=bps,
        )
        return agency_org, client

    async def test_commission_is_a_share_of_what_the_client_was_charged(
        self, db_session, async_session
    ):
        agency_org, client = await self._managed(async_session, "share")
        await _spend(async_session, client, date(2026, 4, 3), 60_000)
        await _spend(async_session, client, date(2026, 4, 20), 40_000)

        await agency.accrue_month(async_session, month=date(2026, 4, 1))

        result = await agency.statement(
            async_session, agency_organization_id=agency_org.id
        )
        assert result["earned_paise"] == 10_000

    async def test_only_the_month_asked_for_is_counted(self, db_session, async_session):
        agency_org, client = await self._managed(async_session, "window")
        await _spend(async_session, client, date(2026, 4, 30), 100_000)
        await _spend(async_session, client, date(2026, 5, 1), 100_000)

        await agency.accrue_month(async_session, month=date(2026, 4, 15))

        result = await agency.statement(
            async_session, agency_organization_id=agency_org.id
        )
        assert result["earned_paise"] == 10_000

    async def test_running_it_twice_does_not_double_what_is_owed(
        self, db_session, async_session
    ):
        """The job is retried. This is the difference between a restart being
        free and a restart doubling somebody's commission."""
        agency_org, client = await self._managed(async_session, "rerun")
        await _spend(async_session, client, date(2026, 4, 3), 100_000)

        await agency.accrue_month(async_session, month=date(2026, 4, 1))
        await agency.accrue_month(async_session, month=date(2026, 4, 1))

        result = await agency.statement(
            async_session, agency_organization_id=agency_org.id
        )
        assert result["earned_paise"] == 10_000
        assert len(result["months"]) == 1

    async def test_changing_the_rate_does_not_rewrite_a_past_month(
        self, db_session, async_session
    ):
        """The property the snapshot exists for. Derived on read, a
        renegotiation would silently restate every month already invoiced."""
        agency_org, client = await self._managed(async_session, "rewrite", bps=1000)
        await _spend(async_session, client, date(2026, 4, 3), 100_000)
        await agency.accrue_month(async_session, month=date(2026, 4, 1))

        await agency.set_manager(
            async_session,
            client_organization_id=client.id,
            agency_organization_id=agency_org.id,
            commission_bps=2000,
        )

        result = await agency.statement(
            async_session, agency_organization_id=agency_org.id
        )
        assert result["months"][0]["amount_paise"] == 10_000
        assert result["months"][0]["commission_bps"] == 1000

    async def test_a_paid_month_is_closed_to_re_accrual(
        self, db_session, async_session
    ):
        """Once settled the figure is one somebody has reconciled against.
        Moving it later is a correction, not a recomputation."""
        agency_org, client = await self._managed(async_session, "closed")
        await _spend(async_session, client, date(2026, 4, 3), 100_000)
        await agency.accrue_month(async_session, month=date(2026, 4, 1))

        accrual = await async_session.scalar(
            __import__("sqlalchemy").select(AgencyCommissionAccrualModel)
        )
        await agency.mark_paid(async_session, accrual_id=accrual.id)

        await _spend(async_session, client, date(2026, 4, 10), 500_000)
        await agency.accrue_month(async_session, month=date(2026, 4, 1))

        assert accrual.amount_paise == 10_000

    async def test_a_month_with_no_spend_still_accrues_zero(
        self, db_session, async_session
    ):
        """ "This client spent nothing in March" and "March was never accrued"
        are different facts, and only one of them needs chasing."""
        agency_org, _ = await self._managed(async_session, "quiet")

        await agency.accrue_month(async_session, month=date(2026, 4, 1))

        result = await agency.statement(
            async_session, agency_organization_id=agency_org.id
        )
        assert len(result["months"]) == 1
        assert result["months"][0]["amount_paise"] == 0

    async def test_an_unmanaged_account_accrues_nothing(
        self, db_session, async_session
    ):
        client = await _org(async_session, "unmanaged")
        await _spend(async_session, client, date(2026, 4, 3), 100_000)

        accrued = await agency.accrue_month(async_session, month=date(2026, 4, 1))

        assert all(a.client_organization_id != client.id for a in accrued)


@pytest.mark.asyncio
class TestTheStatement:
    async def test_owed_is_earned_minus_paid(self, db_session, async_session):
        """Computed rather than stored, so it cannot disagree with the rows
        beneath it."""
        agency_org = await _org(async_session, "owed-ag")
        for index in range(2):
            client = await _org(async_session, f"owed-cl{index}")
            await agency.set_manager(
                async_session,
                client_organization_id=client.id,
                agency_organization_id=agency_org.id,
                commission_bps=1000,
            )
            await _spend(async_session, client, date(2026, 4, 3), 100_000)
        await agency.accrue_month(async_session, month=date(2026, 4, 1))

        import sqlalchemy as sa

        first = (
            (await async_session.execute(sa.select(AgencyCommissionAccrualModel)))
            .scalars()
            .first()
        )
        await agency.mark_paid(async_session, accrual_id=first.id)

        result = await agency.statement(
            async_session, agency_organization_id=agency_org.id
        )
        assert result["earned_paise"] == 20_000
        assert result["paid_paise"] == 10_000
        assert result["owed_paise"] == 10_000

    async def test_the_statement_shows_its_own_arithmetic(
        self, db_session, async_session
    ):
        """An agency cannot see our costs, so a commission they cannot check
        is one they will dispute. Charged and rate are both on the row."""
        agency_org = await _org(async_session, "arith-ag")
        client = await _org(async_session, "arith-cl")
        await agency.set_manager(
            async_session,
            client_organization_id=client.id,
            agency_organization_id=agency_org.id,
            commission_bps=1500,
        )
        await _spend(async_session, client, date(2026, 4, 3), 200_000)
        await agency.accrue_month(async_session, month=date(2026, 4, 1))

        row = (
            await agency.statement(async_session, agency_organization_id=agency_org.id)
        )["months"][0]
        assert row["charged_paise"] == 200_000
        assert row["commission_bps"] == 1500
        assert row["amount_paise"] == 30_000

    async def test_an_ordinary_account_sees_an_empty_statement(
        self, db_session, async_session
    ):
        """Not an error. Every account can reach the endpoint; almost none is
        an agency, and that is an ordinary state rather than a fault."""
        org = await _org(async_session, "ordinary")

        result = await agency.statement(async_session, agency_organization_id=org.id)
        assert result["clients"] == []
        assert result["owed_paise"] == 0

    async def test_one_agency_cannot_see_anothers_clients(
        self, db_session, async_session
    ):
        mine = await _org(async_session, "mine-ag")
        theirs = await _org(async_session, "theirs-ag")
        their_client = await _org(async_session, "theirs-cl")
        await agency.set_manager(
            async_session,
            client_organization_id=their_client.id,
            agency_organization_id=theirs.id,
            commission_bps=1000,
        )

        result = await agency.statement(async_session, agency_organization_id=mine.id)
        assert result["clients"] == []


@pytest.mark.asyncio
class TestSettlement:
    async def test_a_month_cannot_be_marked_paid_twice(self, db_session, async_session):
        agency_org = await _org(async_session, "twice-ag")
        client = await _org(async_session, "twice-cl")
        await agency.set_manager(
            async_session,
            client_organization_id=client.id,
            agency_organization_id=agency_org.id,
            commission_bps=1000,
        )
        await _spend(async_session, client, date(2026, 4, 3), 100_000)
        await agency.accrue_month(async_session, month=date(2026, 4, 1))

        import sqlalchemy as sa

        accrual = await async_session.scalar(sa.select(AgencyCommissionAccrualModel))
        await agency.mark_paid(async_session, accrual_id=accrual.id)

        with pytest.raises(agency.AgencyError):
            await agency.mark_paid(async_session, accrual_id=accrual.id)

    async def test_marking_paid_records_when(self, db_session, async_session):
        agency_org = await _org(async_session, "when-ag")
        client = await _org(async_session, "when-cl")
        await agency.set_manager(
            async_session,
            client_organization_id=client.id,
            agency_organization_id=agency_org.id,
            commission_bps=1000,
        )
        await _spend(async_session, client, date(2026, 4, 3), 100_000)
        await agency.accrue_month(async_session, month=date(2026, 4, 1))

        import sqlalchemy as sa

        accrual = await async_session.scalar(sa.select(AgencyCommissionAccrualModel))
        before = datetime.now().astimezone() - timedelta(seconds=5)
        await agency.mark_paid(async_session, accrual_id=accrual.id, note="NEFT")

        assert accrual.paid_at is not None and accrual.paid_at > before
        assert accrual.paid_note == "NEFT"
