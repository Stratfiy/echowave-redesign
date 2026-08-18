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
    UsdInrRateHistoryModel,
)
from api.enums import CostComponent, RateUnit
from api.services.billing.money import (
    DEFAULT_PLATFORM_RATE_MICROS_USD,
    DEFAULT_PULSE_SECONDS,
    DEFAULT_USD_INR_PAISE,
)
from api.services.billing.rates import (
    resolve_platform_rate,
    resolve_provider_rate,
    resolve_usd_inr,
)

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

        # The uncommitted rate, $0.0365/min, converted at the seeded ₹96 —
        # the receipt carries both, so a customer quoted in dollars can check
        # the rupee figure. This is the price with no tier configured and no
        # commitment; $0.02 is reached through a volume tier or an account
        # override, neither of which exists on this account.
        assert resolved.source == "global_default"
        assert resolved.rate_micros_usd == DEFAULT_PLATFORM_RATE_MICROS_USD
        assert resolved.usd_inr_paise == DEFAULT_USD_INR_PAISE
        assert resolved.rate_mpaise == 350_400
        assert resolved.pulse_seconds == DEFAULT_PULSE_SECONDS

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
        assert resolved.rate_mpaise == 350_400

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

    async def test_a_model_specific_rate_beats_the_provider_fallback(
        self, async_session
    ):
        """The whole point of the model dimension.

        gpt-4o and gpt-4o-mini are more than an order of magnitude apart. Before
        the model column both priced at one rate, so anyone off the default
        model was billed the wrong number.
        """
        async_session.add_all(
            [
                ProviderRateModel(
                    provider="openai",
                    model="",  # provider-wide fallback
                    component=CostComponent.LLM.value,
                    unit=RateUnit.THOUSAND_TOKENS.value,
                    rate_mpaise=12_000,
                    effective_from=JAN,
                ),
                ProviderRateModel(
                    provider="openai",
                    model="gpt-4o-mini",
                    component=CostComponent.LLM.value,
                    unit=RateUnit.THOUSAND_TOKENS.value,
                    rate_mpaise=700,
                    effective_from=JAN,
                ),
            ]
        )
        await async_session.flush()

        mini = await resolve_provider_rate(
            async_session,
            provider="openai",
            component=CostComponent.LLM,
            at=JUN,
            model="gpt-4o-mini",
        )
        assert mini.rate_mpaise == 700
        assert mini.model == "gpt-4o-mini"

    async def test_an_unpriced_model_falls_back_to_the_provider_rate(
        self, async_session
    ):
        async_session.add_all(
            [
                ProviderRateModel(
                    provider="openai",
                    model="",
                    component=CostComponent.LLM.value,
                    unit=RateUnit.THOUSAND_TOKENS.value,
                    rate_mpaise=12_000,
                    effective_from=JAN,
                ),
                ProviderRateModel(
                    provider="openai",
                    model="gpt-4o-mini",
                    component=CostComponent.LLM.value,
                    unit=RateUnit.THOUSAND_TOKENS.value,
                    rate_mpaise=700,
                    effective_from=JAN,
                ),
            ]
        )
        await async_session.flush()

        # A model we have never quoted still prices, at the provider rate,
        # rather than dropping off the receipt as uncosted.
        resolved = await resolve_provider_rate(
            async_session,
            provider="openai",
            component=CostComponent.LLM,
            at=JUN,
            model="some-model-we-never-priced",
        )
        assert resolved.rate_mpaise == 12_000
        assert resolved.model == ""

    async def test_a_model_rate_is_effective_dated_like_any_other(self, async_session):
        async_session.add_all(
            [
                ProviderRateModel(
                    provider="openai",
                    model="gpt-4o",
                    component=CostComponent.LLM.value,
                    unit=RateUnit.THOUSAND_TOKENS.value,
                    rate_mpaise=25_000,
                    effective_from=JAN,
                    effective_to=JUN,
                ),
                ProviderRateModel(
                    provider="openai",
                    model="gpt-4o",
                    component=CostComponent.LLM.value,
                    unit=RateUnit.THOUSAND_TOKENS.value,
                    rate_mpaise=20_000,
                    effective_from=JUN,
                ),
            ]
        )
        await async_session.flush()

        old = await resolve_provider_rate(
            async_session,
            provider="openai",
            component=CostComponent.LLM,
            at=datetime(2026, 3, 1, tzinfo=UTC),
            model="gpt-4o",
        )
        new = await resolve_provider_rate(
            async_session,
            provider="openai",
            component=CostComponent.LLM,
            at=DEC,
            model="gpt-4o",
        )
        assert old.rate_mpaise == 25_000
        assert new.rate_mpaise == 20_000

    async def test_a_fallback_and_a_model_rate_can_both_be_open(self, async_session):
        """Regression: the open-rate unique index must include the model.

        Keyed on (provider, component) alone, inserting a model-specific rate
        alongside the provider fallback would violate the index.
        """
        async_session.add_all(
            [
                ProviderRateModel(
                    provider="elevenlabs",
                    model="",
                    component=CostComponent.TTS.value,
                    unit=RateUnit.THOUSAND_CHARS.value,
                    rate_mpaise=40_000,
                    effective_from=JAN,
                ),
                ProviderRateModel(
                    provider="elevenlabs",
                    model="turbo-v2",
                    component=CostComponent.TTS.value,
                    unit=RateUnit.THOUSAND_CHARS.value,
                    rate_mpaise=18_000,
                    effective_from=JAN,
                ),
            ]
        )
        await async_session.flush()  # must not raise


