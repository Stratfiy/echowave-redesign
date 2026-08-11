"""The staff switch that puts an organization on the managed-number path.

Two properties matter here and they pull in opposite directions.

`is_platform_managed` must be **settable** — until this route existed the flag
had a writer and no caller, so provisioning, rental billing and the KYC gate
were all built and all unreachable.

It must also not be **clearable while numbers are attached**. The flag is what
marks a number as ours to bill and ours to release; clearing it with rentals
outstanding leaves us paying a carrier with nothing pointing at the money.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.db import db_client
from api.db.models import (
    OrganizationModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
    UserModel,
)
from api.enums import PhoneNumberStatus, StaffRole


async def _org_and_config(session, slug: str, *, managed: bool = False):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    config = TelephonyConfigurationModel(
        organization_id=org.id,
        name=f"cfg-{slug}",
        provider="plivo",
        credentials={"auth_id": "MA", "auth_token": "t"},
        is_platform_managed=managed,
    )
    session.add(config)
    await session.flush()
    return org, config


async def _staff(session, slug: str) -> UserModel:
    # SUPERADMIN rather than SUPPORT: this router can put an organization on
    # the managed path, which is a decision about who we bill and who we owe a
    # carrier — the same tier the billing surfaces sit behind.
    user = UserModel(
        provider_id=f"staff-{slug}", staff_role=StaffRole.SUPERADMIN.value
    )
    session.add(user)
    await session.flush()
    return user


async def _number(session, org_id, config_id, address, *, carrier_id="cn-1", status=None):
    row = TelephonyPhoneNumberModel(
        organization_id=org_id,
        telephony_configuration_id=config_id,
        address=address,
        address_normalized=address,
        address_type="phone",
        country_code="IN",
        carrier_number_id=carrier_id,
        status=status or PhoneNumberStatus.ACTIVE.value,
    )
    session.add(row)
    await session.flush()
    return row


def _client(user):
    """An HTTP client authenticated as ``user`` for the superuser gate."""
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


class TestTheGate:
    async def test_the_router_is_unreachable_without_superuser(
        self, db_session, async_session
    ):
        """Marking a configuration managed claims our carrier account and our
        compliance application. It is not a customer's call to make."""
        from api.app import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.put(
                "/api/v1/admin/telephony/configurations/1/platform-managed",
                json={"managed": True},
            )
        assert response.status_code in (401, 403)


class TestSettingTheFlag:
    async def test_a_configuration_can_be_put_on_the_managed_path(
        self, db_session, async_session
    ):
        org, config = await _org_and_config(async_session, "set")
        staff = await _staff(async_session, "s1")

        async with _client(staff) as client:
            response = await client.put(
                f"/api/v1/admin/telephony/configurations/{config.id}/platform-managed",
                json={"managed": True},
            )

        assert response.status_code == 200
        assert response.json()["is_platform_managed"] is True

        stored = await db_client.get_telephony_configuration(config.id)
        assert stored.is_platform_managed is True

    async def test_the_kyc_gate_becomes_live_once_the_flag_is_set(
        self, db_session, async_session
    ):
        """The whole point of the flag. Before it is set the gate never fires;
        after, an unverified account cannot place a call."""
        from api.services.kyc import service as kyc_service

        org, config = await _org_and_config(async_session, "gate")
        staff = await _staff(async_session, "s-gate")

        # Not managed: a bring-your-own configuration is never gated.
        await kyc_service.assert_configuration_may_place_calls(config)

        async with _client(staff) as client:
            await client.put(
                f"/api/v1/admin/telephony/configurations/{config.id}/platform-managed",
                json={"managed": True},
            )

        managed = await db_client.get_telephony_configuration(config.id)
        with pytest.raises(kyc_service.TelephonyNotVerified):
            await kyc_service.assert_configuration_may_place_calls(managed)

    async def test_an_unknown_configuration_is_a_404(
        self, db_session, async_session
    ):
        staff = await _staff(async_session, "s404")
        async with _client(staff) as client:
            response = await client.put(
                "/api/v1/admin/telephony/configurations/99999999/platform-managed",
                json={"managed": True},
            )
        assert response.status_code == 404

    async def test_setting_it_twice_is_harmless(self, db_session, async_session):
        org, config = await _org_and_config(async_session, "twice")
        staff = await _staff(async_session, "s-twice")

        async with _client(staff) as client:
            for _ in range(2):
                response = await client.put(
                    f"/api/v1/admin/telephony/configurations/{config.id}"
                    "/platform-managed",
                    json={"managed": True},
                )
                assert response.status_code == 200


