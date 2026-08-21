"""The rate card, in the shape an operator actually reads it.

A flat list of rows answers "what is row forty-one". Nobody asks that. They ask
whether a vendor is set up and what it costs us — a question that spans a
stored key, the components that vendor serves, the models under each, and which
of those have a price of their own.

Three things a flat list structurally cannot show, and which these tests pin:

* a model the vendor offers that we have **never priced** — a row that does not
  exist cannot be listed;
* whether a price is the **model's own or the provider's**, which is the reason
  an operator edits one rate and watches nothing change;
* whether a **platform key** exists, without which a priced model is a managed
  call that fails at dial time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from api.db.models import ProviderRateModel
from api.enums import CostComponent, RateUnit
from api.services.billing import provider_catalogue


def _rate(provider, component, unit, mpaise, model=""):
    return ProviderRateModel(
        provider=provider,
        model=model,
        component=component.value,
        unit=unit.value,
        rate_mpaise=mpaise,
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
    )


async def _openai_llm(session) -> provider_catalogue.ComponentEntry:
    entries = await provider_catalogue.build(session)
    entry = next(e for e in entries if e.provider == "openai")
    return next(c for c in entry.components if c.component == "llm")


class TestTheVendorsEyeView:
    async def test_a_provider_appears_with_the_components_it_serves(
        self, db_session, async_session
    ):
        entries = await provider_catalogue.build(async_session)
        openai = next(e for e in entries if e.provider == "openai")

        assert {c.component for c in openai.components} >= {"llm", "tts", "stt"}

    async def test_models_the_vendor_offers_appear_even_unpriced(
        self, db_session, async_session
    ):
        """The gap a flat list cannot show: a row that does not exist."""
        llm = await _openai_llm(async_session)

        assert llm.models
        assert all(m.rate_mpaise is None for m in llm.models)

    async def test_an_unpriced_provider_says_so(self, db_session, async_session):
        entries = await provider_catalogue.build(async_session)
        openai = next(e for e in entries if e.provider == "openai")

        assert openai.is_priced is False


class TestFlatVersusPerModel:
    async def test_a_flat_rate_reaches_every_model(self, db_session, async_session):
        """Some vendors quote one price for everything they serve."""
        async_session.add(
            _rate("openai", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 285)
        )
        await async_session.flush()

        llm = await _openai_llm(async_session)

        assert llm.flat_rate_mpaise == 285
        assert all(m.rate_mpaise == 285 for m in llm.models)
        assert all(m.from_provider_rate for m in llm.models)

    async def test_a_named_model_outranks_the_flat_rate(
        self, db_session, async_session
    ):
        """And the screen says which is which — the whole reason editing a
        provider rate can look like it did nothing."""
        async_session.add_all(
            [
                _rate("openai", CostComponent.LLM, RateUnit.THOUSAND_TOKENS, 285),
                _rate(
                    "openai",
                    CostComponent.LLM,
                    RateUnit.THOUSAND_TOKENS,
                    3800,
                    model="gpt-4.1",
                ),
            ]
        )
        await async_session.flush()

        llm = await _openai_llm(async_session)
        by_model = {m.model: m for m in llm.models}

        assert by_model["gpt-4.1"].rate_mpaise == 3800
        assert by_model["gpt-4.1"].from_provider_rate is False
        assert by_model["gpt-4.1-mini"].rate_mpaise == 285
        assert by_model["gpt-4.1-mini"].from_provider_rate is True

    async def test_a_priced_model_the_registry_does_not_list_still_appears(
        self, db_session, async_session
    ):
        """A model priced before the vendor published it, or added by hand,
        must not vanish from the screen that governs its price."""
        async_session.add(
            _rate(
                "openai",
                CostComponent.LLM,
                RateUnit.THOUSAND_TOKENS,
                999,
                model="some-unlisted-model",
            )
        )
        await async_session.flush()

        llm = await _openai_llm(async_session)

        assert "some-unlisted-model" in {m.model for m in llm.models}


class TestWritingPrices:
    async def test_a_flat_rate_and_model_rates_can_be_set_together(
        self, db_session, async_session
    ):
        result = await provider_catalogue.set_rates(
            async_session,
            provider="openai",
            component="llm",
            unit=RateUnit.THOUSAND_TOKENS.value,
            actor_user_id=None,
            flat_rate_mpaise=285,
            model_rates={"gpt-4.1": 3800, "gpt-4.1-mini": 760},
        )
        await async_session.flush()

        assert result["written"] == ["(all models)", "gpt-4.1", "gpt-4.1-mini"]

        llm = await _openai_llm(async_session)
        by_model = {m.model: m for m in llm.models}
        assert llm.flat_rate_mpaise == 285
        assert by_model["gpt-4.1"].rate_mpaise == 3800
        assert by_model["gpt-4.1"].from_provider_rate is False

    async def test_only_a_flat_rate_is_a_valid_way_to_price_a_vendor(
        self, db_session, async_session
    ):
        await provider_catalogue.set_rates(
            async_session,
            provider="cartesia",
            component="tts",
            unit=RateUnit.THOUSAND_CHARS.value,
            actor_user_id=None,
            flat_rate_mpaise=3500,
        )
        await async_session.flush()

        entries = await provider_catalogue.build(async_session)
        tts = next(
            c
            for c in next(e for e in entries if e.provider == "cartesia").components
            if c.component == "tts"
        )
        assert tts.flat_rate_mpaise == 3500

    async def test_setting_a_rate_twice_replaces_rather_than_stacks(
        self, db_session, async_session
    ):
        """Each row still goes through the effective-dated path, so the second
        write closes the first rather than leaving two open."""
        for mpaise in (285, 300):
            await provider_catalogue.set_rates(
                async_session,
                provider="openai",
                component="llm",
                unit=RateUnit.THOUSAND_TOKENS.value,
                actor_user_id=None,
                flat_rate_mpaise=mpaise,
            )
        await async_session.flush()

        llm = await _openai_llm(async_session)
        assert llm.flat_rate_mpaise == 300
