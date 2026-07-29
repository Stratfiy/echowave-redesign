"""Effective-dated rate resolution against a real database.

The resolution order (account override → volume tier → global default) and,
more importantly, the guarantee that a rate change never rewrites history:
asking for the rate "as of" an old timestamp must return what was in force
then, not what is in force now.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import (
    OrganizationModel,
    OrganizationRateHistoryModel,
    PlatformVolumeTierModel,
    ProviderRateModel,
)
from api.enums import CostComponent, RateUnit
from api.services.billing.money import DEFAULT_PLATFORM_RATE_MPAISE
from api.services.billing.rates import resolve_platform_rate, resolve_provider_rate

JAN = datetime(2026, 1, 1, tzinfo=UTC)
JUN = datetime(2026, 6, 1, tzinfo=UTC)
DEC = datetime(2026, 12, 1, tzinfo=UTC)


async def _make_org(async_session, provider_id: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=provider_id, quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


@pytest.mark.asyncio
class TestPlatformRateResolution:
    async def test_falls_back_to_global_default(self, async_session):
        org = await _make_org(async_session, "org-default")

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )

        assert resolved.rate_mpaise == DEFAULT_PLATFORM_RATE_MPAISE  # ₹2.00/min
        assert resolved.source == "global_default"

    async def test_account_override_wins_over_default(self, async_session):
        org = await _make_org(async_session, "org-override")
        async_session.add(
            OrganizationRateHistoryModel(
                organization_id=org.id,
                platform_rate_mpaise=120_000,  # ₹1.20/min enterprise deal
                effective_from=JAN,
                note="enterprise deal",
            )
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )

        assert resolved.rate_mpaise == 120_000
        assert resolved.source == "account_override"

    async def test_account_override_beats_a_matching_volume_tier(self, async_session):
        """Resolution order is override first, then tier — never the reverse."""
        org = await _make_org(async_session, "org-override-and-tier")
        async_session.add_all(
            [
                OrganizationRateHistoryModel(
                    organization_id=org.id,
                    platform_rate_mpaise=120_000,
                    effective_from=JAN,
                ),
                PlatformVolumeTierModel(
                    name="10k minutes",
                    min_period_minutes=10_000,
                    platform_rate_mpaise=150_000,
                    effective_from=JAN,
                ),
            ]
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session,
            organization_id=org.id,
            at=JUN,
            period_minutes=50_000,  # comfortably into the tier
        )

        assert resolved.source == "account_override"
        assert resolved.rate_mpaise == 120_000

    async def test_volume_tier_applies_when_threshold_is_crossed(self, async_session):
        org = await _make_org(async_session, "org-tier")
        async_session.add_all(
            [
                PlatformVolumeTierModel(
                    name="10k minutes",
                    min_period_minutes=10_000,
                    platform_rate_mpaise=160_000,
                    effective_from=JAN,
                ),
                PlatformVolumeTierModel(
                    name="50k minutes",
                    min_period_minutes=50_000,
                    platform_rate_mpaise=140_000,
                    effective_from=JAN,
                ),
            ]
        )
        await async_session.flush()

        below = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN, period_minutes=9_999
        )
        assert below.source == "global_default"

        first_tier = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN, period_minutes=10_000
        )
        assert first_tier.source == "volume_tier"
        assert first_tier.rate_mpaise == 160_000

        # The highest matching threshold wins, not the first one found.
        second_tier = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN, period_minutes=60_000
        )
        assert second_tier.rate_mpaise == 140_000
        assert second_tier.tier_name == "50k minutes"


@pytest.mark.asyncio
class TestEffectiveDating:
    async def test_a_rate_change_does_not_rewrite_history(self, async_session):
        """The core guarantee: an old call re-costs to its original number.

        The account was on ₹2.50/min for the first half of the year and moved
        to ₹1.50/min in June. Recomputing a March invoice must still say ₹2.50.
        """
        org = await _make_org(async_session, "org-history")
        async_session.add_all(
            [
                OrganizationRateHistoryModel(
                    organization_id=org.id,
                    platform_rate_mpaise=250_000,
                    effective_from=JAN,
                    effective_to=JUN,  # closed, not deleted
                ),
                OrganizationRateHistoryModel(
                    organization_id=org.id,
                    platform_rate_mpaise=150_000,
                    effective_from=JUN,
                ),
            ]
        )
        await async_session.flush()

        march = await resolve_platform_rate(
            async_session,
            organization_id=org.id,
            at=datetime(2026, 3, 15, tzinfo=UTC),
        )
        assert march.rate_mpaise == 250_000

        december = await resolve_platform_rate(
            async_session, organization_id=org.id, at=DEC
        )
        assert december.rate_mpaise == 150_000

    async def test_boundary_is_inclusive_of_from_and_exclusive_of_to(
        self, async_session
    ):
        """No gap and no overlap at the instant a rate changes."""
        org = await _make_org(async_session, "org-boundary")
        async_session.add_all(
            [
                OrganizationRateHistoryModel(
                    organization_id=org.id,
                    platform_rate_mpaise=250_000,
                    effective_from=JAN,
                    effective_to=JUN,
                ),
                OrganizationRateHistoryModel(
                    organization_id=org.id,
                    platform_rate_mpaise=150_000,
                    effective_from=JUN,
                ),
            ]
        )
        await async_session.flush()

        just_before = await resolve_platform_rate(
            async_session,
            organization_id=org.id,
            at=JUN - timedelta(microseconds=1),
        )
        exactly_at = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )

        assert just_before.rate_mpaise == 250_000
        assert exactly_at.rate_mpaise == 150_000

    async def test_a_future_dated_rate_does_not_apply_yet(self, async_session):
        """Scheduling a rise for next month must not change today's price."""
        org = await _make_org(async_session, "org-future")
        async_session.add(
            OrganizationRateHistoryModel(
                organization_id=org.id,
                platform_rate_mpaise=300_000,
                effective_from=DEC,
            )
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )

        assert resolved.source == "global_default"
        assert resolved.rate_mpaise == DEFAULT_PLATFORM_RATE_MPAISE

    async def test_rates_are_scoped_to_their_own_account(self, async_session):
        """One account's enterprise deal must never leak onto another."""
        mine = await _make_org(async_session, "org-mine")
        theirs = await _make_org(async_session, "org-theirs")
        async_session.add(
            OrganizationRateHistoryModel(
                organization_id=theirs.id,
                platform_rate_mpaise=100_000,
                effective_from=JAN,
            )
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=mine.id, at=JUN
        )

        assert resolved.source == "global_default"


