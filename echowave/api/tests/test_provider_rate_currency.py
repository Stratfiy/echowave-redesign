"""A provider rate quoted in dollars follows the rupee; one in rupees does not.

``provider_rates`` used to hold a single currency, millipaise, which is right
for a vendor who invoices in rupees and wrong for one who invoices in dollars.
Storing a dollar vendor's price as rupees freezes the conversion at whatever
the rate was the day somebody typed it: the dollar price did not change when
the rupee moved, but the cost did, and nothing on the card knew.

So the row carries the currency it was quoted in, the same split
``organization_rate_history`` already uses for the platform rate, and the
conversion happens at read time. These tests pin the behavioural difference
that split exists for — not merely that the column round-trips.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.db.models import ProviderRateModel, UsdInrRateHistoryModel
from api.enums import CostComponent, RateUnit
from api.services.billing.rates import resolve_provider_rate

JAN = datetime(2026, 1, 1, tzinfo=UTC)
JUN = datetime(2026, 6, 1, tzinfo=UTC)
DEC = datetime(2026, 12, 1, tzinfo=UTC)


@pytest.mark.asyncio
class TestQuotedInDollars:
    async def test_the_cost_moves_when_the_rupee_does(self, async_session):
        """The whole reason the dollar column exists.

        One row, unchanged, read at two times with two exchange rates in force.
        A vendor billing $0.01/minute costs more rupees after the rupee weakens,
        and the card has to say so without anybody re-entering the price.
        """
        async_session.add_all(
            [
                UsdInrRateHistoryModel(
                    paise_per_usd=8_000, effective_from=JAN, effective_to=JUN
                ),
                UsdInrRateHistoryModel(paise_per_usd=10_000, effective_from=JUN),
                ProviderRateModel(
                    provider="deepgram",
                    component=CostComponent.STT.value,
                    unit=RateUnit.MINUTE.value,
                    rate_micros_usd=10_000,  # $0.01 per minute
                    effective_from=JAN,
                ),
            ]
        )
        await async_session.flush()

        cheap = await resolve_provider_rate(
            async_session,
            provider="deepgram",
            component=CostComponent.STT,
            at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        dear = await resolve_provider_rate(
            async_session, provider="deepgram", component=CostComponent.STT, at=DEC
        )

        # $0.01 at ₹80 is 80 paise; at ₹100 it is 100 paise. In millipaise.
        assert cheap.rate_mpaise == 80_000
        assert dear.rate_mpaise == 100_000

        # The dollar price is reported too, so a reader can tell a vendor that
        # got dearer from a rupee that got weaker.
        assert dear.rate_micros_usd == 10_000
        assert dear.usd_inr_paise == 10_000

    async def test_a_rupee_row_is_never_converted(self, async_session):
        """Deliberately unmoved by FX — that is what quoting in rupees means.

        Sarvam publishes ₹30/hour. That number does not change because the
        dollar did, and applying an exchange rate to it would invent a cost
        change no invoice will ever show.
        """
        async_session.add_all(
            [
                UsdInrRateHistoryModel(
                    paise_per_usd=8_000, effective_from=JAN, effective_to=JUN
                ),
                UsdInrRateHistoryModel(paise_per_usd=10_000, effective_from=JUN),
                ProviderRateModel(
                    provider="sarvam",
                    component=CostComponent.STT.value,
                    unit=RateUnit.MINUTE.value,
                    rate_mpaise=50_000,
                    effective_from=JAN,
                ),
            ]
        )
        await async_session.flush()

        before = await resolve_provider_rate(
            async_session,
            provider="sarvam",
            component=CostComponent.STT,
            at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        after = await resolve_provider_rate(
            async_session, provider="sarvam", component=CostComponent.STT, at=DEC
        )

        assert before.rate_mpaise == after.rate_mpaise == 50_000
        # No dollar figure and no FX: there was no conversion to report.
        assert after.rate_micros_usd is None
        assert after.usd_inr_paise is None

    async def test_a_model_row_in_dollars_still_outranks_the_flat_rate(
        self, async_session
    ):
        """Currency must not disturb the resolution order.

        The most specific row wins whichever currency either row is in — a
        dollar-quoted model rate over a rupee-quoted provider-wide one is a
        real combination, since a vendor can publish one headline price in
        rupees and quote a particular model in dollars.
        """
        async_session.add_all(
            [
                UsdInrRateHistoryModel(paise_per_usd=10_000, effective_from=JAN),
                ProviderRateModel(
                    provider="openai",
                    model="",
                    component=CostComponent.LLM.value,
                    unit=RateUnit.THOUSAND_TOKENS.value,
                    rate_mpaise=1_000,
                    effective_from=JAN,
                ),
                ProviderRateModel(
                    provider="openai",
                    model="gpt-4.1",
                    component=CostComponent.LLM.value,
                    unit=RateUnit.THOUSAND_TOKENS.value,
                    rate_micros_usd=3_800,
                    effective_from=JAN,
                ),
            ]
        )
        await async_session.flush()

        named = await resolve_provider_rate(
            async_session,
            provider="openai",
            component=CostComponent.LLM,
            at=DEC,
            model="gpt-4.1",
        )
        fallback = await resolve_provider_rate(
            async_session,
            provider="openai",
            component=CostComponent.LLM,
            at=DEC,
            model="gpt-4o-mini",
        )

        assert named.model == "gpt-4.1"
        assert named.rate_mpaise == 38_000  # $0.0038 at ₹100
        assert fallback.model == ""
        assert fallback.rate_mpaise == 1_000
