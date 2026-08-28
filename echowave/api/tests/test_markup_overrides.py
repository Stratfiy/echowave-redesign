"""A markup override for one (component, provider, model), on top of the
global multiple.

Same properties the global markup and every other rate table here has to
hold, narrowed to one line instead of every account: effective-dated so an
old call re-costs to the multiple that actually applied, most-specific-wins
resolution matching ``provider_rates``, and a cost engine that uses it exactly
where it uses a provider rate — per line, not on the total.
"""

from datetime import UTC, datetime

import pytest

from api.enums import CostComponent, RateUnit
from api.services.billing import markup
from api.services.billing.cost_engine import RateSpec, UsageItem, compute_call_cost
from api.services.billing.money import cost_paise


async def _user(async_session, slug: str):
    from api.db.models import UserModel

    user = UserModel(provider_id=f"user-{slug}")
    async_session.add(user)
    await async_session.flush()
    return user


@pytest.mark.asyncio
class TestResolving:
    async def test_no_override_resolves_to_none(self, async_session):
        """None, not the global markup — the caller decides the fallback, the
        same contract resolve_provider_rate has for a missing rate row."""
        assert (
            await markup.resolve_markup_override_bps(
                async_session, provider="openai", component="llm", at=datetime.now(UTC)
            )
            is None
        )

    async def test_a_model_specific_override_resolves(self, async_session):
        user = await _user(async_session, "specific")
        await markup.set_markup_override(
            async_session,
            provider="openai",
            component="llm",
            model="gpt-4o",
            markup_bps=20_000,
            actor_user_id=user.id,
        )

        assert (
            await markup.resolve_markup_override_bps(
                async_session,
                provider="openai",
                component="llm",
                model="gpt-4o",
                at=datetime.now(UTC),
            )
            == 20_000
        )

    async def test_a_provider_wide_override_applies_to_any_model(self, async_session):
        """model='' is the fallback, exactly like a provider-wide rate row."""
        user = await _user(async_session, "wide")
        await markup.set_markup_override(
            async_session,
            provider="openai",
            component="llm",
            model="",
            markup_bps=18_000,
            actor_user_id=user.id,
        )

        assert (
            await markup.resolve_markup_override_bps(
                async_session,
                provider="openai",
                component="llm",
                model="gpt-4o-mini",
                at=datetime.now(UTC),
            )
            == 18_000
        )

    async def test_a_model_specific_override_wins_over_the_provider_wide_one(
        self, async_session
    ):
        user = await _user(async_session, "precedence")
        await markup.set_markup_override(
            async_session,
            provider="openai",
            component="llm",
            model="",
            markup_bps=18_000,
            actor_user_id=user.id,
        )
        await markup.set_markup_override(
            async_session,
            provider="openai",
            component="llm",
            model="gpt-4o",
            markup_bps=22_000,
            actor_user_id=user.id,
        )

        assert (
            await markup.resolve_markup_override_bps(
                async_session,
                provider="openai",
                component="llm",
                model="gpt-4o",
                at=datetime.now(UTC),
            )
            == 22_000
        )
        # A different model from the same provider still gets the wide row.
        assert (
            await markup.resolve_markup_override_bps(
                async_session,
                provider="openai",
                component="llm",
                model="gpt-4o-mini",
                at=datetime.now(UTC),
            )
            == 18_000
        )

    async def test_an_old_call_prices_against_the_override_of_its_day(
        self, async_session
    ):
        user = await _user(async_session, "history")
        march = datetime(2026, 3, 1, tzinfo=UTC)
        june = datetime(2026, 6, 1, tzinfo=UTC)
        await markup.set_markup_override(
            async_session,
            provider="openai",
            component="llm",
            model="gpt-4o",
            markup_bps=15_000,
            actor_user_id=user.id,
            now=march,
        )
        await markup.set_markup_override(
            async_session,
            provider="openai",
            component="llm",
            model="gpt-4o",
            markup_bps=20_000,
            actor_user_id=user.id,
            now=june,
        )

        at_march = datetime(2026, 3, 15, tzinfo=UTC)
        assert (
            await markup.resolve_markup_override_bps(
                async_session,
                provider="openai",
                component="llm",
                model="gpt-4o",
                at=at_march,
            )
            == 15_000
        )
        assert (
            await markup.resolve_markup_override_bps(
                async_session,
                provider="openai",
                component="llm",
                model="gpt-4o",
                at=datetime.now(UTC),
            )
            == 20_000
        )


class TestBounds:
    def test_below_cost_is_refused(self):
        with pytest.raises(markup.MarkupError):
            markup.validate_markup_bps(9_999)

    def test_an_absurd_multiple_is_refused(self):
        with pytest.raises(markup.MarkupError):
            markup.validate_markup_bps(140_000)


