"""Per-account limits: how many calls at once, and how much in a day.

Two ceilings that fail in different ways, which is why they are two.

**Concurrency, split by direction.** An outbound call that cannot get a slot
waits and dials a moment later and nobody notices. An inbound one is a person
already holding a ringing phone, and the only thing to do with them is refuse.
So inbound is the more expensive ceiling to hit and is the lower of the two by
default — 5 against 10 — which reserves capacity a campaign in full flight
cannot eat.

**Daily spend.** Concurrency caps how many calls run at once; nothing capped
how much they could cost. The bill is ours — calls on our carrier account,
inference on our keys — and a prepaid balance only stops spending once it is
already gone. This is a circuit breaker rather than a commercial limit, which
is why the default is generous: one that trips on an ordinary busy day is one
somebody switches off, and an account with it switched off is the account it
exists to protect.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.constants import (
    DEFAULT_DAILY_SPEND_CEILING_PAISE,
    DEFAULT_INBOUND_CONCURRENCY,
    DEFAULT_OUTBOUND_CONCURRENCY,
)
from api.db.models import CreditLedgerModel, OrganizationModel
from api.enums import CreditLedgerKind, OrganizationConfigurationKey
from api.services.billing import spend_ceiling
from api.services.call_concurrency import CallConcurrencyService, direction_for_source


async def _org(async_session, slug):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


async def _configure(async_session, org, key, value):
    from api.db.models import OrganizationConfigurationModel

    async_session.add(
        OrganizationConfigurationModel(
            organization_id=org.id, key=key.value, value={"value": value}
        )
    )
    await async_session.flush()


class TestClassifyingDirection:
    def test_a_campaign_is_outbound(self):
        assert direction_for_source("campaign:12") == "outbound"

    def test_a_carrier_leg_and_a_browser_are_both_inbound(self):
        """Both are somebody arriving at us, and both mean a person waiting."""
        assert direction_for_source("ari_inbound") == "inbound"
        assert direction_for_source("webrtc") == "inbound"
        assert direction_for_source("public_agent") == "inbound"

    def test_an_unrecognised_source_is_not_guessed(self):
        """None means "use the account's single blind limit", which is exactly
        what it did before this existed. Guessing would be worse: an outbound
        source misread as inbound takes capacity from people holding ringing
        phones."""
        assert direction_for_source("something_new") is None
        assert direction_for_source(None) is None
        assert direction_for_source("") is None


@pytest.mark.asyncio
class TestConcurrencyLimits:
    async def test_the_defaults_are_five_in_and_ten_out(
        self, db_session, async_session
    ):
        org = await _org(async_session, "defaults")
        service = CallConcurrencyService()

        assert (
            await service.get_org_concurrent_limit(org.id, direction="inbound")
            == DEFAULT_INBOUND_CONCURRENCY
            == 5
        )
        assert (
            await service.get_org_concurrent_limit(org.id, direction="outbound")
            == DEFAULT_OUTBOUND_CONCURRENCY
            == 10
        )

    async def test_inbound_is_the_lower_of_the_two(self, db_session, async_session):
        """The reservation that stops a campaign eating every slot and leaving
        real callers hearing nothing."""
        assert DEFAULT_INBOUND_CONCURRENCY < DEFAULT_OUTBOUND_CONCURRENCY

    async def test_a_per_direction_limit_wins(self, db_session, async_session):
        org = await _org(async_session, "per-direction")
        await _configure(
            async_session,
            org,
            OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT_INBOUND,
            25,
        )
        service = CallConcurrencyService()

        assert await service.get_org_concurrent_limit(org.id, direction="inbound") == 25
        # And does not leak into the other direction.
        assert (
            await service.get_org_concurrent_limit(org.id, direction="outbound")
            == DEFAULT_OUTBOUND_CONCURRENCY
        )

    async def test_an_existing_blind_limit_still_applies_to_both(
        self, db_session, async_session
    ):
        """An account that was given a number keeps it. Silently
        re-interpreting a figure somebody chose is how a customer's capacity
        changes without anybody telling them."""
        org = await _org(async_session, "legacy")
        await _configure(
            async_session, org, OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT, 40
        )
        service = CallConcurrencyService()

        assert await service.get_org_concurrent_limit(org.id, direction="inbound") == 40
        assert (
            await service.get_org_concurrent_limit(org.id, direction="outbound") == 40
        )

    async def test_a_direction_limit_overrides_the_blind_one(
        self, db_session, async_session
    ):
        """The more specific setting wins, which is the only order that lets
        somebody move one direction without touching the other."""
        org = await _org(async_session, "both-set")
        await _configure(
            async_session, org, OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT, 40
        )
        await _configure(
            async_session,
            org,
            OrganizationConfigurationKey.CONCURRENT_CALL_LIMIT_OUTBOUND,
            2,
        )
        service = CallConcurrencyService()

        assert await service.get_org_concurrent_limit(org.id, direction="outbound") == 2
        assert await service.get_org_concurrent_limit(org.id, direction="inbound") == 40


@pytest.mark.asyncio
class TestTheSpendCeiling:
    async def _spend(self, async_session, org, paise, *, kind=None, when=None):
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=-paise,
                kind=(kind or CreditLedgerKind.USAGE).value,
                balance_after_paise=0,
                created_at=when or datetime.now(UTC),
            )
        )
        await async_session.flush()

    async def test_an_account_under_its_ceiling_is_allowed(
        self, db_session, async_session
    ):
        org = await _org(async_session, "under")
        await self._spend(async_session, org, 1_000)

        assert not await spend_ceiling.would_exceed(
            async_session, organization_id=org.id
        )

    async def test_an_account_at_its_ceiling_is_stopped(
        self, db_session, async_session
    ):
        org = await _org(async_session, "at")
        await _configure(
            async_session,
            org,
            OrganizationConfigurationKey.DAILY_SPEND_CEILING_PAISE,
            10_000,
        )
        await self._spend(async_session, org, 10_000)

        assert await spend_ceiling.would_exceed(async_session, organization_id=org.id)

    async def test_rentals_count_towards_it(self, db_session, async_session):
        """Different kind of spend, same money. A ceiling that ignored rentals
        is one a runaway number-buying loop walks straight through."""
        org = await _org(async_session, "rental")
        await _configure(
            async_session,
            org,
            OrganizationConfigurationKey.DAILY_SPEND_CEILING_PAISE,
            10_000,
        )
        await self._spend(async_session, org, 10_000, kind=CreditLedgerKind.RENTAL)

        assert await spend_ceiling.would_exceed(async_session, organization_id=org.id)

    async def test_a_top_up_does_not_count_as_spend(self, db_session, async_session):
        """Buying credit is not spending it, and counting it would stop an
        account the moment it paid us."""
        org = await _org(async_session, "topup")
        await _configure(
            async_session,
            org,
            OrganizationConfigurationKey.DAILY_SPEND_CEILING_PAISE,
            10_000,
        )
        async_session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=500_000,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=500_000,
            )
        )
        await async_session.flush()

        assert not await spend_ceiling.would_exceed(
            async_session, organization_id=org.id
        )

    async def test_yesterdays_spend_does_not_count(self, db_session, async_session):
        """Daily means daily. A ceiling that never resets is a lifetime cap
        wearing the wrong name."""
        org = await _org(async_session, "yesterday")
        await _configure(
            async_session,
            org,
            OrganizationConfigurationKey.DAILY_SPEND_CEILING_PAISE,
            10_000,
        )
        await self._spend(
            async_session,
            org,
            50_000,
            when=datetime.now(UTC) - timedelta(days=2),
        )

        assert not await spend_ceiling.would_exceed(
            async_session, organization_id=org.id
        )

    async def test_a_ceiling_of_zero_disables_it(self, db_session, async_session):
        """The escape hatch. An enterprise account on an invoice should not be
        stopped by a circuit breaker meant for card fraud."""
        org = await _org(async_session, "disabled")
        await _configure(
            async_session,
            org,
            OrganizationConfigurationKey.DAILY_SPEND_CEILING_PAISE,
            0,
        )
        await self._spend(async_session, org, 10_000_000)

        assert not await spend_ceiling.would_exceed(
            async_session, organization_id=org.id
        )

    async def test_the_default_is_generous_enough_not_to_be_switched_off(self):
        """A breaker that trips on an ordinary busy day is one somebody
        disables, and a disabled breaker protects nothing."""
        assert DEFAULT_DAILY_SPEND_CEILING_PAISE >= 1_000_000

    async def test_one_account_cannot_trip_anothers_ceiling(
        self, db_session, async_session
    ):
        mine = await _org(async_session, "mine-ceiling")
        theirs = await _org(async_session, "theirs-ceiling")
        await _configure(
            async_session,
            mine,
            OrganizationConfigurationKey.DAILY_SPEND_CEILING_PAISE,
            10_000,
        )
        await self._spend(async_session, theirs, 10_000_000)

        assert not await spend_ceiling.would_exceed(
            async_session, organization_id=mine.id
        )
