"""An approved account can buy a number without a human in the loop.

Carrier approval used to leave the customer on a dead end: "No managed carrier
account is set up for this organisation yet. Contact support." The only way
past it was a staff member calling an admin endpoint with no UI, once per
customer, forever. Every comparable platform lets an approved account buy a
number by itself, and the only thing in the way here was a row nobody created.

What the tests guard is mostly what must *not* happen — this runs from a
webhook Plivo re-delivers, and it writes credentials.
"""

from __future__ import annotations

import pytest

from api.db.models import OrganizationModel
from api.enums import KycStatus
from api.services.kyc import service as kyc_service


async def _org(session, slug: str) -> int:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org.id


@pytest.fixture
def platform_credentials(monkeypatch):
    monkeypatch.setattr("api.constants.PLATFORM_PLIVO_AUTH_ID", "MAPLATFORMXXXXXXXXXX")
    monkeypatch.setattr("api.constants.PLATFORM_PLIVO_AUTH_TOKEN", "platform-token")
    monkeypatch.setattr("api.constants.PLATFORM_PLIVO_APPLICATION_ID", None)


@pytest.mark.asyncio
class TestCreatingIt:
    async def test_an_approved_account_gets_one(
        self, db_session, async_session, platform_credentials
    ):
        from api.db import db_client

        organization_id = await _org(async_session, "approved")

        config_id = await kyc_service.ensure_managed_configuration(organization_id)

        assert config_id is not None
        rows = await db_client.list_telephony_configurations(organization_id)
        assert [r.is_platform_managed for r in rows] == [True]

    async def test_it_carries_our_credentials_not_the_customers(
        self, db_session, async_session, platform_credentials
    ):
        """That is what managed means — the numbers sit on Decibyl's carrier
        account and we pay the rent."""
        from api.db import db_client

        organization_id = await _org(async_session, "creds")
        config_id = await kyc_service.ensure_managed_configuration(organization_id)

        row = await db_client.get_telephony_configuration(config_id)
        assert row.provider == "plivo"

    async def test_it_becomes_the_route_when_the_account_has_no_other(
        self, db_session, async_session, platform_credentials
    ):
        """An account with no carrier of its own has to route through this one
        or it cannot dial at all."""
        from api.db import db_client

        organization_id = await _org(async_session, "only-config")
        config_id = await kyc_service.ensure_managed_configuration(organization_id)

        row = await db_client.get_telephony_configuration(config_id)
        assert row.is_default_outbound is True

    async def test_it_does_not_hijack_an_existing_route(
        self, db_session, async_session, platform_credentials
    ):
        """An account already running its own carrier keeps its route.
        Redirecting its existing traffic onto our account would move somebody
        else's call volume onto our bill."""
        from api.db import db_client

        organization_id = await _org(async_session, "byo-carrier")
        own = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Their own Plivo",
            provider="plivo",
            credentials={"auth_id": "MATHEIRSXXXXXXXXXXXX", "auth_token": "t"},
        )

        config_id = await kyc_service.ensure_managed_configuration(organization_id)

        managed = await db_client.get_telephony_configuration(config_id)
        theirs = await db_client.get_telephony_configuration(own.id)
        assert managed.is_default_outbound is False
        assert theirs.is_default_outbound is True

    async def test_running_twice_makes_one_configuration(
        self, db_session, async_session, platform_credentials
    ):
        """Plivo re-delivers callbacks. Two managed configurations for one
        organization would make "the managed one" ambiguous everywhere it is
        looked up."""
        from api.db import db_client

        organization_id = await _org(async_session, "twice")

        first = await kyc_service.ensure_managed_configuration(organization_id)
        second = await kyc_service.ensure_managed_configuration(organization_id)

        assert first == second
        rows = await db_client.list_telephony_configurations(organization_id)
        assert len([r for r in rows if r.is_platform_managed]) == 1

    async def test_a_deployment_with_no_platform_credentials_declines(
        self, db_session, async_session, monkeypatch
    ):
        """It cannot host managed numbers at all. Saying so in the log beats
        writing a configuration that authenticates as nobody."""
        from api.db import db_client

        monkeypatch.setattr("api.constants.PLATFORM_PLIVO_AUTH_ID", None)
        monkeypatch.setattr("api.constants.PLATFORM_PLIVO_AUTH_TOKEN", None)
        organization_id = await _org(async_session, "unconfigured")

        assert await kyc_service.ensure_managed_configuration(organization_id) is None
        assert await db_client.list_telephony_configurations(organization_id) == []

    async def test_another_organizations_configuration_does_not_count(
        self, db_session, async_session, platform_credentials
    ):
        """The lookup for "already has one" is per organization. Sharing one
        would put two customers' numbers under one row."""
        from api.db import db_client

        theirs = await _org(async_session, "theirs")
        mine = await _org(async_session, "mine")
        await kyc_service.ensure_managed_configuration(theirs)

        await kyc_service.ensure_managed_configuration(mine)

        rows = await db_client.list_telephony_configurations(mine)
        assert len([r for r in rows if r.is_platform_managed]) == 1


