"""The telephony gate, and the carrier status poll that opens it.

Two properties worth holding onto:

* An account may place calls on a platform-managed number only after the
  licensee approved it — not after our own staff did.
* A bring-your-own telephony configuration is never gated. Those numbers sit
  under the customer's own carrier account, verified in their own name; a
  self-hosted deployment must keep working exactly as before.
"""

import pytest

from api.db import db_client
from api.db.models import (
    OrganizationModel,
    TelephonyConfigurationModel,
    UserModel,
)
from api.enums import KycStatus
from api.services.kyc import service as kyc_service
from api.services.kyc.carrier import CarrierStatus, CarrierVerdict
from api.tasks.kyc_carrier_poll import poll_kyc_carrier_status


async def _org(session, slug: str) -> int:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org.id


async def _config(session, organization_id: int, *, managed: bool):
    configuration = TelephonyConfigurationModel(
        organization_id=organization_id,
        name="managed" if managed else "byo",
        provider="plivo",
        credentials={},
        is_platform_managed=managed,
    )
    session.add(configuration)
    await session.flush()
    return configuration


async def _at_status(session, slug: str, status: KycStatus) -> int:
    org_id = await _org(session, slug)
    await db_client.get_or_create_kyc(org_id)
    await db_client.update_kyc(org_id, status=status.value)
    return org_id


class TestTheGate:
    async def test_an_unverified_account_cannot_use_a_managed_number(
        self, db_session, async_session
    ):
        org_id = await _org(async_session, "unverified")
        configuration = await _config(async_session, org_id, managed=True)

        with pytest.raises(kyc_service.TelephonyNotVerified) as raised:
            await kyc_service.assert_configuration_may_place_calls(configuration)
        assert raised.value.status == KycStatus.NOT_STARTED.value

    async def test_our_own_approval_is_not_enough(self, db_session, async_session):
        """The point of the whole two-stage machine, asserted at the gate rather
        than only at the state machine."""
        org_id = await _at_status(async_session, "forwarded", KycStatus.FORWARDED)
        configuration = await _config(async_session, org_id, managed=True)

        with pytest.raises(kyc_service.TelephonyNotVerified) as raised:
            await kyc_service.assert_configuration_may_place_calls(configuration)
        assert raised.value.status == KycStatus.FORWARDED.value
        assert "telecom operator" in str(raised.value)

    async def test_carrier_approval_opens_the_gate(self, db_session, async_session):
        org_id = await _at_status(async_session, "ok", KycStatus.CARRIER_APPROVED)
        configuration = await _config(async_session, org_id, managed=True)

        await kyc_service.assert_configuration_may_place_calls(configuration)

    async def test_bring_your_own_telephony_is_never_gated(
        self, db_session, async_session
    ):
        """Those numbers are on the customer's own carrier account, already
        verified in their name. Gating them would break every self-hosted
        deployment, none of which has a KYC record at all."""
        org_id = await _org(async_session, "byo")
        configuration = await _config(async_session, org_id, managed=False)

        await kyc_service.assert_configuration_may_place_calls(configuration)

    async def test_no_configuration_is_not_a_gate_decision(
        self, db_session, async_session
    ):
        """Callers pass whatever they resolved. A missing config fails later on
        its own terms — reporting it as unverified would be a wrong reason."""
        await kyc_service.assert_configuration_may_place_calls(None)

    @pytest.mark.parametrize(
        "status",
        [
            KycStatus.NOT_STARTED,
            KycStatus.SUBMITTED,
            KycStatus.UNDER_REVIEW,
            KycStatus.REJECTED,
            KycStatus.FORWARDED,
            KycStatus.CARRIER_REJECTED,
        ],
    )
    async def test_every_status_short_of_carrier_approval_is_blocked(
        self, db_session, async_session, status
    ):
        org_id = await _at_status(async_session, f"s-{status.value}", status)

        with pytest.raises(kyc_service.TelephonyNotVerified) as raised:
            await kyc_service.assert_may_place_calls(org_id)
        # And says something the customer can act on, not just "denied".
        assert len(str(raised.value)) > 20