@pytest.mark.asyncio
class TestExchangeRateResolution:
    """The rupee figure on a receipt is a function of the day it was billed.

    Pricing in dollars only holds up if the conversion is effective-dated like
    everything else — otherwise a rupee that moved after the fact would rewrite
    invoices that were already sent.
    """

    async def _fx(self, async_session, paise_per_usd: int, effective_from, **kw):
        row = UsdInrRateHistoryModel(
            paise_per_usd=paise_per_usd, effective_from=effective_from, **kw
        )
        async_session.add(row)
        await async_session.flush()
        return row

    async def test_the_migration_seeded_an_opening_rate(self, async_session):
        """So that no call is ever billed off the hard-coded fallback."""
        fx = await resolve_usd_inr(async_session, at=JUN)
        assert fx.paise_per_usd == DEFAULT_USD_INR_PAISE
        assert fx.is_fallback is False

    async def test_a_later_rate_does_not_rewrite_an_earlier_call(self, async_session):
        """The property the whole effective-dating scheme exists for."""
        # Close the seeded open row and open a weaker rupee from December.
        from sqlalchemy import update

        await async_session.execute(
            update(UsdInrRateHistoryModel)
            .where(UsdInrRateHistoryModel.effective_to.is_(None))
            .values(effective_to=DEC)
        )
        await self._fx(async_session, 11_000, DEC, source="test")

        june = await resolve_platform_rate(
            async_session,
            organization_id=(await _make_org(async_session, "org-fx-june")).id,
            at=JUN,
        )
        december = await resolve_platform_rate(
            async_session,
            organization_id=(await _make_org(async_session, "org-fx-dec")).id,
            at=DEC,
        )

        # Same dollar rate both times; different rupee amounts. The point of
        # the test is that FX moves and the dollar price does not.
        assert (
            june.rate_micros_usd
            == december.rate_micros_usd
            == DEFAULT_PLATFORM_RATE_MICROS_USD
        )
        assert june.rate_mpaise == 350_400  # at ₹96
        assert december.rate_mpaise == 401_500  # at ₹110

    async def test_a_gap_in_the_history_is_flagged_not_hidden(self, async_session):
        """Billing at a stale rate quietly erodes margin, and the only symptom
        is a number that looks fine. So the fallback announces itself."""
        await async_session.execute(UsdInrRateHistoryModel.__table__.delete())

        fx = await resolve_usd_inr(async_session, at=JUN)

        assert fx.is_fallback is True
        assert fx.paise_per_usd == DEFAULT_USD_INR_PAISE
        assert fx.source is None


@pytest.mark.asyncio
class TestRupeeNativeOverrides:
    """A contract written in rupees must not float with the dollar.

    Both currencies are supported on purpose: the list price is in USD, but a
    deal agreed at "₹1.20 a minute" means ₹1.20 a minute whatever the rupee
    does, and converting it would change what was signed.
    """

    async def test_a_rupee_override_ignores_the_exchange_rate(self, async_session):
        org = await _make_org(async_session, "org-inr-native")
        async_session.add(
            OrganizationRateHistoryModel(
                organization_id=org.id,
                platform_rate_mpaise=120_000,  # ₹1.20/min, agreed in rupees
                effective_from=JAN,
            )
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )

        assert resolved.rate_mpaise == 120_000
        assert resolved.source == "account_override"
        # No dollar price is reported, because there is not one to report.
        assert resolved.rate_micros_usd is None
        assert resolved.usd_inr_paise is None

    async def test_a_dollar_override_is_converted(self, async_session):
        org = await _make_org(async_session, "org-usd-override")
        async_session.add(
            OrganizationRateHistoryModel(
                organization_id=org.id,
                platform_rate_micros_usd=15_000,  # $0.015/min volume deal
                effective_from=JAN,
            )
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )

        assert resolved.rate_micros_usd == 15_000
        assert resolved.rate_mpaise == 144_000  # $0.015 * ₹96 = ₹1.44

    async def test_a_row_cannot_carry_both_currencies(self, async_session):
        """Two populated columns would leave two answers to what the account
        pays, so the database refuses it rather than the code guessing."""
        from sqlalchemy.exc import IntegrityError

        org = await _make_org(async_session, "org-both")
        # In a savepoint: the constraint violation aborts the transaction, and
        # the surrounding test transaction has to survive it.
        with pytest.raises(IntegrityError):
            async with async_session.begin_nested():
                async_session.add(
                    OrganizationRateHistoryModel(
                        organization_id=org.id,
                        platform_rate_micros_usd=20_000,
                        platform_rate_mpaise=200_000,
                        effective_from=JAN,
                    )
                )

    async def test_a_row_must_carry_one(self, async_session):
        from sqlalchemy.exc import IntegrityError

        org = await _make_org(async_session, "org-neither")
        with pytest.raises(IntegrityError):
            async with async_session.begin_nested():
                async_session.add(
                    OrganizationRateHistoryModel(
                        organization_id=org.id, effective_from=JAN
                    )
                )


@pytest.mark.asyncio
class TestNegotiatedPulse:
    async def test_an_account_can_be_pinned_to_whole_minute_billing(
        self, async_session
    ):
        """Some enterprise contracts specify it, and the pulse is a term of the
        deal rather than a platform-wide constant."""
        org = await _make_org(async_session, "org-minute-pulse")
        async_session.add(
            OrganizationRateHistoryModel(
                organization_id=org.id,
                platform_rate_micros_usd=20_000,
                pulse_seconds=60,
                effective_from=JAN,
            )
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )
        assert resolved.pulse_seconds == 60

    async def test_an_unset_pulse_falls_back_to_the_platform_default(
        self, async_session
    ):
        org = await _make_org(async_session, "org-default-pulse")
        async_session.add(
            OrganizationRateHistoryModel(
                organization_id=org.id,
                platform_rate_micros_usd=20_000,
                effective_from=JAN,
            )
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )
        assert resolved.pulse_seconds == DEFAULT_PULSE_SECONDS