class TestClearingTheFlag:
    async def test_it_can_be_cleared_when_no_numbers_are_attached(
        self, db_session, async_session
    ):
        org, config = await _org_and_config(async_session, "clear", managed=True)
        staff = await _staff(async_session, "s-clear")

        async with _client(staff) as client:
            response = await client.put(
                f"/api/v1/admin/telephony/configurations/{config.id}/platform-managed",
                json={"managed": False},
            )

        assert response.status_code == 200
        assert response.json()["is_platform_managed"] is False

    async def test_clearing_is_refused_while_our_numbers_are_attached(
        self, db_session, async_session
    ):
        """The expensive mistake: clearing the flag orphans the rentals."""
        org, config = await _org_and_config(async_session, "held", managed=True)
        staff = await _staff(async_session, "s-held")
        await _number(async_session, org.id, config.id, "+918041110100")

        async with _client(staff) as client:
            response = await client.put(
                f"/api/v1/admin/telephony/configurations/{config.id}/platform-managed",
                json={"managed": False},
            )

        assert response.status_code == 409
        assert "+918041110100" in response.json()["detail"]

        stored = await db_client.get_telephony_configuration(config.id)
        assert stored.is_platform_managed is True

    async def test_a_released_number_does_not_block_clearing(
        self, db_session, async_session
    ):
        """Released means we no longer pay for it, so it holds nothing back."""
        org, config = await _org_and_config(async_session, "released", managed=True)
        staff = await _staff(async_session, "s-rel")
        await _number(
            async_session,
            org.id,
            config.id,
            "+918041110200",
            status=PhoneNumberStatus.RELEASED.value,
        )

        async with _client(staff) as client:
            response = await client.put(
                f"/api/v1/admin/telephony/configurations/{config.id}/platform-managed",
                json={"managed": False},
            )

        assert response.status_code == 200

    async def test_a_customers_own_number_does_not_block_clearing(
        self, db_session, async_session
    ):
        """No carrier id means we never bought it, so we are not paying rent."""
        org, config = await _org_and_config(async_session, "byo", managed=True)
        staff = await _staff(async_session, "s-byo")
        await _number(
            async_session, org.id, config.id, "+918041110300", carrier_id=None
        )

        async with _client(staff) as client:
            response = await client.put(
                f"/api/v1/admin/telephony/configurations/{config.id}/platform-managed",
                json={"managed": False},
            )

        assert response.status_code == 200


class TestListing:
    async def test_the_default_listing_is_what_staff_administer(
        self, db_session, async_session
    ):
        """Managed configurations only — every customer's own carrier account
        across the platform is a lot of rows nobody asked for."""
        managed_org, managed_cfg = await _org_and_config(
            async_session, "list-managed", managed=True
        )
        _, own_cfg = await _org_and_config(async_session, "list-own", managed=False)
        staff = await _staff(async_session, "s-list")

        async with _client(staff) as client:
            response = await client.get("/api/v1/admin/telephony/configurations")

        ids = {c["id"] for c in response.json()["configurations"]}
        assert managed_cfg.id in ids
        assert own_cfg.id not in ids

    async def test_filtering_by_organization_shows_all_of_its_configurations(
        self, db_session, async_session
    ):
        org, own_cfg = await _org_and_config(async_session, "list-filter")
        staff = await _staff(async_session, "s-filter")

        async with _client(staff) as client:
            response = await client.get(
                "/api/v1/admin/telephony/configurations",
                params={"organization_id": org.id},
            )

        ids = {c["id"] for c in response.json()["configurations"]}
        assert own_cfg.id in ids