@pytest.mark.asyncio
class TestWriting:
    async def test_a_second_set_closes_the_first(self, async_session):
        """At most one open override per key — replacing rather than
        stacking, so resolution never has two answers for the same line."""
        import sqlalchemy as sa

        from api.db.models import ManagedMarkupOverrideModel

        user = await _user(async_session, "replace")
        await markup.set_markup_override(
            async_session,
            provider="sarvam",
            component="tts",
            markup_bps=15_000,
            actor_user_id=user.id,
        )
        await markup.set_markup_override(
            async_session,
            provider="sarvam",
            component="tts",
            markup_bps=19_000,
            actor_user_id=user.id,
        )

        open_rows = (
            (
                await async_session.execute(
                    sa.select(ManagedMarkupOverrideModel).where(
                        ManagedMarkupOverrideModel.provider == "sarvam",
                        ManagedMarkupOverrideModel.component == "tts",
                        ManagedMarkupOverrideModel.effective_to.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(open_rows) == 1
        assert open_rows[0].markup_bps == 19_000

    async def test_clearing_an_override_returns_the_line_to_the_global_markup(
        self, async_session
    ):
        user = await _user(async_session, "clear")
        await markup.set_markup_override(
            async_session,
            provider="sarvam",
            component="stt",
            markup_bps=16_000,
            actor_user_id=user.id,
        )

        cleared = await markup.clear_markup_override(
            async_session, provider="sarvam", component="stt", actor_user_id=user.id
        )

        assert cleared is True
        assert (
            await markup.resolve_markup_override_bps(
                async_session, provider="sarvam", component="stt", at=datetime.now(UTC)
            )
            is None
        )

    async def test_clearing_nothing_reports_false(self, async_session):
        user = await _user(async_session, "clear-nothing")
        cleared = await markup.clear_markup_override(
            async_session, provider="nobody", component="llm", actor_user_id=user.id
        )
        assert cleared is False

    async def test_an_override_needs_a_provider(self, async_session):
        user = await _user(async_session, "no-provider")
        with pytest.raises(markup.MarkupError):
            await markup.set_markup_override(
                async_session,
                provider="",
                component="llm",
                markup_bps=15_000,
                actor_user_id=user.id,
            )


class TestCostEngineUsesTheOverride:
    """The one place this has to matter: what a receipt actually charges."""

    def test_a_line_with_an_override_uses_it_instead_of_the_flat_markup(self):
        cost = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=20_000,
            markup_bps=14_000,  # the blanket multiple
            usage=(UsageItem(CostComponent.LLM, "openai", 1_000, model="gpt-4o"),),
            provider_rates={
                ("llm", "openai", "gpt-4o"): RateSpec(
                    rate_mpaise=10_000, unit=RateUnit.THOUSAND_TOKENS
                ),
            },
            markup_overrides={("llm", "openai", "gpt-4o"): 20_000},
        )

        line = next(line for line in cost.line_items if line.component == "llm")
        # 1000 tokens at 10000 mpaise/1k tokens = 10000 mpaise = 1000 paise
        # vendor cost, at 2.0x override = 2000 paise, not 1.4x = 1400.
        assert line.provider_cost_paise == 1_000
        assert line.cost_paise == 2_000

    def test_a_line_with_no_override_still_uses_the_flat_markup(self):
        """An empty overrides map must behave exactly as if the parameter did
        not exist — the whole point of it being additive."""
        cost = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=20_000,
            markup_bps=14_000,
            usage=(UsageItem(CostComponent.LLM, "openai", 1_000, model="gpt-4o"),),
            provider_rates={
                ("llm", "openai", "gpt-4o"): RateSpec(
                    rate_mpaise=10_000, unit=RateUnit.THOUSAND_TOKENS
                ),
            },
            markup_overrides={},
        )

        line = next(line for line in cost.line_items if line.component == "llm")
        assert line.cost_paise == 1_400  # 1000 paise at 1.4x

    def test_a_provider_wide_override_applies_to_a_model_with_no_override_of_its_own(
        self,
    ):
        cost = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=20_000,
            markup_bps=14_000,
            usage=(UsageItem(CostComponent.TTS, "sarvam", 1_000, model="bulbul:v2"),),
            provider_rates={
                ("tts", "sarvam", "bulbul:v2"): RateSpec(
                    rate_mpaise=40_000, unit=RateUnit.THOUSAND_CHARS
                ),
            },
            markup_overrides={("tts", "sarvam", ""): 17_000},
        )

        line = next(line for line in cost.line_items if line.component == "tts")
        # 1000 chars at 40000 mpaise/1k = 40000 mpaise = 4000 paise vendor
        # cost, at 1.7x = 6800.
        assert line.provider_cost_paise == 4_000
        assert line.cost_paise == 6_800

    def test_the_platform_fee_is_never_touched_by_an_override(self):
        """Overrides key on (component, provider, model) and are only ever
        consulted inside the provider-line loop; PLATFORM never reaches that
        loop, so a bogus 99000bps entry under a "platform" key must have no
        effect at all — the fee is exactly what an unrelated markup_bps would
        produce on its own."""
        cost = compute_call_cost(
            billable_seconds=60,
            platform_rate_mpaise=20_000,
            markup_bps=14_000,
            usage=(),
            markup_overrides={("platform", "", ""): 99_000},
        )
        fee_line = next(line for line in cost.line_items if line.component == "platform")
        expected_fee = cost_paise(quantity=60, rate_mpaise=20_000, unit=RateUnit.MINUTE)
        assert fee_line.cost_paise == expected_fee
        assert fee_line.provider_cost_paise == 0
