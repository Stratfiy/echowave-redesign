"""What a partner is owed, and the ways that number goes wrong.

The commission arrangement was already tested. This is the arithmetic that
turns it into money, so the tests here are mostly about the cases where a
plausible implementation quietly pays the wrong amount: a rate that moved
mid-period, a day the account was not yet attributed, a negative margin, a
generate run that fired twice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from api.db.models import (
    DailyOrganizationRollupModel,
    OrganizationModel,
)
from api.enums import CommissionBasis, PartnerStatementStatus
from api.services.partners import applications, earnings, referrals
from api.services.partners.applications import PartnerError

PERIOD_START = date(2026, 3, 1)
PERIOD_END = date(2026, 3, 31)


async def _org(session, slug: str, **kwargs) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0, **kwargs)
    session.add(org)
    await session.flush()
    return org


async def _partner(session, slug: str, *, bps: int, basis: str):
    """A partner on a commission, with a referral code."""
    org = await _org(session, slug)
    await applications.set_commission(
        session,
        organization_id=org.id,
        commission_bps=bps,
        basis=basis,
        set_by=None,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await referrals.ensure_code(session, org)
    return org


async def _referred(session, slug: str, partner, *, referred_at=None):
    org = await _org(session, slug)
    org.referred_by_organization_id = partner.id
    org.referred_at = referred_at or datetime(2026, 1, 1, tzinfo=UTC)
    await session.flush()
    return org


async def _day(session, org, day: date, *, charged: int, provider_cost: int):
    session.add(
        DailyOrganizationRollupModel(
            organization_id=org.id,
            day=day,
            charged_paise=charged,
            provider_cost_paise=provider_cost,
            margin_paise=charged - provider_cost,
        )
    )
    await session.flush()


@pytest.mark.asyncio
class TestTheArithmetic:
    async def test_a_share_of_margin_is_a_share_of_margin(
        self, db_session, async_session
    ):
        partner = await _partner(
            async_session, "margin", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        client = await _referred(async_session, "margin-client", partner)
        # 1000 charged, 600 to providers, so 400 margin. 10% of 400 is 40.
        await _day(
            async_session, client, date(2026, 3, 5), charged=1000, provider_cost=600
        )

        lines, basis = await earnings.compute_lines(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        assert basis == CommissionBasis.PLATFORM_FEE.value
        assert len(lines) == 1
        assert lines[0]["basis_amount_paise"] == 400
        assert lines[0]["amount_paise"] == 40

    async def test_a_share_of_spend_is_a_share_of_the_whole_charge(
        self, db_session, async_session
    ):
        """The difference between the two bases is most of the margin, which is
        exactly why it is stored per partner rather than assumed."""
        partner = await _partner(
            async_session, "spend", bps=1000, basis=CommissionBasis.TOTAL_SPEND.value
        )
        client = await _referred(async_session, "spend-client", partner)
        await _day(
            async_session, client, date(2026, 3, 5), charged=1000, provider_cost=600
        )

        lines, _ = await earnings.compute_lines(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        # 10% of the full 1000, not of the 400 margin.
        assert lines[0]["basis_amount_paise"] == 1000
        assert lines[0]["amount_paise"] == 100

    async def test_a_renegotiation_does_not_backdate_itself(
        self, db_session, async_session
    ):
        """The failure this whole design exists to prevent.

        A partner moved from 10% to 20% on the 15th earns 10% on the earlier
        days. Pricing the period total at the rate found at period end — the
        obvious shortcut — pays 20% on a fortnight nobody agreed to.
        """
        partner = await _partner(
            async_session, "reneg", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        client = await _referred(async_session, "reneg-client", partner)
        await _day(
            async_session, client, date(2026, 3, 10), charged=1000, provider_cost=0
        )
        await _day(
            async_session, client, date(2026, 3, 20), charged=1000, provider_cost=0
        )

        await applications.set_commission(
            async_session,
            organization_id=partner.id,
            commission_bps=2000,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
            effective_from=datetime(2026, 3, 15, tzinfo=UTC),
        )

        lines, _ = await earnings.compute_lines(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        # 10% of 1000 on the 10th, 20% of 1000 on the 20th.
        assert lines[0]["amount_paise"] == 100 + 200

    async def test_spend_before_attribution_earns_nothing(
        self, db_session, async_session
    ):
        """An account attributed by hand halfway through must not earn the
        partner a commission on the months before they introduced it."""
        partner = await _partner(
            async_session, "late", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        client = await _referred(
            async_session,
            "late-client",
            partner,
            referred_at=datetime(2026, 3, 15, tzinfo=UTC),
        )
        await _day(
            async_session, client, date(2026, 3, 10), charged=1000, provider_cost=0
        )
        await _day(
            async_session, client, date(2026, 3, 20), charged=1000, provider_cost=0
        )

        lines, _ = await earnings.compute_lines(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        assert lines[0]["amount_paise"] == 100

    async def test_a_day_before_any_commission_earns_nothing(
        self, db_session, async_session
    ):
        """An application approved on the 20th does not earn for the 19th."""
        partner = await _org(async_session, "notyet")
        await applications.set_commission(
            async_session,
            organization_id=partner.id,
            commission_bps=1000,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
            effective_from=datetime(2026, 3, 20, tzinfo=UTC),
        )
        client = await _referred(async_session, "notyet-client", partner)
        await _day(
            async_session, client, date(2026, 3, 10), charged=5000, provider_cost=0
        )

        lines, _ = await earnings.compute_lines(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        assert lines == []

    async def test_a_negative_margin_day_does_not_reduce_what_we_owe(
        self, db_session, async_session
    ):
        """A recost or a provider invoice above what we charged can put a day
        underwater. That is our loss on our own rate card, not something to
        claw back out of a partner's good days."""
        partner = await _partner(
            async_session, "under", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        client = await _referred(async_session, "under-client", partner)
        await _day(
            async_session, client, date(2026, 3, 5), charged=1000, provider_cost=0
        )
        await _day(
            async_session, client, date(2026, 3, 6), charged=100, provider_cost=900
        )

        lines, _ = await earnings.compute_lines(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        assert lines[0]["amount_paise"] == 100

    async def test_another_partners_accounts_are_not_in_this_statement(
        self, db_session, async_session
    ):
        partner = await _partner(
            async_session, "mine", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        other = await _partner(
            async_session, "theirs", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        theirs = await _referred(async_session, "their-client", other)
        await _day(
            async_session, theirs, date(2026, 3, 5), charged=9999, provider_cost=0
        )

        lines, _ = await earnings.compute_lines(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        assert lines == []

    async def test_an_unreferred_account_earns_nobody_anything(
        self, db_session, async_session
    ):
        partner = await _partner(
            async_session, "none", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        stranger = await _org(async_session, "stranger")
        await _day(
            async_session, stranger, date(2026, 3, 5), charged=9999, provider_cost=0
        )

        lines, _ = await earnings.compute_lines(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        assert lines == []


@pytest.mark.asyncio
class TestTheStatement:
    async def test_the_total_is_the_sum_of_its_lines(self, db_session, async_session):
        """The property that makes a statement answerable. Same definition
        `total_charged_paise` uses on a call receipt, for the same reason."""
        partner = await _partner(
            async_session, "sum", bps=1500, basis=CommissionBasis.PLATFORM_FEE.value
        )
        for index in range(3):
            client = await _referred(async_session, f"sum-client-{index}", partner)
            await _day(
                async_session,
                client,
                date(2026, 3, 5 + index),
                charged=1000 * (index + 1),
                provider_cost=0,
            )

        statement = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        lines = await earnings.lines_for(async_session, statement.id)

        assert len(lines) == 3
        assert statement.amount_paise == sum(line.amount_paise for line in lines)
        assert statement.basis_amount_paise == sum(
            line.basis_amount_paise for line in lines
        )

    async def test_generating_twice_does_not_double_what_we_owe(
        self, db_session, async_session
    ):
        """The one arithmetic error nobody notices until after it is paid."""
        partner = await _partner(
            async_session, "twice", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        client = await _referred(async_session, "twice-client", partner)
        await _day(
            async_session, client, date(2026, 3, 5), charged=1000, provider_cost=0
        )

        first = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        second = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        assert first.id == second.id
        assert second.amount_paise == 100
        assert len(await earnings.lines_for(async_session, second.id)) == 1

    async def test_regenerating_picks_up_a_late_rollup(self, db_session, async_session):
        """Why regeneration is the ordinary path rather than an escape hatch:
        a call costed after the period closed belongs in that period."""
        partner = await _partner(
            async_session,
            "late-roll",
            bps=1000,
            basis=CommissionBasis.PLATFORM_FEE.value,
        )
        client = await _referred(async_session, "late-roll-client", partner)
        await _day(
            async_session, client, date(2026, 3, 5), charged=1000, provider_cost=0
        )

        await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        await _day(
            async_session, client, date(2026, 3, 6), charged=2000, provider_cost=0
        )
        statement = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        assert statement.amount_paise == 300

    async def test_an_issued_statement_is_not_regenerated(
        self, db_session, async_session
    ):
        """After somebody has been told a number, changing it quietly is not a
        correction — it is a different number with the same name."""
        partner = await _partner(
            async_session, "issued", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        client = await _referred(async_session, "issued-client", partner)
        await _day(
            async_session, client, date(2026, 3, 5), charged=1000, provider_cost=0
        )

        statement = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        await earnings.issue(async_session, statement)

        with pytest.raises(PartnerError, match="issued"):
            await earnings.generate(
                async_session,
                partner_organization_id=partner.id,
                period_start=PERIOD_START,
                period_end=PERIOD_END,
            )

    async def test_a_period_overlapping_a_settled_one_is_refused(
        self, db_session, async_session
    ):
        """`period_start` is not an identity for a period, and this pays out.

        A statement for 1–31 March that has been issued and paid does not
        collide on `period_start` with a "catch-up" run for 15 March–30 April,
        so the second one re-accrued the fortnight already settled and would
        have paid for those days a second time.
        """
        partner = await _partner(
            async_session,
            "overlap",
            bps=1500,
            basis=CommissionBasis.PLATFORM_FEE.value,
        )
        client = await _referred(async_session, "overlap-client", partner)
        await _day(
            async_session, client, date(2026, 3, 20), charged=400_000, provider_cost=0
        )

        settled = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        await earnings.issue(async_session, settled)
        await earnings.mark_paid(async_session, settled, payment_reference="neft-1")

        with pytest.raises(PartnerError, match="overlaps"):
            await earnings.generate(
                async_session,
                partner_organization_id=partner.id,
                period_start=date(2026, 3, 15),
                period_end=date(2026, 4, 30),
            )

    async def test_a_period_after_a_settled_one_is_allowed(
        self, db_session, async_session
    ):
        """The guard is about overlap, not about having a past. A period that
        starts the day after a paid one is the ordinary next statement."""
        partner = await _partner(
            async_session,
            "sequential",
            bps=1500,
            basis=CommissionBasis.PLATFORM_FEE.value,
        )
        client = await _referred(async_session, "sequential-client", partner)
        await _day(
            async_session, client, date(2026, 3, 20), charged=400_000, provider_cost=0
        )
        await _day(
            async_session, client, date(2026, 4, 10), charged=200_000, provider_cost=0
        )

        first = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        await earnings.issue(async_session, first)
        await earnings.mark_paid(async_session, first, payment_reference="neft-2")

        second = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 4, 30),
        )
        assert second.amount_paise == 30_000  # 15% of 200000, April only

    async def test_paying_records_the_reference(self, db_session, async_session):
        partner = await _partner(
            async_session, "paid", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        client = await _referred(async_session, "paid-client", partner)
        await _day(
            async_session, client, date(2026, 3, 5), charged=1000, provider_cost=0
        )

        statement = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        await earnings.mark_paid(
            async_session, statement, payment_reference="UTR123456"
        )

        assert statement.status == PartnerStatementStatus.PAID.value
        assert statement.payment_reference == "UTR123456"
        assert statement.paid_at is not None

    async def test_an_empty_statement_cannot_be_paid(self, db_session, async_session):
        """Guards a fat finger, not a fraud: marking a zero statement paid
        records a transfer that never happened."""
        partner = await _partner(
            async_session, "empty", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        statement = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        assert statement.amount_paise == 0
        with pytest.raises(PartnerError, match="nothing to pay"):
            await earnings.mark_paid(async_session, statement)

    async def test_outstanding_skips_what_has_been_paid(
        self, db_session, async_session
    ):
        partner = await _partner(
            async_session, "outst", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        client = await _referred(async_session, "outst-client", partner)
        await _day(
            async_session, client, date(2026, 3, 5), charged=1000, provider_cost=0
        )
        statement = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

        assert statement.id in {
            row.id for row in await earnings.outstanding(async_session)
        }
        await earnings.mark_paid(async_session, statement)
        assert statement.id not in {
            row.id for row in await earnings.outstanding(async_session)
        }

    async def test_a_period_cannot_end_before_it_starts(
        self, db_session, async_session
    ):
        partner = await _partner(
            async_session,
            "backwards",
            bps=1000,
            basis=CommissionBasis.PLATFORM_FEE.value,
        )
        with pytest.raises(PartnerError, match="before it starts"):
            await earnings.generate(
                async_session,
                partner_organization_id=partner.id,
                period_start=PERIOD_END,
                period_end=PERIOD_START,
            )

    async def test_a_line_survives_the_account_that_produced_it(
        self, db_session, async_session
    ):
        """A referred account closing must not erase what we owe for the months
        it was open, so the name is copied onto the line."""
        partner = await _partner(
            async_session, "survive", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        client = await _referred(async_session, "survive-client", partner)
        client.billing_name = "Kalyan Clinics"
        await _day(
            async_session, client, date(2026, 3, 5), charged=1000, provider_cost=0
        )

        statement = await earnings.generate(
            async_session,
            partner_organization_id=partner.id,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        lines = await earnings.lines_for(async_session, statement.id)
        assert lines[0].referred_name == "Kalyan Clinics"


@pytest.mark.asyncio
class TestReferralCodes:
    async def test_a_code_is_stable_once_minted(self, db_session, async_session):
        """A code that changes is a code that stops attributing every link
        already in circulation, so there is deliberately no regenerate."""
        org = await _org(async_session, "stable")
        first = await referrals.ensure_code(async_session, org)
        second = await referrals.ensure_code(async_session, org)
        assert first == second

    async def test_the_alphabet_excludes_what_gets_misread(
        self, db_session, async_session
    ):
        """Codes are read down a phone. A transcription error does not fail
        loudly — it attributes nothing, and nobody finds out for a month."""
        org = await _org(async_session, "alphabet")
        code = await referrals.ensure_code(async_session, org)
        assert not set(code) & set("O0I1L")

    @pytest.mark.parametrize("typed", ["ab-cd ef", "  ABCDEF  ", "AbCdEf"])
    async def test_a_code_is_read_the_way_people_type_it(
        self, db_session, async_session, typed
    ):
        assert referrals.normalize(typed) == "ABCDEF"

    async def test_an_unknown_code_attributes_nothing_and_raises_nothing(
        self, db_session, async_session
    ):
        """A signup must never fail on a bad referral code. The person signing
        up did not choose it and cannot fix it."""
        org = await _org(async_session, "unknown-code")
        assert (
            await referrals.attribute(
                async_session, organization=org, code="NOSUCHCODE"
            )
            is None
        )
        assert org.referred_by_organization_id is None

    async def test_a_partner_cannot_refer_themselves(self, db_session, async_session):
        org = await _org(async_session, "selfref")
        code = await referrals.ensure_code(async_session, org)
        assert (
            await referrals.attribute(async_session, organization=org, code=code)
            is None
        )
        assert org.referred_by_organization_id is None

    async def test_attribution_is_write_once(self, db_session, async_session):
        """Attribution that can change later is attribution somebody argues
        about after the money is calculated."""
        first = await _partner(
            async_session, "first-p", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        second = await _partner(
            async_session,
            "second-p",
            bps=1000,
            basis=CommissionBasis.PLATFORM_FEE.value,
        )
        client = await _org(async_session, "contested")

        await referrals.attribute(
            async_session, organization=client, code=first.referral_code
        )
        await referrals.attribute(
            async_session, organization=client, code=second.referral_code
        )

        assert client.referred_by_organization_id == first.id

    async def test_approval_mints_a_code(self, db_session, async_session):
        """A rate with nothing to attribute to it is a percentage of nothing,
        so approval is the moment both halves have to exist."""
        org = await _org(async_session, "approved")
        application = await applications.submit(
            async_session, organization_id=org.id, user_id=None, kind="reseller"
        )
        await applications.approve(
            async_session,
            application=application,
            decided_by=None,
            commission_bps=1500,
            basis=CommissionBasis.PLATFORM_FEE.value,
        )
        await async_session.refresh(org)
        assert org.referral_code


@pytest.mark.asyncio
class TestWhoGetsAStatement:
    async def test_a_partner_whose_arrangement_ended_still_gets_their_last_month(
        self, db_session, async_session
    ):
        """Ever held a commission, not currently holds one. Otherwise the final
        month of every partner who leaves is silently dropped."""
        partner = await _partner(
            async_session, "ended", bps=1000, basis=CommissionBasis.PLATFORM_FEE.value
        )
        await applications.set_commission(
            async_session,
            organization_id=partner.id,
            commission_bps=0,
            basis=CommissionBasis.PLATFORM_FEE.value,
            set_by=None,
            effective_from=datetime(2026, 4, 1, tzinfo=UTC),
        )

        assert partner.id in await applications.organizations_with_commission(
            async_session
        )