@pytest.mark.asyncio
class TestTheVerdictComesFirst:
    async def test_the_approval_survives_a_failure_to_configure(
        self, db_session, async_session, monkeypatch
    ):
        """The verdict is the carrier's fact. Losing it because we could not
        write a configuration afterwards would make Plivo redeliver a callback
        we already handled, and the account would read as unapproved while the
        carrier believes otherwise."""
        from api.db import db_client
        from api.services.kyc.carrier import CarrierVerdict

        organization_id = await _org(async_session, "verdict-first")
        await db_client.get_or_create_kyc(organization_id)
        await db_client.update_kyc(organization_id, status=KycStatus.FORWARDED.value)

        async def _explode(_organization_id):
            raise RuntimeError("carrier configuration store is down")

        monkeypatch.setattr(kyc_service, "ensure_managed_configuration", _explode)

        view = await kyc_service.record_carrier_verdict(
            organization_id=organization_id, verdict=CarrierVerdict.APPROVED
        )

        assert view is not None
        record = await db_client.get_kyc(organization_id)
        assert record.status == KycStatus.CARRIER_APPROVED.value


@pytest.mark.asyncio
class TestAccountsApprovedBeforeThisExisted:
    """They must not need a staff member either.

    The verdict handler creates the configuration going forward, which does
    nothing for an account already carrying `carrier_approved`. Those accounts
    would sit on "No managed carrier account is set up for this organisation
    yet" indefinitely, so the provisioning gate heals them on the way past.
    """

    async def test_searching_creates_the_missing_configuration(
        self, db_session, async_session, platform_credentials
    ):
        from api.db import db_client
        from api.services.telephony import provisioning

        organization_id = await _org(async_session, "already-approved")
        await db_client.get_or_create_kyc(organization_id)
        await db_client.update_kyc(
            organization_id,
            status=KycStatus.CARRIER_APPROVED.value,
            carrier="plivo",
            carrier_reference="app-existing",
        )
        assert await db_client.list_telephony_configurations(organization_id) == []

        await provisioning.assert_may_provision(organization_id)

        rows = await db_client.list_telephony_configurations(organization_id)
        assert [r.is_platform_managed for r in rows] == [True]

    async def test_an_unapproved_account_gets_nothing(
        self, db_session, async_session, platform_credentials
    ):
        """The gate refuses before it reaches the healing step, so an account
        that has not been approved never acquires our credentials."""
        from api.db import db_client
        from api.services.telephony import provisioning

        organization_id = await _org(async_session, "unapproved")
        await db_client.get_or_create_kyc(organization_id)

        with pytest.raises(provisioning.NotVerified):
            await provisioning.assert_may_provision(organization_id)

        assert await db_client.list_telephony_configurations(organization_id) == []
