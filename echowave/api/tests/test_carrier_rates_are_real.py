"""No customer is billed a marked-up minute at a price nobody checked.

Four carriers have never had their India outbound rate read off a published
price list. Two carried stand-ins at the Twilio India mobile figure; Telnyx and
Vonage carried their **US** rates — $0.0070 and $0.0139, about ₹0.73 and ₹1.45
— against traffic that is Indian, where termination is a different and dearer
market.

While carriage was cost-only, erring high was free: we under-reported our own
margin until somebody corrected it. Carriage is now marked up into the sell
price like every other component, which removes the safe direction entirely. A
stand-in that is too high overcharges the customer; one that is too low sells
the minute at a loss; **the invoice reads identically either way**, which is
what makes this the dangerous shape rather than merely the untidy one.

So the rule is narrow and absolute, and these tests hold it: a carrier whose
live rate still carries the stand-in marker cannot be put on the managed path.
Not warned about — refused, at the route that decides it.

It costs nothing today. A customer on their own Telnyx account is billed by
Telnyx and we add no carriage at all, so the rule bites only where Decibyl
fronts the carrier — which is exactly where the number has to be right.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from api.db.models import (
    OrganizationModel,
    ProviderRateModel,
    TelephonyConfigurationModel,
    UserModel,
)
from api.enums import CostComponent, RateUnit, StaffRole
from api.services.billing import carrier_rates
from api.services.billing.default_rates import (
    PROVISIONAL_MARKER,
    TELEPHONY_RATES,
)

LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


def _carriage_rate(
    provider: str, *, note: str, mpaise: int = 1_200
) -> ProviderRateModel:
    return ProviderRateModel(
        provider=provider,
        model="",
        component=CostComponent.TELEPHONY.value,
        unit=RateUnit.MINUTE.value,
        rate_mpaise=mpaise,
        effective_from=LONG_AGO,
        note=note,
    )


def _seeded_note(rate) -> str:
    """What ``scripts/seed_provider_rates`` writes for one default row."""
    return f"Seeded default ({rate.basis})"


class TestTheFileSaysWhichPricesAreGuesses:
    def test_the_flag_and_the_words_agree(self):
        """An operator reads the basis; the code reads the flag.

        If those two ever disagree, one audience is being told something the
        other is not, and the rate card is the audience that matters least at
        3am.
        """
        for rate in TELEPHONY_RATES:
            marked = PROVISIONAL_MARKER in rate.basis
            assert marked is rate.provisional, (
                f"{rate.provider}: provisional={rate.provisional} but its basis "
                f"{'does not say' if rate.provisional else 'says'} so"
            )

    def test_exactly_the_four_we_could_not_verify(self):
        assert carrier_rates.seeded_provisional_carriers() == frozenset(
            {"cloudonix", "vobiz", "telnyx", "vonage"}
        )

    def test_telnyx_and_vonage_no_longer_carry_a_us_rate(self):
        """The bug, stated as a number.

        Telnyx at $0.0070 is roughly ₹0.73 a minute; India mobile termination
        is nowhere near that. A US figure read as an India figure understates
        every managed minute, and with carriage marked up it undersells them.
        """
        rates = {r.provider: r for r in TELEPHONY_RATES}
        stand_in = rates["cloudonix"].usd_per_unit

        for provider in ("telnyx", "vonage"):
            assert rates[provider].usd_per_unit == stand_in, (
                f"{provider} is not on the India stand-in rate"
            )
            # The basis still names the old figure — that is the audit trail,
            # not the price. What must not survive is the rate itself.
            assert rates[provider].provisional
            assert rates[provider].basis.startswith(PROVISIONAL_MARKER)

    def test_no_carrier_is_priced_at_zero(self):
        """A carrier priced at nothing reads as costed and contributes nothing,
        so margin comes out better than it is on every call that uses it."""
        for rate in TELEPHONY_RATES:
            assert rate.usd_per_unit > 0, f"{rate.provider} carriage is priced at zero"


class TestWhatIsActuallyInForce:
    """The question that decides whether money moves is about the live rate."""

    async def test_a_seeded_stand_in_is_reported(self, db_session, async_session):
        rate = next(r for r in TELEPHONY_RATES if r.provider == "telnyx")
        async_session.add(_carriage_rate("telnyx", note=_seeded_note(rate)))
        await async_session.flush()

        assert "telnyx" in await carrier_rates.provisionally_priced(async_session)

    async def test_superseding_it_with_a_real_price_clears_it(
        self, db_session, async_session
    ):
        """The intended way out, and it needs no code change.

        An operator entering the published rate closes the seeded row and
        writes their own note. The marker goes with it.
        """
        rate = next(r for r in TELEPHONY_RATES if r.provider == "vonage")
        seeded = _carriage_rate("vonage", note=_seeded_note(rate))
        async_session.add(seeded)
        await async_session.flush()

        seeded.effective_to = datetime.now(UTC) - timedelta(seconds=1)
        async_session.add(
            _carriage_rate(
                "vonage",
                note="Vonage published India mobile outbound, checked 2026-08",
                mpaise=9_100,
            )
        )
        await async_session.flush()

        assert (
            await carrier_rates.is_provisionally_priced(
                async_session, provider="vonage"
            )
            is False
        )

    async def test_a_carrier_with_no_rate_at_all_is_not_provisional(
        self, db_session, async_session
    ):
        """Unpriced is a different failure with its own guard. Conflating the
        two would let a carrier with no row at all pass as merely uncertain."""
        assert (
            await carrier_rates.is_provisionally_priced(
                async_session, provider="twilio"
            )
            is False
        )


async def _org_and_config(session, slug: str, provider: str, *, managed: bool = False):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    config = TelephonyConfigurationModel(
        organization_id=org.id,
        name=f"cfg-{slug}",
        provider=provider,
        credentials={"auth_id": "MA", "auth_token": "t"},
        is_platform_managed=managed,
    )
    session.add(config)
    await session.flush()
    return org, config


async def _staff(session, slug: str) -> UserModel:
    user = UserModel(provider_id=f"staff-{slug}", staff_role=StaffRole.SUPERADMIN.value)
    session.add(user)
    await session.flush()
    return user


def _client(user):
    from contextlib import asynccontextmanager

    from api.app import app
    from api.services.auth.depends import get_superuser

    @asynccontextmanager
    async def _ctx():
        async def _override():
            return user

        app.dependency_overrides[get_superuser] = _override
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_superuser, None)

    return _ctx()


class TestTheManagedPathRefusesAGuess:
    @pytest.fixture(autouse=True)
    async def _stand_in_on_file(self, db_session, async_session):
        rate = next(r for r in TELEPHONY_RATES if r.provider == "telnyx")
        async_session.add(_carriage_rate("telnyx", note=_seeded_note(rate)))
        async_session.add(
            _carriage_rate("plivo", note="Plivo India outbound local, published")
        )
        await async_session.flush()

    async def test_a_carrier_on_a_stand_in_cannot_be_sold(
        self, db_session, async_session
    ):
        _, config = await _org_and_config(async_session, "telnyx", "telnyx")
        staff = await _staff(async_session, "guess")

        async with _client(staff) as client:
            response = await client.put(
                f"/api/v1/admin/telephony/configurations/{config.id}/platform-managed",
                json={"managed": True},
            )

        assert response.status_code == 409
        detail = response.json()["detail"]
        # Both halves, because a refusal an operator cannot act on is an outage.
        assert "telnyx" in detail
        assert "rate-card" in detail

    async def test_a_verified_carrier_goes_through(self, db_session, async_session):
        _, config = await _org_and_config(async_session, "plivo", "plivo")
        staff = await _staff(async_session, "real")

        async with _client(staff) as client:
            response = await client.put(
                f"/api/v1/admin/telephony/configurations/{config.id}/platform-managed",
                json={"managed": True},
            )

        assert response.status_code == 200
        assert response.json()["is_platform_managed"] is True

    async def test_turning_it_off_is_never_blocked_by_the_price(
        self, db_session, async_session
    ):
        """Backing out of a mistake must not need the price fixed first."""
        _, config = await _org_and_config(async_session, "undo", "telnyx", managed=True)
        staff = await _staff(async_session, "undo")

        async with _client(staff) as client:
            response = await client.put(
                f"/api/v1/admin/telephony/configurations/{config.id}/platform-managed",
                json={"managed": False},
            )

        assert response.status_code == 200
        assert response.json()["is_platform_managed"] is False


class TestReadinessSaysWhichAndWhy:
    async def _check(self, session):
        from api.services.billing import readiness

        return await readiness._carrier_price_check(session)

    async def test_nothing_provisional_reads_clean(self, db_session, async_session):
        async_session.add(
            _carriage_rate("plivo", note="Plivo India outbound local, published")
        )
        await async_session.flush()

        check = await self._check(async_session)
        assert check.status == "ready"
        assert "stand-in" in check.detail

    async def test_a_stand_in_nobody_sells_is_reported_but_not_a_blocker(
        self, db_session, async_session
    ):
        """Nothing is mispriced, because nothing can be sold on it.

        Reported anyway: an operator who tries to turn one on will be refused
        at the route, and this is where that refusal is explained in advance.
        """
        rate = next(r for r in TELEPHONY_RATES if r.provider == "vobiz")
        async_session.add(_carriage_rate("vobiz", note=_seeded_note(rate)))
        await async_session.flush()

        check = await self._check(async_session)
        assert check.status == "ready"
        assert "vobiz" in check.detail
        assert "cannot be put on the managed path" in check.detail

    async def test_a_stand_in_already_being_sold_is_a_blocker(
        self, db_session, async_session
    ):
        """The case that predates the guard, and the only one costing money."""
        rate = next(r for r in TELEPHONY_RATES if r.provider == "cloudonix")
        async_session.add(_carriage_rate("cloudonix", note=_seeded_note(rate)))
        await _org_and_config(async_session, "sold", "cloudonix", managed=True)
        await async_session.flush()

        check = await self._check(async_session)
        assert check.status == "action_required"
        assert "cloudonix" in check.detail
        assert check.remedy
