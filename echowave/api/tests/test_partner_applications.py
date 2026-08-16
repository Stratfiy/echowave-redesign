"""Applying to be a partner, and being paid as one.

Two different kinds of care here. The application half is a queue, and the
failure modes are ordinary: a double submit, a stale approval, a rejection
that quietly takes something away.

The commission half is money, and the failure mode is the expensive one — a
rate that moves under a statement that was already issued. That is why a
commission is effective-dated rather than a column, and why most of what
follows is about time rather than about percentages.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import OrganizationModel, PartnerCommissionModel, UserModel
from api.enums import (
    AccountType,
    CommissionBasis,
    PartnerApplicationStatus,
    PartnerKind,
)
from api.services.partners import applications as partners
from api.services.partners.applications import PartnerError


async def _org(async_session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


async def _user(async_session, slug: str) -> UserModel:
    user = UserModel(provider_id=f"user-{slug}", email=f"{slug}@example.com")
    async_session.add(user)
    await async_session.flush()
    return user


async def _applied(async_session, slug: str, **kwargs):
    org = await _org(async_session, slug)
    user = await _user(async_session, slug)
    application = await partners.submit(
        async_session,
        organization_id=org.id,
        user_id=user.id,
        kind=kwargs.pop("kind", PartnerKind.RESELLER.value),
        **kwargs,
    )
    return org, user, application


@pytest.mark.asyncio
class TestApplying:
    async def test_an_application_lands_in_the_queue(self, async_session):
        _org_, _user_, application = await _applied(
            async_session, "queue-lands", expected_minutes_per_month=5000
        )

        assert application.status == PartnerApplicationStatus.PENDING.value
        assert application.expected_minutes_per_month == 5000
        assert application in await partners.queue(async_session)

    async def test_a_second_application_is_refused_with_a_sentence(self, async_session):
        """Clicking submit twice must not put two rows in the queue — a
        reviewer would approve the one that is already stale."""
        org, user, _ = await _applied(async_session, "double-submit")

        with pytest.raises(PartnerError) as exc:
            await partners.submit(
                async_session,
                organization_id=org.id,
                user_id=user.id,
                kind=PartnerKind.AGENCY.value,
            )

        assert "already have an application" in str(exc.value)

    async def test_an_unknown_kind_is_refused(self, async_session):
        org = await _org(async_session, "bad-kind")
        with pytest.raises(PartnerError):
            await partners.submit(
                async_session,
                organization_id=org.id,
                user_id=None,
                kind="definitely-not-a-partner-kind",
            )

    async def test_the_queue_is_oldest_first(self, async_session):
        """A newest-first queue starves whoever has waited longest, which is
        the one thing a queue must not do."""
        _o1, _u1, first = await _applied(async_session, "order-first")
        _o2, _u2, second = await _applied(async_session, "order-second")
        first.submitted_at = datetime(2026, 1, 1, tzinfo=UTC)
        second.submitted_at = datetime(2026, 2, 1, tzinfo=UTC)
        await async_session.flush()

        rows = await partners.queue(async_session)
        ours = [r for r in rows if r.id in {first.id, second.id}]

        assert [r.id for r in ours] == [first.id, second.id]


@pytest.mark.asyncio
class TestApproving:
    async def test_approval_grants_the_commission_and_the_tier(self, async_session):
        org, _user_, application = await _applied(async_session, "approve-grants")
        staff = await _user(async_session, "approve-staff")

        commission = await partners.approve(
            async_session,
            application=application,
            decided_by=staff.id,
            commission_bps=1250,
            basis=CommissionBasis.PLATFORM_FEE.value,
            note="Quoted against 5k minutes a month.",
        )

        assert application.status == PartnerApplicationStatus.APPROVED.value
        assert application.decided_by == staff.id
        assert commission.commission_bps == 1250
        assert commission.basis == CommissionBasis.PLATFORM_FEE.value
        # The badge staff segment by, set from the application so the tier can
        # never disagree with what was approved.
        await async_session.refresh(org)
        assert org.account_type == AccountType.AGENCY.value

    async def test_the_commission_points_back_at_its_application(self, async_session):
        """ "Why is this account on 12.5%?" is asked months later."""
        _org_, _user_, application = await _applied(async_session, "approve-links")
        commission = await partners.approve(
            async_session,
            application=application,
            decided_by=None,
            commission_bps=1250,
            basis=CommissionBasis.TOTAL_SPEND.value,
        )

        assert commission.application_id == application.id

    async def test_approving_twice_is_refused(self, async_session):
        """Two reviewers opening the same queue must not both grant it."""
        _org_, _user_, application = await _applied(async_session, "approve-twice")
        await partners.approve(
            async_session,
            application=application,
            decided_by=None,
            commission_bps=1000,
            basis=CommissionBasis.PLATFORM_FEE.value,
        )

        with pytest.raises(PartnerError) as exc:
            await partners.approve(
                async_session,
                application=application,
                decided_by=None,
                commission_bps=9000,
                basis=CommissionBasis.PLATFORM_FEE.value,
            )
        assert "already approved" in str(exc.value)

    async def test_a_rejection_takes_nothing_away(self, async_session):
        """A customer turned down keeps the product they already had — no
        commission row, no tier change, nothing to notice."""
        org, _user_, application = await _applied(async_session, "reject-harmless")

        await partners.reject(
            async_session,
            application=application,
            decided_by=None,
            note="Come back when you have a client on the platform.",
        )

        await async_session.refresh(org)
        assert application.status == PartnerApplicationStatus.REJECTED.value
        assert application.decision_note.startswith("Come back")
        assert org.account_type is None
        assert await partners.live_commission(async_session, org.id) is None

    async def test_a_rejected_applicant_can_apply_again(self, async_session):
        """The row stays for the record; it must not block a second attempt."""
        org, user, application = await _applied(async_session, "reject-reapply")
        await partners.reject(async_session, application=application, decided_by=None)

        again = await partners.submit(
            async_session,
            organization_id=org.id,
            user_id=user.id,
            kind=PartnerKind.AGENCY.value,
        )
        assert again.status == PartnerApplicationStatus.PENDING.value


@pytest.mark.asyncio
class TestTheCommissionIsEffectiveDated:
    async def test_a_renegotiation_closes_the_old_rate(self, async_session):
        org = await _org(async_session, "reneg-closes")

        first = await partners.set_commission(
            async_session,
            organization_id=org.id,
            commission_bps=1000,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
        )
        second = await partners.set_commission(
            async_session,
            organization_id=org.id,
            commission_bps=1500,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
        )

        await async_session.refresh(first)
        assert first.effective_to is not None
        assert second.effective_to is None
        assert (await partners.live_commission(async_session, org.id)).id == second.id

    async def test_an_old_month_still_reads_its_own_rate(self, async_session):
        """The expensive failure this shape exists to prevent: paying April's
        rate on March's minutes because the statement read the live row."""
        org = await _org(async_session, "reneg-history")
        march = datetime(2026, 3, 15, tzinfo=UTC)
        april = datetime(2026, 4, 15, tzinfo=UTC)

        await partners.set_commission(
            async_session,
            organization_id=org.id,
            commission_bps=1000,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
            effective_from=march - timedelta(days=30),
        )
        await partners.set_commission(
            async_session,
            organization_id=org.id,
            commission_bps=2000,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
            effective_from=april - timedelta(days=1),
        )

        assert (
            await partners.commission_at(async_session, org.id, march)
        ).commission_bps == 1000
        assert (
            await partners.commission_at(async_session, org.id, april)
        ).commission_bps == 2000

    async def test_there_is_no_instant_with_two_rates(self, async_session):
        """The old row closes at exactly the moment the new one opens, so a
        lookup can never find two rows covering one instant, nor a gap."""
        org = await _org(async_session, "no-overlap")
        await partners.set_commission(
            async_session,
            organization_id=org.id,
            commission_bps=1000,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
        )
        second = await partners.set_commission(
            async_session,
            organization_id=org.id,
            commission_bps=1500,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
        )

        at_the_seam = await partners.commission_at(
            async_session, org.id, second.effective_from
        )
        assert at_the_seam.id == second.id

    async def test_before_any_arrangement_there_is_none(self, async_session):
        """Not zero. An account with no commission is not an account on 0%,
        and a payout run has to be able to tell those apart."""
        org = await _org(async_session, "before-any")
        await partners.set_commission(
            async_session,
            organization_id=org.id,
            commission_bps=1000,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
            effective_from=datetime(2026, 6, 1, tzinfo=UTC),
        )

        assert (
            await partners.commission_at(
                async_session, org.id, datetime(2026, 1, 1, tzinfo=UTC)
            )
            is None
        )

    @pytest.mark.parametrize("bps", [-1, 10_001])
    async def test_a_nonsense_percentage_is_refused(self, async_session, bps):
        """A negative commission is a charge; over 100% of spend is more than
        the invoice. Cheaper to refuse here than to find on a payout run."""
        org = await _org(async_session, f"bps-{bps}")
        with pytest.raises(PartnerError):
            await partners.set_commission(
                async_session,
                organization_id=org.id,
                commission_bps=bps,
                basis=CommissionBasis.PLATFORM_FEE.value,
                set_by=None,
            )

    async def test_an_unknown_basis_is_refused(self, async_session):
        """The basis is most of the margin — a typo must not default."""
        org = await _org(async_session, "bad-basis")
        with pytest.raises(PartnerError):
            await partners.set_commission(
                async_session,
                organization_id=org.id,
                commission_bps=1000,
                basis="whatever",
                set_by=None,
            )

    async def test_the_database_refuses_a_second_open_row(self, async_session):
        """The service closes the old row, but the invariant is what keeps a
        concurrent grant from leaving two live rates on one account."""
        org = await _org(async_session, "two-open")
        await partners.set_commission(
            async_session,
            organization_id=org.id,
            commission_bps=1000,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
        )
        async_session.add(
            PartnerCommissionModel(
                organization_id=org.id,
                commission_bps=2000,
                basis=CommissionBasis.PLATFORM_FEE.value,
                effective_from=datetime.now(UTC),
            )
        )
        with pytest.raises(Exception):
            await async_session.flush()