class TestCarrierPoll:
    async def test_an_approval_from_the_carrier_opens_telephony(
        self, db_session, async_session, monkeypatch
    ):
        org_id = await _at_status(async_session, "poll-ok", KycStatus.FORWARDED)
        await db_client.update_kyc(org_id, carrier="fake", carrier_reference="app-1")

        class FakeCarrier:
            name = "fake"
            pollable = True

            async def check(self, reference):
                assert reference == "app-1"
                return CarrierStatus(
                    verdict=CarrierVerdict.APPROVED, raw_status="approved"
                )

        monkeypatch.setitem(
            kyc_service.get_carrier.__globals__["_CARRIERS"], "fake", FakeCarrier()
        )

        await poll_kyc_carrier_status(None)

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.CARRIER_APPROVED.value
        assert record.carrier_approved_at is not None

    async def test_a_pending_application_stays_put(
        self, db_session, async_session, monkeypatch
    ):
        org_id = await _at_status(async_session, "poll-wait", KycStatus.FORWARDED)
        await db_client.update_kyc(org_id, carrier="fake", carrier_reference="app-2")

        class FakeCarrier:
            name = "fake"
            pollable = True

            async def check(self, reference):
                return CarrierStatus(
                    verdict=CarrierVerdict.PENDING, raw_status="in_review"
                )

        monkeypatch.setitem(
            kyc_service.get_carrier.__globals__["_CARRIERS"], "fake", FakeCarrier()
        )

        await poll_kyc_carrier_status(None)

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.FORWARDED.value
        assert record.carrier_status == "in_review"
        assert record.carrier_checked_at is not None

    async def test_one_carrier_failing_does_not_strand_the_others(
        self, db_session, async_session, monkeypatch
    ):
        """A compliance API returning 500 for one application is not a reason to
        leave every other customer waiting until the next tick."""
        broken = await _at_status(async_session, "poll-broken", KycStatus.FORWARDED)
        await db_client.update_kyc(broken, carrier="broken", carrier_reference="app-3")
        healthy = await _at_status(async_session, "poll-healthy", KycStatus.FORWARDED)
        await db_client.update_kyc(healthy, carrier="fake", carrier_reference="app-4")

        class BrokenCarrier:
            name = "broken"
            pollable = True

            async def check(self, reference):
                raise RuntimeError("compliance API is down")

        class FakeCarrier:
            name = "fake"
            pollable = True

            async def check(self, reference):
                return CarrierStatus(verdict=CarrierVerdict.APPROVED)

        carriers = kyc_service.get_carrier.__globals__["_CARRIERS"]
        monkeypatch.setitem(carriers, "broken", BrokenCarrier())
        monkeypatch.setitem(carriers, "fake", FakeCarrier())

        await poll_kyc_carrier_status(None)

        assert (await db_client.get_kyc(broken)).status == KycStatus.FORWARDED.value
        assert (
            await db_client.get_kyc(healthy)
        ).status == KycStatus.CARRIER_APPROVED.value

    async def test_a_manually_forwarded_application_is_left_alone(
        self, db_session, async_session
    ):
        """Refreshing carrier_checked_at on a schedule would imply we asked the
        licensee when nobody did."""
        org_id = await _at_status(async_session, "poll-manual", KycStatus.FORWARDED)
        await db_client.update_kyc(
            org_id, carrier="manual", carrier_reference=f"manual:{org_id}"
        )

        await poll_kyc_carrier_status(None)

        record = await db_client.get_kyc(org_id)
        assert record.status == KycStatus.FORWARDED.value
        assert record.carrier_checked_at is None

    async def test_an_application_we_never_forwarded_is_not_polled(
        self, db_session, async_session, monkeypatch
    ):
        """Only records with a carrier reference are outstanding; polling a
        submitted one would be asking about an application nobody has."""
        org_id = await _at_status(async_session, "poll-submitted", KycStatus.SUBMITTED)

        calls: list[str] = []

        class FakeCarrier:
            name = "fake"
            pollable = True

            async def check(self, reference):
                calls.append(reference)
                return CarrierStatus(verdict=CarrierVerdict.APPROVED)

        monkeypatch.setitem(
            kyc_service.get_carrier.__globals__["_CARRIERS"], "fake", FakeCarrier()
        )

        await poll_kyc_carrier_status(None)

        assert calls == []
        assert (await db_client.get_kyc(org_id)).status == KycStatus.SUBMITTED.value
