"""Setting prices from the admin panel.

The property every test here defends is that changing a price **never rewrites
an invoice already sent**. A price change closes the outgoing row and opens a
new one, so asking "what did this account pay in June" keeps answering the June
number however many times the price has moved since.

The second concern is that the global default is a real row an operator set,
not a constant in source — and that when it is missing, the screen says so
rather than quietly falling back.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import OrganizationModel, UserModel
from api.enums import CostComponent, RateUnit
from api.services.billing.money import (
    DEFAULT_PLATFORM_RATE_MICROS_USD,
    DEFAULT_PULSE_SECONDS,
)
from api.services.billing.rate_card import (
    GLOBAL_TIER_MIN_MINUTES,
    RateCardError,
    get_rate_card,
    retire_provider_rate,
    retire_volume_tier,
    set_account_rate,
    set_exchange_rate,
    set_provider_rate,
    set_volume_tier,
)
from api.services.billing.rates import resolve_platform_rate, resolve_provider_rate

JAN = datetime(2026, 1, 1, tzinfo=UTC)
JUN = datetime(2026, 6, 1, tzinfo=UTC)
DEC = datetime(2026, 12, 1, tzinfo=UTC)


async def _staff(session) -> UserModel:
    user = UserModel(provider_id=f"staff-{datetime.now(UTC).timestamp()}")
    session.add(user)
    await session.flush()
    return user


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


@pytest.mark.asyncio
class TestTheGlobalPrice:
    async def test_setting_it_changes_what_an_account_is_charged(self, async_session):
        """The whole point: a price typed into the admin panel is the price the
        cost engine resolves, with no deploy in between."""
        staff = await _staff(async_session)
        org = await _org(async_session, "global-price")

        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            name="Global default",
            platform_rate_micros_usd=30_000,  # $0.03/min
            effective_from=JAN,
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )
        assert resolved.source == "volume_tier"
        assert resolved.rate_micros_usd == 30_000
        assert resolved.rate_mpaise == 288_000  # $0.03 at ₹96

    async def test_the_pulse_is_settable_alongside_the_price(self, async_session):
        staff = await _staff(async_session)
        org = await _org(async_session, "global-pulse")

        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            name="Global default",
            platform_rate_micros_usd=20_000,
            pulse_seconds=30,
            effective_from=JAN,
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )
        assert resolved.pulse_seconds == 30

    async def test_raising_the_price_does_not_reprice_last_month(self, async_session):
        """The property the whole effective-dating scheme exists for, exercised
        through the admin path rather than through hand-written rows."""
        staff = await _staff(async_session)
        org = await _org(async_session, "global-history")

        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            name="Global default",
            platform_rate_micros_usd=20_000,
            effective_from=JAN,
        )
        await async_session.flush()
        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            name="Global default",
            platform_rate_micros_usd=40_000,
            effective_from=DEC,
        )
        await async_session.flush()

        june = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )
        december = await resolve_platform_rate(
            async_session, organization_id=org.id, at=DEC
        )
        assert june.rate_micros_usd == 20_000
        assert december.rate_micros_usd == 40_000

    async def test_a_price_cannot_start_before_the_one_it_replaces(self, async_session):
        """Two rows opening at the same instant would leave the resolver
        choosing between them arbitrarily."""
        staff = await _staff(async_session)
        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            name="Global default",
            platform_rate_micros_usd=20_000,
            effective_from=JUN,
        )
        await async_session.flush()

        with pytest.raises(RateCardError, match="must start after"):
            await set_volume_tier(
                async_session,
                actor_user_id=staff.id,
                min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
                name="Global default",
                platform_rate_micros_usd=40_000,
                effective_from=JAN,
            )

    async def test_the_global_tier_cannot_be_removed(self, async_session):
        """Retiring it would drop every account back onto a constant in source,
        which is the situation this module exists to end."""
        staff = await _staff(async_session)
        with pytest.raises(RateCardError, match="cannot be removed"):
            await retire_volume_tier(
                async_session,
                actor_user_id=staff.id,
                min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            )


@pytest.mark.asyncio
class TestPriceValidation:
    @pytest.mark.parametrize(
        "usd, inr",
        [(20_000, 200_000), (None, None)],
        ids=["both currencies", "neither currency"],
    )
    async def test_exactly_one_currency_is_required(self, async_session, usd, inr):
        staff = await _staff(async_session)
        with pytest.raises(RateCardError, match="dollars or in rupees"):
            await set_volume_tier(
                async_session,
                actor_user_id=staff.id,
                min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
                name="Global default",
                platform_rate_micros_usd=usd,
                platform_rate_mpaise=inr,
            )

    @pytest.mark.parametrize("pulse", [0, 61, -15])
    async def test_an_implausible_pulse_is_refused(self, async_session, pulse):
        """Above 60 it stops being a pulse and becomes minute billing with
        extra steps; at or below zero it is not billing at all."""
        staff = await _staff(async_session)
        with pytest.raises(RateCardError, match="pulse"):
            await set_volume_tier(
                async_session,
                actor_user_id=staff.id,
                min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
                name="Global default",
                platform_rate_micros_usd=20_000,
                pulse_seconds=pulse,
            )

    async def test_a_negative_price_is_refused(self, async_session):
        staff = await _staff(async_session)
        with pytest.raises(RateCardError, match="negative"):
            await set_volume_tier(
                async_session,
                actor_user_id=staff.id,
                min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
                name="Global default",
                platform_rate_micros_usd=-1,
            )

    async def test_an_unknown_unit_is_refused_at_write_time(self, async_session):
        """Storing it would succeed and then fail during costing — on a live
        call, hours later, far from the person who typed it."""
        staff = await _staff(async_session)
        with pytest.raises(RateCardError):
            await set_provider_rate(
                async_session,
                actor_user_id=staff.id,
                provider="openai",
                component="llm",
                unit="per_vibe",
                rate_mpaise=1000,
            )


@pytest.mark.asyncio
class TestProviderRates:
    async def test_setting_one_changes_what_a_call_is_costed_at(self, async_session):
        staff = await _staff(async_session)
        await set_provider_rate(
            async_session,
            actor_user_id=staff.id,
            provider="deepgram",
            component=CostComponent.STT,
            unit=RateUnit.MINUTE,
            rate_mpaise=25_000,
            effective_from=JAN,
        )
        await async_session.flush()

        resolved = await resolve_provider_rate(
            async_session, provider="deepgram", component=CostComponent.STT, at=JUN
        )
        assert resolved is not None
        assert resolved.rate_mpaise == 25_000

    async def test_a_model_rate_and_the_provider_fallback_are_independent(
        self, async_session
    ):
        """Changing the price of one model must not disturb the other, or the
        catch-all every unlisted model resolves to."""
        staff = await _staff(async_session)
        for model, rate in (("", 12_000), ("gpt-4o-mini", 700)):
            await set_provider_rate(
                async_session,
                actor_user_id=staff.id,
                provider="openai",
                component=CostComponent.LLM,
                unit=RateUnit.THOUSAND_TOKENS,
                rate_mpaise=rate,
                model=model,
                effective_from=JAN,
            )
        await async_session.flush()

        # Move only the specific model.
        await set_provider_rate(
            async_session,
            actor_user_id=staff.id,
            provider="openai",
            component=CostComponent.LLM,
            unit=RateUnit.THOUSAND_TOKENS,
            rate_mpaise=500,
            model="gpt-4o-mini",
            effective_from=JUN,
        )
        await async_session.flush()

        mini = await resolve_provider_rate(
            async_session,
            provider="openai",
            component=CostComponent.LLM,
            at=DEC,
            model="gpt-4o-mini",
        )
        fallback = await resolve_provider_rate(
            async_session,
            provider="openai",
            component=CostComponent.LLM,
            at=DEC,
            model="something-else",
        )
        assert mini.rate_mpaise == 500
        assert fallback.rate_mpaise == 12_000

    async def test_retiring_a_rate_leaves_usage_uncosted_not_free(self, async_session):
        """Pricing at zero would understate provider cost and overstate margin.
        No rate means the cost engine reports it, which is the honest failure."""
        staff = await _staff(async_session)
        await set_provider_rate(
            async_session,
            actor_user_id=staff.id,
            provider="cartesia",
            component=CostComponent.TTS,
            unit=RateUnit.THOUSAND_CHARS,
            rate_mpaise=40_000,
            effective_from=JAN,
        )
        await async_session.flush()

        await retire_provider_rate(
            async_session,
            actor_user_id=staff.id,
            provider="cartesia",
            component=CostComponent.TTS,
            effective_from=JUN,
        )
        await async_session.flush()

        assert (
            await resolve_provider_rate(
                async_session,
                provider="cartesia",
                component=CostComponent.TTS,
                at=DEC,
            )
        ) is None
        # And the retirement did not rewrite what earlier calls were costed at.
        earlier = await resolve_provider_rate(
            async_session, provider="cartesia", component=CostComponent.TTS, at=JAN
        )
        assert earlier.rate_mpaise == 40_000

    async def test_provider_and_model_are_normalised(self, async_session):
        """So "OpenAI" typed into a form resolves the same row the pipeline
        writes as "openai"."""
        staff = await _staff(async_session)
        await set_provider_rate(
            async_session,
            actor_user_id=staff.id,
            provider="  OpenAI ",
            component=CostComponent.LLM,
            unit=RateUnit.THOUSAND_TOKENS,
            rate_mpaise=9_000,
            model=" GPT-4O ",
            effective_from=JAN,
        )
        await async_session.flush()

        resolved = await resolve_provider_rate(
            async_session,
            provider="openai",
            component=CostComponent.LLM,
            at=JUN,
            model="gpt-4o",
        )
        assert resolved is not None
        assert resolved.rate_mpaise == 9_000


@pytest.mark.asyncio
class TestAccountOverrides:
    async def test_an_override_beats_the_global_price(self, async_session):
        staff = await _staff(async_session)
        org = await _org(async_session, "override-wins")
        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            name="Global default",
            platform_rate_micros_usd=20_000,
            effective_from=JAN,
        )
        await set_account_rate(
            async_session,
            actor_user_id=staff.id,
            organization_id=org.id,
            platform_rate_micros_usd=12_000,
            pulse_seconds=1,
            effective_from=JAN,
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )
        assert resolved.source == "account_override"
        assert resolved.rate_micros_usd == 12_000
        assert resolved.pulse_seconds == 1

    async def test_an_override_can_be_written_in_rupees(self, async_session):
        """For a contract that must not move with the dollar."""
        staff = await _staff(async_session)
        org = await _org(async_session, "override-inr")
        await set_account_rate(
            async_session,
            actor_user_id=staff.id,
            organization_id=org.id,
            platform_rate_mpaise=120_000,
            effective_from=JAN,
        )
        await async_session.flush()

        resolved = await resolve_platform_rate(
            async_session, organization_id=org.id, at=JUN
        )
        assert resolved.rate_mpaise == 120_000
        assert resolved.rate_micros_usd is None

    async def test_an_unknown_account_is_refused(self, async_session):
        staff = await _staff(async_session)
        with pytest.raises(RateCardError, match="not found"):
            await set_account_rate(
                async_session,
                actor_user_id=staff.id,
                organization_id=999_999,
                platform_rate_micros_usd=20_000,
            )


@pytest.mark.asyncio
class TestReadingTheCard:
    async def test_it_says_when_the_price_is_still_a_fallback(self, async_session):
        """An operator who has never set a price should be told that, not shown
        a number that looks configured."""
        from sqlalchemy import delete

        from api.db.models import PlatformVolumeTierModel

        await async_session.execute(delete(PlatformVolumeTierModel))

        card = await get_rate_card(async_session)

        assert card.using_fallback_platform_rate is True
        assert card.global_tier is None
        assert card.fallback["platform_rate_micros_usd"] == (
            DEFAULT_PLATFORM_RATE_MICROS_USD
        )
        assert card.fallback["pulse_seconds"] == DEFAULT_PULSE_SECONDS

    async def test_it_returns_only_what_is_in_force(self, async_session):
        """Superseded rows stay in the table for re-costing, but a rate card
        showing two prices for the same thing would be unreadable."""
        staff = await _staff(async_session)
        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            name="Global default",
            platform_rate_micros_usd=20_000,
            effective_from=JAN,
        )
        await async_session.flush()
        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            name="Global default",
            platform_rate_micros_usd=25_000,
            effective_from=JUN,
        )
        await async_session.flush()

        card = await get_rate_card(async_session)
        assert card.global_tier["platform_rate_micros_usd"] == 25_000
        assert card.using_fallback_platform_rate is False

    async def test_volume_tiers_are_listed_apart_from_the_global_price(
        self, async_session
    ):
        staff = await _staff(async_session)
        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            name="Global default",
            platform_rate_micros_usd=20_000,
            effective_from=JAN,
        )
        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=50_000,
            name="Scale",
            platform_rate_micros_usd=15_000,
            effective_from=JAN,
        )
        await async_session.flush()

        card = await get_rate_card(async_session)
        assert card.global_tier["min_period_minutes"] == 0
        assert [t["min_period_minutes"] for t in card.volume_tiers] == [50_000]

    async def test_the_exchange_rate_round_trips(self, async_session):
        staff = await _staff(async_session)
        await set_exchange_rate(
            async_session,
            actor_user_id=staff.id,
            paise_per_usd=10_250,
            source="rbi",
        )
        await async_session.flush()

        card = await get_rate_card(async_session)
        assert card.exchange_rate["paise_per_usd"] == 10_250
        assert card.exchange_rate["source"] == "rbi"

    async def test_a_new_exchange_rate_does_not_reprice_old_calls(self, async_session):
        """It takes effect now, never retroactively — repricing an invoice
        already sent is not something a form should be able to do."""
        staff = await _staff(async_session)
        org = await _org(async_session, "fx-not-retro")
        before = datetime.now(UTC) - timedelta(days=1)

        await set_exchange_rate(
            async_session, actor_user_id=staff.id, paise_per_usd=11_000
        )
        await async_session.flush()

        # A call from yesterday still resolves at the rate that covered it.
        yesterday = await resolve_platform_rate(
            async_session, organization_id=org.id, at=before
        )
        today = await resolve_platform_rate(
            async_session, organization_id=org.id, at=datetime.now(UTC)
        )
        assert yesterday.usd_inr_paise == 9_600
        assert today.usd_inr_paise == 11_000

    @pytest.mark.parametrize("bad", [0, -100])
    async def test_a_non_positive_exchange_rate_is_refused(self, async_session, bad):
        staff = await _staff(async_session)
        with pytest.raises(RateCardError, match="greater than zero"):
            await set_exchange_rate(
                async_session, actor_user_id=staff.id, paise_per_usd=bad
            )


@pytest.mark.asyncio
class TestTheDashboardReportsTheRealPrice:
    """The account screens must show what an account actually pays.

    They used to read ``organizations.platform_rate_mpaise``, a convenience
    mirror that only ever held a rupee figure. Once a price could be quoted in
    dollars that column went null, and the screens fell back to a constant —
    showing ₹2.00 for an account on $0.02, and reporting a real negotiated
    override as though the account were on the list price.
    """

    async def test_an_account_on_the_list_price_shows_it(self, async_session):
        from api.db.billing_dashboard_client import account_detail

        staff = await _staff(async_session)
        org = await _org(async_session, "dash-default")
        await set_volume_tier(
            async_session,
            actor_user_id=staff.id,
            min_period_minutes=GLOBAL_TIER_MIN_MINUTES,
            name="Global default",
            platform_rate_micros_usd=20_000,
            effective_from=JAN,
        )
        await async_session.flush()

        detail = await account_detail(async_session, organization_id=org.id)

        # $0.02 at ₹96, not the ₹2.00 legacy constant.
        assert detail["platform_rate_mpaise"] == 192_000
        assert detail["platform_rate_micros_usd"] == 20_000
        assert detail["platform_rate_is_override"] is False

    async def test_a_dollar_override_is_reported_as_an_override(self, async_session):
        """The bug that mattered most: a negotiated dollar price read as "no
        override", so nobody could tell which accounts were on a deal."""
        from api.db.billing_dashboard_client import account_detail

        staff = await _staff(async_session)
        org = await _org(async_session, "dash-usd-override")
        await set_account_rate(
            async_session,
            actor_user_id=staff.id,
            organization_id=org.id,
            platform_rate_micros_usd=12_000,
            pulse_seconds=30,
            effective_from=JAN,
        )
        await async_session.flush()

        detail = await account_detail(async_session, organization_id=org.id)

        assert detail["platform_rate_micros_usd"] == 12_000
        assert detail["platform_rate_mpaise"] == 115_200  # $0.012 at ₹96
        assert detail["pulse_seconds"] == 30
        assert detail["platform_rate_is_override"] is True

    async def test_the_accounts_list_agrees_with_the_detail_screen(self, async_session):
        from api.db.billing_dashboard_client import accounts_summary

        staff = await _staff(async_session)
        org = await _org(async_session, "dash-list")
        await set_account_rate(
            async_session,
            actor_user_id=staff.id,
            organization_id=org.id,
            platform_rate_mpaise=120_000,
            effective_from=JAN,
        )
        await async_session.flush()

        rows = await accounts_summary(async_session, start=JUN.date(), end=JUN.date())
        mine = next(r for r in rows if r["organization_id"] == org.id)

        assert mine["platform_rate_mpaise"] == 120_000
        # A rupee contract has no dollar price to report.
        assert mine["platform_rate_micros_usd"] is None
        assert mine["platform_rate_source"] == "account_override"

    async def test_rate_history_shows_a_dollar_row_rather_than_a_blank(
        self, async_session
    ):
        from api.db.billing_dashboard_client import account_rate_history

        staff = await _staff(async_session)
        org = await _org(async_session, "dash-history")
        await set_account_rate(
            async_session,
            actor_user_id=staff.id,
            organization_id=org.id,
            platform_rate_micros_usd=18_000,
            effective_from=JAN,
        )
        await async_session.flush()

        history = await account_rate_history(async_session, organization_id=org.id)

        assert history[0]["platform_rate_micros_usd"] == 18_000
        assert history[0]["platform_rate_mpaise"] is None
