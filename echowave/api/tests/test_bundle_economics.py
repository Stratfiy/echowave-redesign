"""What a bundle earns, and the one way that number can be wrong.

The operator screen shows three figures per bundle: what a customer pays, what
the vendors charge us, and the margin between them. There is exactly one way
for those to be wrong in a way nobody notices — computing any of them a second
time, on a path that then drifts from the first. Every pricing defect found
this week was a parallel sum: the wizard quoted without the markup the invoice
applied, and the realtime tier quoted a text-token assumption against audio.

So these tests do not check that the arithmetic is "right" against numbers
written here — numbers written here are a second calculation, and would pass
happily while the product quoted something else. They check that the operator
screen and the customer screen return the *same* number from the *same* path,
and that the margin is the difference between two figures rather than a third.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.db.models import OrganizationModel, ProviderRateModel
from api.services.billing.estimator import rate_card_provider
from api.services.billing.markup import resolve_markup_bps
from api.services.configuration import agent_options, managed_tiers
from api.services.configuration import bundles as bundle_service


def _rate(provider, component, unit, mpaise, model=""):
    return ProviderRateModel(
        provider=provider,
        model=model,
        component=component,
        unit=unit,
        rate_mpaise=mpaise,
        effective_from=datetime(2020, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
async def priced(async_session):
    """A price book with a real spread between tiers.

    Two mistakes were made writing this fixture before it worked, and both are
    worth stating because either one silently turns these tests green while
    testing nothing:

    * With **no rates at all**, every component prices at zero and each tier
      collapses to the bare platform fee — so "Lite is cheaper than Smart"
      compares two identical numbers and passes.
    * With rates **a hundred times too small**, the per-minute cost lands below
      one paise, integer rounding takes it to zero, and the tiers collapse
      again — the same false pass, from a different direction.

    The figures below are the vendors' own published rates, in millipaise per
    rate unit, which land the per-minute cost in rupees rather than in the
    rounding error.
    """
    await bundle_service.ensure_seeded(async_session)
    # Named from the tier vocabulary rather than typed here. Writing the names
    # by hand produced a fixture where two tiers resolved to the same vendor
    # model — ``resolve`` falls back to the default tier for a name it does not
    # know, silently — and a price book with one row per *tier* then collides on
    # the unique index, because the key is the vendor model, not the tier.
    llm_rates = {"lite": 1_500, "default": 15_000, "accurate": 120_000}
    for tier in managed_tiers.LLM_TIERS:
        upstream = managed_tiers.resolve("llm", tier)
        async_session.add(
            _rate(
                upstream.provider,
                "llm",
                "1k_tokens",
                llm_rates[tier],
                model=upstream.model,
            )
        )
    for component, unit, mpaise in (
        ("stt", "minute", 60_000),
        ("tts", "1k_chars", 180_000),
    ):
        upstream = managed_tiers.resolve(component, "default")
        async_session.add(
            _rate(upstream.provider, component, unit, mpaise, model=upstream.model)
        )

    # The speech-to-speech bundles too, or their variants price at the bare
    # platform fee — which is not zero, so a "skip the unpriced ones" guard
    # would not catch it, and "the customer pays more than the vendors charge"
    # would compare the platform fee against itself and fail for the right
    # reason at the wrong moment. A realtime session is metered as language
    # model usage, which is why it is priced under that component.
    #
    # Seeded under **rate_card_provider(...)**, not under the tier's own
    # provider name. That distinction is the whole of a defect this fixture
    # used to hide: the tier says ``openai_realtime`` and the price book says
    # ``decibylopenairealtime``, because the latter is what
    # ``provider_from_processor`` derives from the running pipeline and
    # therefore the only name a real call is ever costed under. Seeding under
    # the tier name made these tests pass against a price book no production
    # deployment has, while the live Simple picker quoted Rs2.76 a minute for a
    # model costing Rs25.79.
    #
    # Deduplicated by vendor model, because several realtime tiers can point at
    # the same one — the price book is keyed by what we buy, not by what we
    # call it.
    seen: set[tuple[str, str]] = set()
    for tier in managed_tiers.REALTIME_TIERS:
        upstream = managed_tiers.resolve(managed_tiers.REALTIME_COMPONENT, tier)
        priced_as = rate_card_provider(upstream.provider)
        if (priced_as, upstream.model) in seen:
            continue
        seen.add((priced_as, upstream.model))
        async_session.add(
            _rate(priced_as, "llm", "1k_tokens", 40_000, model=upstream.model)
        )
    await async_session.flush()


class TestTheOperatorAndTheCustomerSeeOneNumber:
    async def test_the_price_is_the_customers_price_to_the_paise(
        self, db_session, async_session, priced
    ):
        """Not "close to" — the same integer, from the same call.

        An operator who prices a bundle against a figure the customer never
        sees is pricing a different product.
        """
        customer = await agent_options.bundle_options(
            async_session, organization_id=None
        )
        operator = await agent_options.bundle_economics(async_session)

        customer_prices = {
            (b["slug"], v["tier"]): v["paise_per_minute"]
            for b in customer
            for v in b["variants"]
        }
        operator_prices = {
            (b["slug"], v["tier"]): v["paise_per_minute"]
            for b in operator
            for v in b["variants"]
        }

        assert operator_prices == customer_prices
        assert customer_prices, "no bundles were priced, so nothing was compared"

    async def test_margin_is_the_difference_and_not_a_third_number(
        self, db_session, async_session, priced
    ):
        operator = await agent_options.bundle_economics(async_session)

        for bundle in operator:
            for variant in bundle["variants"]:
                assert (
                    variant["margin_paise_per_minute"]
                    == variant["paise_per_minute"] - variant["cost_paise_per_minute"]
                )

    async def test_the_customer_pays_more_than_the_vendors_charge(
        self, db_session, async_session, priced
    ):
        """The markup, observed rather than asserted from a constant.

        Pinning the multiple itself would restate the configuration; what
        matters is that the marked-up path is genuinely above the raw one, so a
        markup accidentally dropped from the quote — which is exactly the bug
        the wizard shipped with — fails here.
        """
        operator = await agent_options.bundle_economics(async_session)
        markup_bps = await resolve_markup_bps(async_session)

        checked = 0
        for bundle in operator:
            for variant in bundle["variants"]:
                if variant["cost_paise_per_minute"] == 0:
                    continue  # unpriced stack: nothing to mark up
                assert variant["paise_per_minute"] > variant["cost_paise_per_minute"], (
                    f"{bundle['slug']}/{variant['tier']} earns nothing"
                )
                checked += 1

        assert markup_bps > 10_000, "the markup is not configured above cost"
        assert checked, "every bundle priced at zero, so no margin was checked"


class TestPricingWithNoAccountAttached:
    async def test_an_account_override_does_not_move_the_operator_view(
        self, db_session, async_session, priced
    ):
        """One customer's contract is not everybody's list price.

        The screen exists to decide what a bundle should cost in general.
        Quoting it against whichever account happened to be at hand would
        publish a negotiated rate as the default.
        """
        from api.services.billing.rate_card import set_account_rate

        org = OrganizationModel(provider_id="org-economics", quota_decibyl_tokens=0)
        async_session.add(org)
        await async_session.flush()

        before = await agent_options.bundle_economics(async_session)
        await set_account_rate(
            async_session,
            actor_user_id=None,
            organization_id=org.id,
            platform_rate_mpaise=999_999,
            effective_from=datetime(2019, 1, 1, tzinfo=UTC),
        )
        await async_session.flush()
        after = await agent_options.bundle_economics(async_session)

        assert before == after


class TestTheTiersStillDiffer:
    async def test_a_smarter_brain_costs_more(self, db_session, async_session, priced):
        """Guards the fixture as much as the code: if the price book were
        empty or scaled wrong, every tier would collapse to the platform fee
        and the tests above would pass while measuring nothing."""
        operator = await agent_options.bundle_economics(async_session)
        everyday = next(b for b in operator if b["slug"] == "everyday")
        by_tier = {v["tier"]: v["paise_per_minute"] for v in everyday["variants"]}

        assert by_tier["lite"] < by_tier["default"] < by_tier["accurate"]
