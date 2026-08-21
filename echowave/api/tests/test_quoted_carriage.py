"""A per-minute quote either contains the carrier's minutes or says it does not.

Carriage is an exact per-minute rate and routinely a third of the cost of a
call, and two screens were getting it wrong in opposite directions — both
confidently, which is the shape that costs money.

**The create wizard passed no carrier at all.** ``GET /agent-options`` called
``price_per_minute`` with no ``telephony_provider``, so the one screen a
first-time buyer reads before paying quoted a stack with no carriage in it —
about 10% light for an account whose numbers are ours, and the omission was
never stated on the page.

**The Models screen passed the wrong one.** It priced whatever the default
outbound configuration named, whether or not that configuration was ours. An
account dialling on its own Twilio was quoted a carriage line on top of the
invoice Twilio already sends it: the same double charge ``carriage.py`` exists
to refuse on the bill, appearing instead on the estimate.

So the answer is one function — "which carrier's minutes would land on *this*
account's invoice" — and both screens ask it. The three states below are the
whole of it, and each one owes the customer a different sentence.
"""

from __future__ import annotations

from api.db.models import OrganizationModel, TelephonyConfigurationModel
from api.services.telephony import carriage


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _config(
    session,
    org,
    *,
    provider: str,
    managed: bool,
    default_outbound: bool = False,
    name: str = "cfg",
) -> TelephonyConfigurationModel:
    row = TelephonyConfigurationModel(
        organization_id=org.id,
        name=name,
        provider=provider,
        credentials={"auth_id": "MA", "auth_token": "t"},
        is_platform_managed=managed,
        is_default_outbound=default_outbound,
    )
    session.add(row)
    await session.flush()
    return row


class TestTheThreeStates:
    async def test_no_number_yet_excludes_carriage_and_says_the_price_will_grow(
        self, db_session, async_session
    ):
        org = await _org(async_session, "none")

        basis = await carriage.billable_carrier(
            async_session, organization_id=org.id
        )

        assert basis.provider is None
        assert basis.reason == carriage.NO_CARRIER
        # The distinction that matters: this price is *incomplete*, and the
        # customer has to know it will go up rather than assume calls are free.
        assert "no phone number is connected" in basis.explanation

    async def test_their_own_carrier_excludes_carriage_because_we_bill_none(
        self, db_session, async_session
    ):
        """Complete as our invoice, and it has to say whose invoice it is not.

        Adding a carriage line here would charge twice for one phone call.
        """
        org = await _org(async_session, "byok")
        await _config(async_session, org, provider="twilio", managed=False)

        basis = await carriage.billable_carrier(
            async_session, organization_id=org.id
        )

        assert basis.provider is None
        assert basis.reason == carriage.CUSTOMER_CARRIER
        assert "your own twilio account" in basis.explanation.lower()

    async def test_our_carrier_is_priced_and_named(self, db_session, async_session):
        org = await _org(async_session, "managed")
        await _config(async_session, org, provider="plivo", managed=True)

        basis = await carriage.billable_carrier(
            async_session, organization_id=org.id
        )

        assert basis.provider == "plivo"
        assert basis.reason == carriage.MANAGED
        assert "plivo" in basis.explanation


class TestWhichConfigurationAnswers:
    async def test_the_default_outbound_one_wins(self, db_session, async_session):
        """The configuration a call actually leaves through.

        An account can hold several. Pricing the wrong one is how a quote ends
        up naming a carrier the customer never dials on.
        """
        org = await _org(async_session, "several")
        await _config(
            async_session, org, provider="twilio", managed=False, name="old"
        )
        await _config(
            async_session,
            org,
            provider="plivo",
            managed=True,
            default_outbound=True,
            name="current",
        )

        basis = await carriage.billable_carrier(
            async_session, organization_id=org.id
        )

        assert basis.provider == "plivo"

    async def test_with_no_default_flagged_the_first_answers(
        self, db_session, async_session
    ):
        """Right for the common case of one carrier and nothing flagged, and
        the same fallback ``telephony/factory.py`` makes."""
        org = await _org(async_session, "unflagged")
        await _config(async_session, org, provider="plivo", managed=True, name="only")

        basis = await carriage.billable_carrier(
            async_session, organization_id=org.id
        )

        assert basis.provider == "plivo"


class TestTheQuoteActuallyMoves:
    """The point of all of it: the number on the screen changes."""

    async def test_a_managed_carrier_raises_the_quoted_minute(
        self, db_session, async_session
    ):
        from datetime import UTC, datetime

        from api.db.models import ProviderRateModel
        from api.enums import CostComponent, RateUnit
        from api.services.billing.estimator import rate_card_provider
        from api.services.configuration import agent_options, managed_tiers

        org = await _org(async_session, "moves")

        # Whatever the managed tiers resolve to today, priced — so this asserts
        # against a real total rather than skipping when the database is bare.
        # Keyed through ``rate_card_provider`` because a realtime vendor is
        # named one way by the service factory and another by the rate card,
        # and seeding under the wrong one is exactly how a stack came to be
        # quoted at a ninth of its real price.
        for component, unit in (
            ("stt", RateUnit.MINUTE),
            ("llm", RateUnit.THOUSAND_TOKENS),
            ("tts", RateUnit.THOUSAND_CHARS),
        ):
            upstream = managed_tiers.resolve(component, "default")
            async_session.add(
                ProviderRateModel(
                    provider=rate_card_provider(upstream.provider),
                    model=upstream.model,
                    component=CostComponent(component).value,
                    unit=unit.value,
                    rate_mpaise=10_000,
                    effective_from=datetime(2020, 1, 1, tzinfo=UTC),
                )
            )
        async_session.add(
            ProviderRateModel(
                provider="plivo",
                model="",
                component=CostComponent.TELEPHONY.value,
                unit=RateUnit.MINUTE.value,
                rate_mpaise=60_000,  # Rs0.60 a minute, Plivo India local.
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        await async_session.flush()

        without = await agent_options.price_per_minute(
            async_session, organization_id=org.id, brain="default"
        )
        with_carrier = await agent_options.price_per_minute(
            async_session,
            organization_id=org.id,
            brain="default",
            telephony_provider="plivo",
        )

        assert without is not None and with_carrier is not None
        # Additive, and never free: the whole bug was a line worth roughly a
        # tenth of a call quietly reading as zero.
        assert with_carrier > without
