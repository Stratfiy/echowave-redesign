"""The telephony gate: who may place a call on a managed number.

Two properties worth holding onto:

* An account may place calls on a platform-managed number only after the
  licensee approved it — not after our own staff did.
* A bring-your-own telephony configuration is never gated. Those numbers sit
  under the customer's own carrier account, verified in their own name; a
  self-hosted deployment must keep working exactly as before.

The carrier-poll tests that used to live here went with the poll itself. Plivo
posts a compliance callback on every status change, so what used to be tested
as "did we ask, and did we believe the answer" is now tested in
``test_kyc_carrier_callback.py`` as "did we verify who asked, and is a repeat
harmless".
"""

import pytest

from api.db import db_client
from api.db.models import (
    OrganizationModel,
    TelephonyConfigurationModel,
)
from api.enums import KycStatus
from api.services.kyc import service as kyc_service


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
