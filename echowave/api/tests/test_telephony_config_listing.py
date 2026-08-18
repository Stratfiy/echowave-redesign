"""What `GET /telephony-configs` says about each configuration.

One field on this response decides whether a customer can buy a phone number:
`is_platform_managed`. The purchase screen filters on it, and offers a search
only when something comes back managed.

It carried a default of `False` in the schema and the route never passed it, so
every configuration on every account was reported as unmanaged. The database
was right the whole time; the wire was not. A fully approved customer sat on
"No managed carrier account is set up for this organisation yet. Contact
support" with a correctly flagged row in the table behind it.

These tests exist because the failure was invisible from every direction —
correct data, no error, no log line, a screen that read like a configuration
problem.
"""

from __future__ import annotations

import pytest

from api.db.models import OrganizationModel


async def _org(session, slug: str) -> int:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org.id


@pytest.mark.asyncio
class TestTheManagedFlagSurvivesTheResponse:
    async def test_a_managed_configuration_is_reported_as_managed(
        self, db_session, async_session
    ):
        from api.db import db_client
        from api.routes import organization as organization_routes

        organization_id = await _org(async_session, "managed")
        row = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Decibyl managed",
            provider="plivo",
            credentials={"auth_id": "MAXXXXXXXXXXXXXXXXXX", "auth_token": "t"},
        )
        await db_client.set_platform_managed(row.id, managed=True)

        response = await organization_routes.list_telephony_configurations(
            user=type("U", (), {"selected_organization_id": organization_id})()
        )

        assert [c.is_platform_managed for c in response.configurations] == [True]

    async def test_a_customers_own_configuration_is_not(
        self, db_session, async_session
    ):
        """The flag has to distinguish, not just be present. Reporting every
        configuration as managed would offer a purchase the provisioning route
        refuses."""
        from api.db import db_client
        from api.routes import organization as organization_routes

        organization_id = await _org(async_session, "byo")
        await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Their own Plivo",
            provider="plivo",
            credentials={"auth_id": "MATHEIRSXXXXXXXXXXXX", "auth_token": "t"},
        )

        response = await organization_routes.list_telephony_configurations(
            user=type("U", (), {"selected_organization_id": organization_id})()
        )

        assert [c.is_platform_managed for c in response.configurations] == [False]

    async def test_both_kinds_are_told_apart_in_one_listing(
        self, db_session, async_session
    ):
        """The shape the buy screen actually sees on an account that runs its
        own carrier and rents a managed number alongside it."""
        from api.db import db_client
        from api.routes import organization as organization_routes

        organization_id = await _org(async_session, "both")
        await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Their own Plivo",
            provider="plivo",
            credentials={"auth_id": "MATHEIRSXXXXXXXXXXXX", "auth_token": "t"},
        )
        managed = await db_client.create_telephony_configuration(
            organization_id=organization_id,
            name="Decibyl managed",
            provider="plivo",
            credentials={"auth_id": "MAOURSXXXXXXXXXXXXXX", "auth_token": "t"},
        )
        await db_client.set_platform_managed(managed.id, managed=True)

        response = await organization_routes.list_telephony_configurations(
            user=type("U", (), {"selected_organization_id": organization_id})()
        )

        by_name = {c.name: c.is_platform_managed for c in response.configurations}
        assert by_name == {"Their own Plivo": False, "Decibyl managed": True}


def test_the_field_has_no_default():
    """The default is what hid this. A field deciding whether a customer can
    spend money must fail loudly when a caller forgets it, not quietly answer
    'no'."""
    from api.schemas.telephony_config import TelephonyConfigurationListItem

    assert TelephonyConfigurationListItem.model_fields[
        "is_platform_managed"
    ].is_required()
