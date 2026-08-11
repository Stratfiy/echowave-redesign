"""Provisioning and release: the two places a number can leak money.

Provisioning's dangerous window is between buying a number at the carrier and
recording it here. Anything that fails in that window must end with the number
released — a number we cannot record is a number we must not keep, because it
will be billed monthly against nothing.

Release's danger is the opposite: it is irreversible, the number may be printed
on the customer's signage, and it can be reissued to someone else. So the tests
below assert that the grace period is enforced rather than assumed, and that a
released row survives as the record we once held it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api.db import db_client
from api.db.models import (
    CreditLedgerModel,
    OrganizationKycModel,
    OrganizationModel,
    PaymentMandateModel,
    RecurringChargeModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    UserModel,
)
from api.enums import (
    CreditLedgerKind,
    KycStatus,
    MandateStatus,
    PhoneNumberStatus,
    RecurringChargeStatus,
)
from api.services.billing import rentals
from api.services.telephony import number_lifecycle, provisioning
from api.services.telephony.base import (
    AvailableNumber,
    NumberPurchaseResult,
    ProviderSyncResult,
)

NUMBER = "+918041234567"


class FakeProvider:
    """A carrier that does what it is told, and records what it was told."""

    PROVIDER_NAME = "plivo"

    def __init__(
        self,
        *,
        buy_ok=True,
        release_ok=True,
        owned=None,
        manages_numbers=True,
    ):
        self.buy_ok = buy_ok
        self.release_ok = release_ok
        self.owned = owned if owned is not None else []
        self.manages_numbers = manages_numbers
        self.auth_id = "MA123"
        self.auth_token = "token"
        self.bought = []
        self.released = []
        self.app_ids = []

    def supports_number_management(self):
        return self.manages_numbers

    async def search_available_numbers(self, **kwargs):
        return [
            AvailableNumber(
                number=NUMBER,
                country_iso="IN",
                number_type="local",
                city="Bangalore",
                monthly_rental="2.50",
            )
        ]

    async def buy_number(self, number, *, app_id=None, idempotency_key=None):
        self.bought.append(number)
        self.app_ids.append(app_id)
        if not self.buy_ok:
            return NumberPurchaseResult(
                ok=False, number=number, message="no inventory"
            )
        return NumberPurchaseResult(
            ok=True, number=number, carrier_number_id="cn-999"
        )

    async def release_number(self, number):
        self.released.append(number)
        return ProviderSyncResult(ok=self.release_ok, message=None if self.release_ok else "carrier said no")

    async def list_owned_numbers(self):
        return list(self.owned)


class FakeComplianceClient:
    def __init__(self, *, link_ok=True):
        self.link_ok = link_ok
        self.linked = []

    async def link(self, *, number, compliance_application_id):
        if not self.link_ok:
            raise RuntimeError("compliance link refused")
        self.linked.append((number, compliance_application_id))
        return {"status": "ok"}


@pytest.fixture
def fake_provider(monkeypatch):
    provider = FakeProvider()

    async def _get(config_id, organization_id):
        return provider

    monkeypatch.setattr(provisioning, "_provider", _get)

    async def _get_lifecycle(config_id, organization_id):
        return provider

    monkeypatch.setattr(
        "api.services.telephony.factory.get_telephony_provider_by_id", _get_lifecycle
    )
    return provider


async def _approved_org(
    session, slug: str, *, status=KycStatus.CARRIER_APPROVED, autopay=True
):
    """An account that may buy a number: verified, and with autopay authorised.

    ``autopay`` is a parameter rather than always-on because the two gates are
    independent — an account can be approved by the carrier and still have no
    standing instruction behind the rent, which is its own refusal.
    """
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    session.add(
        OrganizationKycModel(
            organization_id=org.id,
            status=status.value,
            carrier="plivo",
            carrier_reference="app-approved",
        )
    )
    if autopay:
        session.add(
            PaymentMandateModel(
                organization_id=org.id,
                provider="razorpay",
                purpose="number_rental",
                subscription_id=f"sub-{slug}",
                plan_id="plan-rental",
                status=MandateStatus.ACTIVE.value,
                price_paise=34900,
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
    return org.id, user.id, config.id


class TestAutopayGate:
    """The second gate, and the one that decides whether the rent gets paid.

    Verification says the carrier will sell us a number for this customer.
    Autopay says the customer has authorised us to collect for it every month.
    A number issued on the first without the second is a number we pay a
    carrier for and cannot collect on.
    """

    async def test_an_account_with_no_mandate_cannot_buy(
        self, db_session, async_session, fake_provider
    ):
        from api.services.billing.mandates import MandateNotAuthorised

        org_id, _, config_id = await _approved_org(
            async_session, "no-mandate", autopay=False
        )

        with pytest.raises(MandateNotAuthorised):
            await provisioning.provision(
                organization_id=org_id,
                telephony_configuration_id=config_id,
                address=NUMBER,
            )
        # Nothing was bought. The gate is before the purchase, so there is no
        # compensating release to get wrong.
        assert fake_provider.bought == []

    async def test_searching_does_not_need_a_mandate(
        self, db_session, async_session, fake_provider
    ):
        """The flow is documents -> approved -> search -> select -> mandate ->
        number. Asking someone to authorise a standing instruction before they
        have seen what is on offer is the wrong order."""
        org_id, _, config_id = await _approved_org(
            async_session, "search-no-mandate", autopay=False
        )

        found = await provisioning.search(
            organization_id=org_id, telephony_configuration_id=config_id
        )
        assert found

    async def test_the_rental_records_which_mandate_backs_it(
        self, db_session, async_session, fake_provider
    ):
        org_id, _, config_id = await _approved_org(async_session, "mandate-linked")

        result = await provisioning.provision(
            organization_id=org_id,
            telephony_configuration_id=config_id,
            address=NUMBER,
            compliance_client=FakeComplianceClient(),
        )

        async with db_client.async_session() as session:
            charge = await session.get(
                RecurringChargeModel, result.recurring_charge_id
            )
            mandate = await session.get(PaymentMandateModel, charge.mandate_id)
        assert mandate is not None
        assert mandate.organization_id == org_id


class TestVerificationGate:
    async def test_an_unverified_account_cannot_buy(
        self, db_session, async_session, fake_provider
    ):
        org_id, _, config_id = await _approved_org(
            async_session, "unver", status=KycStatus.SUBMITTED
        )

        with pytest.raises(provisioning.NotVerified):
            await provisioning.provision(
                organization_id=org_id,
                telephony_configuration_id=config_id,
                address=NUMBER,
            )
        assert fake_provider.bought == []

    async def test_a_suspended_account_cannot_buy(
        self, db_session, async_session, fake_provider
    ):
        org_id, _, config_id = await _approved_org(
            async_session, "susp-buy", status=KycStatus.SUSPENDED
        )
        with pytest.raises(provisioning.NotVerified):
            await provisioning.provision(
                organization_id=org_id,
                telephony_configuration_id=config_id,
                address=NUMBER,
            )

    async def test_search_is_gated_too(
        self, db_session, async_session, fake_provider
    ):
        """Showing a list they cannot buy from is worse than saying why."""
        org_id, _, config_id = await _approved_org(
            async_session, "search-gate", status=KycStatus.NOT_STARTED
        )
        with pytest.raises(provisioning.NotVerified):
            await provisioning.search(
                organization_id=org_id, telephony_configuration_id=config_id
            )


class TestHappyPath:
    async def test_provisioning_buys_links_records_and_opens_a_rental(
        self, db_session, async_session, fake_provider, monkeypatch
    ):
        org_id, _, config_id = await _approved_org(async_session, "happy")
        monkeypatch.setattr(
            provisioning, "PLATFORM_PLIVO_APPLICATION_ID", "app-inbound"
        )
        client = FakeComplianceClient()

        result = await provisioning.provision(
            organization_id=org_id,
            telephony_configuration_id=config_id,
            address=NUMBER,
            compliance_client=client,
        )

        assert fake_provider.bought == [NUMBER]
        # Bound to our application at purchase, so there is no console step and
        # no window where the number is rented but answers nowhere.
        assert fake_provider.app_ids == ["app-inbound"]
        assert client.linked == [(NUMBER.lstrip("+"), "app-approved")]

        async with db_client.async_session() as session:
            number = await session.get(TelephonyPhoneNumberModel, result.phone_number_id)
            charge = await session.get(
                RecurringChargeModel, result.recurring_charge_id
            )
        assert number.status == PhoneNumberStatus.ACTIVE.value
        assert number.carrier_number_id == "cn-999"
        assert number.is_active is True
        assert number.provisioned_at is not None
        assert charge.status == RecurringChargeStatus.ACTIVE.value


class TestCompensatingRelease:
    async def test_a_failed_link_releases_the_number_we_just_bought(
        self, db_session, async_session, fake_provider
    ):
        """The window this whole module is shaped around."""
        org_id, _, config_id = await _approved_org(async_session, "linkfail")

        with pytest.raises(provisioning.ProvisioningError):
            await provisioning.provision(
                organization_id=org_id,
                telephony_configuration_id=config_id,
                address=NUMBER,
                compliance_client=FakeComplianceClient(link_ok=False),
            )

        assert fake_provider.bought == [NUMBER]
        assert fake_provider.released == [NUMBER]

    async def test_nothing_is_left_behind_in_the_database(
        self, db_session, async_session, fake_provider
    ):
        org_id, _, config_id = await _approved_org(async_session, "nodangle")

        with pytest.raises(provisioning.ProvisioningError):
            await provisioning.provision(
                organization_id=org_id,
                telephony_configuration_id=config_id,
                address=NUMBER,
                compliance_client=FakeComplianceClient(link_ok=False),
            )

        async with db_client.async_session() as session:
            rows = list(
                (
                    await session.scalars(
                        select(TelephonyPhoneNumberModel).where(
                            TelephonyPhoneNumberModel.organization_id == org_id
                        )
                    )
                ).all()
            )
        assert rows == []

    async def test_no_rental_is_opened_for_a_failed_provisioning(
        self, db_session, async_session, fake_provider
    ):
        """Otherwise we bill a customer monthly for a number they never got."""
        org_id, _, config_id = await _approved_org(async_session, "norental")

        with pytest.raises(provisioning.ProvisioningError):
            await provisioning.provision(
                organization_id=org_id,
                telephony_configuration_id=config_id,
                address=NUMBER,
                compliance_client=FakeComplianceClient(link_ok=False),
            )

        async with db_client.async_session() as session:
            charges = list(
                (
                    await session.scalars(
                        select(RecurringChargeModel).where(
                            RecurringChargeModel.organization_id == org_id
                        )
                    )
                ).all()
            )
        assert charges == []

    async def test_a_refused_purchase_releases_nothing(
        self, db_session, async_session, monkeypatch
    ):
        """There is nothing to compensate for if the carrier never sold it."""
        provider = FakeProvider(buy_ok=False)

        async def _get(config_id, organization_id):
            return provider

        monkeypatch.setattr(provisioning, "_provider", _get)
        org_id, _, config_id = await _approved_org(async_session, "buyfail")

        with pytest.raises(provisioning.ProvisioningError):
            await provisioning.provision(
                organization_id=org_id,
                telephony_configuration_id=config_id,
                address=NUMBER,
                compliance_client=FakeComplianceClient(),
            )
        assert provider.released == []

    async def test_a_carrier_that_cannot_sell_numbers_is_refused_up_front(
        self, db_session, async_session, monkeypatch
    ):
        provider = FakeProvider(manages_numbers=False)

        async def _get(config_id, organization_id):
            return provider

        monkeypatch.setattr(provisioning, "_provider", _get)
        org_id, _, config_id = await _approved_org(async_session, "unmanaged")

        with pytest.raises(provisioning.ProvisioningError):
            await provisioning.provision(
                organization_id=org_id,
                telephony_configuration_id=config_id,
                address=NUMBER,
            )
        assert provider.bought == []


class TestRelease:
    async def _provisioned(self, session, slug, fake_provider):
        org_id, user_id, config_id = await _approved_org(session, slug)
        result = await provisioning.provision(
            organization_id=org_id,
            telephony_configuration_id=config_id,
            address=NUMBER,
            compliance_client=FakeComplianceClient(),
        )
        return org_id, user_id, result.phone_number_id

    async def test_release_before_the_grace_period_is_refused(
        self, db_session, async_session, fake_provider
    ):
        org_id, user_id, number_id = await self._provisioned(
            async_session, "early", fake_provider
        )

        with pytest.raises(number_lifecycle.ReleaseNotPermitted):
            await number_lifecycle.release_number(
                phone_number_id=number_id,
                actor_user_id=user_id,
                reason="cleaning up",
            )
        assert fake_provider.released == []

    async def test_release_on_a_first_failure_is_refused(
        self, db_session, async_session, fake_provider
    ):
        """The specific mistake the policy exists to prevent."""
        org_id, user_id, number_id = await self._provisioned(
            async_session, "firstfail", fake_provider
        )
        async with db_client.async_session() as session:
            charge = await session.scalar(
                select(RecurringChargeModel).where(
                    RecurringChargeModel.resource_id == number_id
                )
            )
            charge.first_failed_at = datetime.now(UTC)
            charge.status = RecurringChargeStatus.PAST_DUE.value
            await session.commit()

        with pytest.raises(number_lifecycle.ReleaseNotPermitted):
            await number_lifecycle.release_number(
                phone_number_id=number_id,
                actor_user_id=user_id,
                reason="unpaid",
            )

    async def test_release_at_day_forty_four_is_still_refused(
        self, db_session, async_session, fake_provider
    ):
        org_id, user_id, number_id = await self._provisioned(
            async_session, "day44", fake_provider
        )
        async with db_client.async_session() as session:
            charge = await session.scalar(
                select(RecurringChargeModel).where(
                    RecurringChargeModel.resource_id == number_id
                )
            )
            charge.first_failed_at = datetime.now(UTC) - timedelta(days=44)
            await session.commit()

        with pytest.raises(number_lifecycle.ReleaseNotPermitted):
            await number_lifecycle.release_number(
                phone_number_id=number_id,
                actor_user_id=user_id,
                reason="unpaid 44 days",
            )

    async def test_release_after_the_grace_period_succeeds_and_soft_deletes(
        self, db_session, async_session, fake_provider
    ):
        org_id, user_id, number_id = await self._provisioned(
            async_session, "day46", fake_provider
        )
        async with db_client.async_session() as session:
            charge = await session.scalar(
                select(RecurringChargeModel).where(
                    RecurringChargeModel.resource_id == number_id
                )
            )
            charge.first_failed_at = datetime.now(UTC) - timedelta(days=46)
            await session.commit()

        await number_lifecycle.release_number(
            phone_number_id=number_id,
            actor_user_id=user_id,
            reason="unpaid for 46 days",
        )

        assert fake_provider.released == [NUMBER]
        async with db_client.async_session() as session:
            number = await session.get(TelephonyPhoneNumberModel, number_id)
            charge = await session.scalar(
                select(RecurringChargeModel).where(
                    RecurringChargeModel.resource_id == number_id
                )
            )
        # The row survives. It is the only record we ever held this number.
        assert number is not None
        assert number.status == PhoneNumberStatus.RELEASED.value
        assert number.released_by == user_id
        assert number.release_reason == "unpaid for 46 days"
        assert number.inbound_workflow_id is None
        assert charge.status == RecurringChargeStatus.RELEASED.value
        assert charge.next_charge_at is None

    async def test_a_forced_release_still_needs_a_reason(
        self, db_session, async_session, fake_provider
    ):
        org_id, user_id, number_id = await self._provisioned(
            async_session, "forced-noreason", fake_provider
        )
        with pytest.raises(ValueError):
            await number_lifecycle.release_number(
                phone_number_id=number_id,
                actor_user_id=user_id,
                reason="   ",
                force=True,
            )

    async def test_a_forced_release_skips_only_the_grace_period(
        self, db_session, async_session, fake_provider
    ):
        """A customer giving up a number they have fully paid for."""
        org_id, user_id, number_id = await self._provisioned(
            async_session, "forced", fake_provider
        )
        await number_lifecycle.release_number(
            phone_number_id=number_id,
            actor_user_id=user_id,
            reason="customer asked to give it up",
            force=True,
        )
        assert fake_provider.released == [NUMBER]

    async def test_a_failed_carrier_release_records_nothing(
        self, db_session, async_session, monkeypatch
    ):
        """A row saying released while the carrier still bills us is worse than
        a failed release, because it stops anyone looking."""
        provider = FakeProvider(release_ok=False)

        async def _get(config_id, organization_id):
            return provider

        monkeypatch.setattr(provisioning, "_provider", _get)
        monkeypatch.setattr(
            "api.services.telephony.factory.get_telephony_provider_by_id", _get
        )

        org_id, user_id, config_id = await _approved_org(async_session, "relfail")
        result = await provisioning.provision(
            organization_id=org_id,
            telephony_configuration_id=config_id,
            address=NUMBER,
            compliance_client=FakeComplianceClient(),
        )

        with pytest.raises(RuntimeError):
            await number_lifecycle.release_number(
                phone_number_id=result.phone_number_id,
                actor_user_id=user_id,
                reason="giving up",
                force=True,
            )

        async with db_client.async_session() as session:
            number = await session.get(
                TelephonyPhoneNumberModel, result.phone_number_id
            )
        assert number.status == PhoneNumberStatus.ACTIVE.value
        assert number.released_at is None

    async def test_releasing_twice_is_harmless(
        self, db_session, async_session, fake_provider
    ):
        org_id, user_id, number_id = await self._provisioned(
            async_session, "twice", fake_provider
        )
        for _ in range(2):
            await number_lifecycle.release_number(
                phone_number_id=number_id,
                actor_user_id=user_id,
                reason="customer request",
                force=True,
            )
        # Released once at the carrier, not twice.
        assert fake_provider.released == [NUMBER]


class TestServeGate:
    def test_an_active_number_serves(self):
        class N:
            status = PhoneNumberStatus.ACTIVE.value

        number_lifecycle.assert_number_may_serve(N())

    def test_a_suspended_number_is_refused_with_a_recoverable_message(self):
        class N:
            status = PhoneNumberStatus.SUSPENDED.value

        with pytest.raises(number_lifecycle.NumberSuspended) as exc:
            number_lifecycle.assert_number_may_serve(N())
        assert "still yours" in str(exc.value)
        assert exc.value.status == PhoneNumberStatus.SUSPENDED.value

    def test_a_released_number_is_refused_without_false_hope(self):
        class N:
            status = PhoneNumberStatus.RELEASED.value

        with pytest.raises(number_lifecycle.NumberSuspended) as exc:
            number_lifecycle.assert_number_may_serve(N())
        assert "no longer" in str(exc.value)

    def test_a_number_with_no_status_still_serves(self):
        """Rows predating the column must not take a working platform down."""

        class N:
            status = None

        number_lifecycle.assert_number_may_serve(N())


class TestReconciliation:
    async def test_a_number_at_the_carrier_we_do_not_know_about_is_reported(
        self, db_session, async_session, monkeypatch
    ):
        """Rent we are paying with nothing to bill it against."""
        provider = FakeProvider(owned=["+918099999999"])

        async def _get(config_id, organization_id):
            return provider

        monkeypatch.setattr(
            "api.services.telephony.factory.get_telephony_provider_by_id", _get
        )
        _, _, config_id = await _approved_org(async_session, "recon-orphan")

        async with db_client.async_session() as session:
            config = await session.get(TelephonyConfigurationModel, config_id)
            report = await number_lifecycle.reconcile_configuration(config)

        assert report.at_carrier_only == ["+918099999999"]
        assert not report.clean

    async def test_a_number_we_hold_that_the_carrier_lost_is_reported(
        self, db_session, async_session, fake_provider
    ):
        """Inbound will fail for a customer who is paying."""
        org_id, _, number_id = await TestRelease()._provisioned(
            async_session, "recon-missing", fake_provider
        )
        fake_provider.owned = []  # carrier says it has nothing

        async with db_client.async_session() as session:
            number = await session.get(TelephonyPhoneNumberModel, number_id)
            config = await session.get(
                TelephonyConfigurationModel, number.telephony_configuration_id
            )
            report = await number_lifecycle.reconcile_configuration(config)

        assert report.in_database_only == [NUMBER]

    async def test_an_agreeing_inventory_is_clean(
        self, db_session, async_session, fake_provider
    ):
        org_id, _, number_id = await TestRelease()._provisioned(
            async_session, "recon-clean", fake_provider
        )
        fake_provider.owned = [NUMBER]

        async with db_client.async_session() as session:
            number = await session.get(TelephonyPhoneNumberModel, number_id)
            config = await session.get(
                TelephonyConfigurationModel, number.telephony_configuration_id
            )
            report = await number_lifecycle.reconcile_configuration(config)

        assert report.clean

    async def test_an_unreachable_carrier_reports_an_error_not_an_empty_inventory(
        self, db_session, async_session, monkeypatch
    ):
        """Otherwise every number we hold looks missing, and that false alarm
        is what trains people to ignore this job."""

        class Broken(FakeProvider):
            async def list_owned_numbers(self):
                raise RuntimeError("carrier unreachable")

        provider = Broken()

        async def _get(config_id, organization_id):
            return provider

        monkeypatch.setattr(
            "api.services.telephony.factory.get_telephony_provider_by_id", _get
        )
        _, _, config_id = await _approved_org(async_session, "recon-down")

        async with db_client.async_session() as session:
            config = await session.get(TelephonyConfigurationModel, config_id)
            report = await number_lifecycle.reconcile_configuration(config)

        assert report.error is not None
        assert report.in_database_only == []
        assert not report.clean

    async def test_a_released_number_is_not_reported_as_missing(
        self, db_session, async_session, fake_provider
    ):
        org_id, user_id, number_id = await TestRelease()._provisioned(
            async_session, "recon-released", fake_provider
        )
        await number_lifecycle.release_number(
            phone_number_id=number_id,
            actor_user_id=user_id,
            reason="customer request",
            force=True,
        )
        fake_provider.owned = []

        async with db_client.async_session() as session:
            number = await session.get(TelephonyPhoneNumberModel, number_id)
            config = await session.get(
                TelephonyConfigurationModel, number.telephony_configuration_id
            )
            report = await number_lifecycle.reconcile_configuration(config)

        assert report.clean
