"""Phone numbers while nothing is charged.

A number is rent Decibyl pays a carrier every month, and there is no longer a
price or a standing instruction to collect it. Three things follow, and this
pins each:

* no autopay is asked for, so a verified, agreed account can simply get one;
* a per-account cap (``MAX_MANAGED_NUMBERS_PER_ACCOUNT``) is what bounds the
  rent we carry, and giving a number back frees its place;
* a number is never suspended or released because billing stopped, however
  long a charge has been failing.

No price reaches the response either.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from api import constants
from api.db import db_client
from api.db.models import (
    AgreementAcceptanceModel,
    OrganizationKycModel,
    OrganizationModel,
    RecurringChargeModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    UserModel,
)
from api.enums import KycStatus, PhoneNumberStatus, RecurringChargeStatus
from api.services.billing import rentals
from api.services.compliance.agreements import CURRENT_VERSIONS
from api.services.telephony import number_lifecycle, provisioning
from api.services.telephony.base import (
    AvailableNumber,
    NumberPurchaseResult,
    ProviderSyncResult,
)

pytestmark = pytest.mark.checkout_closed


class FakeProvider:
    PROVIDER_NAME = "plivo"

    def __init__(self):
        self.bought: list[str] = []
        self.released: list[str] = []

    def supports_number_management(self):
        return True

    async def search_available_numbers(self, **kwargs):
        return [
            AvailableNumber(
                number="+918041234567",
                country_iso="IN",
                number_type="local",
                city="Bangalore",
                monthly_rental="2.50",
                setup_price="1.00",
            )
        ]

    async def buy_number(self, number, *, app_id=None, idempotency_key=None):
        self.bought.append(number)
        return NumberPurchaseResult(
            ok=True, number=number, carrier_number_id=f"cn-{len(self.bought)}"
        )

    async def release_number(self, number):
        self.released.append(number)
        return ProviderSyncResult(ok=True)


class FakeCompliance:
    async def link(self, *, number, compliance_application_id):
        return {"status": "ok"}


@pytest.fixture
def fake_provider(monkeypatch):
    provider = FakeProvider()

    async def _get(config_id, organization_id):
        return provider

    monkeypatch.setattr(provisioning, "_provider", _get)
    monkeypatch.setattr(
        "api.services.telephony.factory.get_telephony_provider_by_id", _get
    )
    monkeypatch.setattr(provisioning, "PLATFORM_PLIVO_APPLICATION_ID", "app-inbound")
    return provider


async def _account(session, slug: str):
    """Verified and agreed, with no mandate: the whole point."""
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    session.add(
        OrganizationKycModel(
            organization_id=org.id,
            status=KycStatus.CARRIER_APPROVED.value,
            carrier="plivo",
            carrier_reference="app-approved",
        )
    )
    for key, version in CURRENT_VERSIONS.items():
        session.add(
            AgreementAcceptanceModel(
                organization_id=org.id,
                user_id=user.id,
                agreement=key,
                version=version,
            )
        )
    config = TelephonyConfigurationModel(
        organization_id=org.id,
        name=f"cfg-{slug}",
        provider="plivo",
        credentials={"auth_id": "MA123", "auth_token": "token"},
        is_platform_managed=True,
    )
    session.add(config)
    await session.flush()
    return org.id, config.id


async def _get_one(org_id: int, config_id: int, n: int):
    return await provisioning.provision(
        organization_id=org_id,
        telephony_configuration_id=config_id,
        address=f"+9180412345{n:02d}",
        compliance_client=FakeCompliance(),
    )


class TestNoAutopayIsAsked:
    async def test_an_account_with_no_mandate_gets_a_number(
        self, db_session, async_session, fake_provider
    ):
        org_id, config_id = await _account(async_session, "no-mandate")
        assert constants.REQUIRE_MANDATE_FOR_NUMBERS is False

        result = await _get_one(org_id, config_id, 1)

        assert fake_provider.bought == ["+918041234501"]
        assert result.recurring_charge_id is not None
        assert not hasattr(result, "monthly_price_paise"), "no price in the result"


class TestThePerAccountCap:
    async def test_the_cap_is_a_constant_of_a_few_numbers(self):
        assert isinstance(constants.MAX_MANAGED_NUMBERS_PER_ACCOUNT, int)
        assert 1 <= constants.MAX_MANAGED_NUMBERS_PER_ACCOUNT <= 10

    async def test_a_number_beyond_the_cap_is_refused_before_anything_is_bought(
        self, db_session, async_session, fake_provider, monkeypatch
    ):
        monkeypatch.setattr(constants, "MAX_MANAGED_NUMBERS_PER_ACCOUNT", 2)
        org_id, config_id = await _account(async_session, "cap")
        await _get_one(org_id, config_id, 1)
        await _get_one(org_id, config_id, 2)

        with pytest.raises(provisioning.ProvisioningError) as refused:
            await _get_one(org_id, config_id, 3)

        assert "most for one account (2)" in str(refused.value)
        assert fake_provider.bought == ["+918041234501", "+918041234502"]
        assert "₹" not in str(refused.value)

    async def test_giving_one_back_frees_its_place(
        self, db_session, async_session, fake_provider, monkeypatch
    ):
        monkeypatch.setattr(constants, "MAX_MANAGED_NUMBERS_PER_ACCOUNT", 1)
        org_id, config_id = await _account(async_session, "give-back")
        first = await _get_one(org_id, config_id, 1)
        with pytest.raises(provisioning.ProvisioningError):
            await _get_one(org_id, config_id, 2)

        await number_lifecycle.release_number(
            phone_number_id=first.phone_number_id,
            actor_user_id=None,
            reason="no longer needed",
            force=True,
        )

        again = await _get_one(org_id, config_id, 2)
        assert again.phone_number_id != first.phone_number_id

    async def test_one_account_s_numbers_do_not_count_against_another(
        self, db_session, async_session, fake_provider, monkeypatch
    ):
        monkeypatch.setattr(constants, "MAX_MANAGED_NUMBERS_PER_ACCOUNT", 1)
        a_org, a_config = await _account(async_session, "cap-a")
        b_org, b_config = await _account(async_session, "cap-b")
        await _get_one(a_org, a_config, 1)

        await _get_one(b_org, b_config, 2)  # does not raise

        async with db_client.async_session() as session:
            assert await rentals.numbers_rented_by(session, organization_id=a_org) == 1
            assert await rentals.numbers_rented_by(session, organization_id=b_org) == 1


class TestNoPriceIsShown:
    async def test_the_search_response_carries_no_rental_or_setup_price(
        self, db_session, async_session, fake_provider, monkeypatch
    ):
        from api.routes import managed_numbers

        org_id, config_id = await _account(async_session, "search")
        user = UserModel(provider_id="user-search-2", selected_organization_id=org_id)
        async_session.add(user)
        await async_session.flush()

        response = await managed_numbers.search_numbers(
            managed_numbers.NumberSearchRequest(
                telephony_configuration_id=config_id, country_iso="IN"
            ),
            user=user,
        )

        assert response["numbers"], "the search still finds numbers"
        for row in response["numbers"]:
            assert "monthly_rental" not in row and "setup_price" not in row

    async def test_the_purchase_response_carries_no_price(
        self, db_session, async_session, fake_provider, monkeypatch
    ):
        from api.routes import managed_numbers

        org_id, config_id = await _account(async_session, "buy")
        user = UserModel(provider_id="user-buy-2", selected_organization_id=org_id)
        async_session.add(user)
        await async_session.flush()

        real_provision = provisioning.provision

        async def _provision_with_a_fake_compliance_client(**kwargs):
            return await real_provision(**kwargs, compliance_client=FakeCompliance())

        monkeypatch.setattr(
            provisioning, "provision", _provision_with_a_fake_compliance_client
        )
        response = await managed_numbers.provision_number(
            managed_numbers.ProvisionRequest(
                telephony_configuration_id=config_id, address="+918041234599"
            ),
            user=user,
        )

        assert "monthly_price_paise" not in response
        assert response["address"] == "+918041234599"


class TestANumberIsNeverLostToBilling:
    NOW = datetime(2026, 11, 1, tzinfo=UTC)

    async def test_a_charge_failing_for_months_suspends_nothing(
        self, db_session, async_session, fake_provider
    ):
        org_id, config_id = await _account(async_session, "months")
        result = await _get_one(org_id, config_id, 1)
        async with db_client.async_session() as session:
            charge = await session.get(RecurringChargeModel, result.recurring_charge_id)
            charge.first_failed_at = self.NOW - timedelta(days=90)
            charge.next_charge_at = self.NOW - timedelta(days=1)
            await session.commit()

        outcome = await rentals.charge_period(result.recurring_charge_id, now=self.NOW)

        assert outcome.charged is False  # nobody has any balance to take it from
        async with db_client.async_session() as session:
            number = await session.get(
                TelephonyPhoneNumberModel, result.phone_number_id
            )
            charge = await session.get(RecurringChargeModel, result.recurring_charge_id)
        assert number.status == PhoneNumberStatus.ACTIVE.value
        assert charge.status == RecurringChargeStatus.PAST_DUE.value
        assert outcome.dunning_state.should_warn is False
        assert outcome.dunning_state.should_suspend is False

    async def test_an_unforced_release_for_non_payment_is_never_permitted(
        self, db_session, async_session, fake_provider
    ):
        org_id, config_id = await _account(async_session, "no-release")
        result = await _get_one(org_id, config_id, 1)
        async with db_client.async_session() as session:
            charge = await session.get(RecurringChargeModel, result.recurring_charge_id)
            charge.first_failed_at = self.NOW - timedelta(days=4000)
            await session.commit()

        with pytest.raises(number_lifecycle.ReleaseNotPermitted) as refused:
            await number_lifecycle.release_number(
                phone_number_id=result.phone_number_id,
                actor_user_id=None,
                reason="unpaid",
                now=self.NOW,
            )

        assert "not released for unpaid rent" in str(refused.value)
        assert fake_provider.released == []