@pytest.mark.asyncio
class TestProviderRateResolution:
    async def test_resolves_the_rate_in_force_at_the_time(self, async_session):
        async_session.add_all(
            [
                ProviderRateModel(
                    provider="deepgram",
                    component=CostComponent.STT.value,
                    unit=RateUnit.MINUTE.value,
                    rate_mpaise=30_000,
                    effective_from=JAN,
                    effective_to=JUN,
                ),
                ProviderRateModel(
                    provider="deepgram",
                    component=CostComponent.STT.value,
                    unit=RateUnit.MINUTE.value,
                    rate_mpaise=25_000,
                    effective_from=JUN,
                ),
            ]
        )
        await async_session.flush()

        old = await resolve_provider_rate(
            async_session,
            provider="deepgram",
            component=CostComponent.STT,
            at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        new = await resolve_provider_rate(
            async_session, provider="deepgram", component=CostComponent.STT, at=DEC
        )

        assert old.rate_mpaise == 30_000
        assert new.rate_mpaise == 25_000
        assert new.unit is RateUnit.MINUTE

    async def test_missing_rate_returns_none_rather_than_zero(self, async_session):
        """No rate on file is not the same as a rate of zero.

        Returning 0 here would silently understate provider cost and overstate
        margin; the cost engine surfaces the gap instead.
        """
        resolved = await resolve_provider_rate(
            async_session,
            provider="never-configured",
            component=CostComponent.TTS,
            at=JUN,
        )
        assert resolved is None
