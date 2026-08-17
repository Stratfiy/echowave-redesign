"""Decibyl's own caller IDs, lent to every account.

A trial account has no carrier configuration, so `/telephony/initiate-call`
failed at `telephony_not_configured` before it reached anything else — a wall in
front of exactly the person trying to find out whether the product works.
Twilio answers the same question with one shared number that never becomes a
customer's, and this is that.

Almost every test here is about what a shared number must *not* do. Inbound
dispatch resolves an organization from `(provider, account_id, called number)`
with no organization in the key, so a shared row reachable inbound would answer
one customer's caller inside whichever tenant the database returned first —
which is the cross-org misrouting this codebase has already been bitten by once.
"""

from __future__ import annotations

import pytest

from api.db.models import (
    OrganizationModel,
    TelephonyConfigurationModel,
    TelephonyPhoneNumberModel,
)

SHARED = "+919000000001"
CUSTOMER = "+919000000002"
PLIVO_AUTH_ID = "MAPLATFORMXXXXXXXXXX"


async def _org(session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    return org


async def _config(session, org, *, auth_id: str = PLIVO_AUTH_ID):
    row = TelephonyConfigurationModel(
        organization_id=org.id,
        name="plivo",
        provider="plivo",
        credentials={"auth_id": auth_id, "auth_token": "tok"},
        is_default_outbound=True,
    )
    session.add(row)
    await session.flush()
    return row


async def _number(session, org, config, address: str, **kwargs):
    row = TelephonyPhoneNumberModel(
        organization_id=org.id,
        telephony_configuration_id=config.id,
        address=address,
        address_normalized=address,
        address_type="pstn",
        is_active=True,
        **kwargs,
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
class TestASharedNumberIsNotReachableInbound:
    async def test_the_account_matched_lookup_does_not_return_it(
        self, db_session, async_session
    ):
        """The primary inbound path. A hit here would answer a stranger's call
        as whichever tenant we happened to park the number on."""
        from api.db import db_client

        org = await _org(async_session, "shared-inb")
        config = await _config(async_session, org)
        await _number(async_session, org, config, SHARED, is_shared_outbound=True)

        match = await db_client.find_inbound_route_by_account(
            provider="plivo",
            account_id_field="auth_id",
            account_id=PLIVO_AUTH_ID,
            to_number=SHARED,
        )

        assert match is None

    async def test_the_number_only_fallback_does_not_return_it(
        self, db_session, async_session
    ):
        """The fallback added for Plivo webhooks with no AuthID. It matches on
        the called number alone, which is exactly the shape that would pick up
        a shared caller ID."""
        from api.db import db_client

        org = await _org(async_session, "shared-fallback")
        config = await _config(async_session, org)
        await _number(async_session, org, config, SHARED, is_shared_outbound=True)

        match = await db_client.find_inbound_route_by_number(
            provider="plivo", to_number=SHARED
        )

        assert match is None

    async def test_an_ordinary_number_is_still_reachable(
        self, db_session, async_session
    ):
        """The exclusion must be the flag doing it, not the query being broken."""
        from api.db import db_client

        org = await _org(async_session, "ordinary")
        config = await _config(async_session, org, auth_id="MAORDINARYXXXXXXXXXX")
        await _number(async_session, org, config, CUSTOMER)

        match = await db_client.find_inbound_route_by_account(
            provider="plivo",
            account_id_field="auth_id",
            account_id="MAORDINARYXXXXXXXXXX",
            to_number=CUSTOMER,
        )

        assert match is not None


@pytest.mark.asyncio
class TestLendingOneNumberToEveryone:
    async def test_a_shared_number_does_not_conflict_with_anything(
        self, db_session, async_session
    ):
        """What makes lending possible at all.

        The routing-conflict check enforces global uniqueness on
        `(provider, account_id, address)` so two orgs cannot both own a number.
        A shared caller ID is not inbound-routable, so it cannot make anything
        ambiguous — and if it counted as a conflict, it could be lent to exactly
        one account, which is not lending.
        """
        from api.db import db_client

        org = await _org(async_session, "conflict")
        config = await _config(async_session, org)
        await _number(async_session, org, config, SHARED, is_shared_outbound=True)

        conflict = await db_client.find_inbound_routing_conflict(
            provider="plivo",
            account_id_field="auth_id",
            account_id=PLIVO_AUTH_ID,
            address=SHARED,
        )

        assert conflict is None

    async def test_the_pool_is_listed_across_organizations(
        self, db_session, async_session
    ):
        """Not org-scoped, deliberately: the numbers are ours and every account
        dials from them."""
        from api.db import db_client

        org = await _org(async_session, "pool")
        config = await _config(async_session, org)
        await _number(async_session, org, config, SHARED, is_shared_outbound=True)
        await _number(async_session, org, config, CUSTOMER)

        rows = await db_client.list_shared_outbound_numbers()

        addresses = [r.address_normalized for r in rows]
        assert SHARED in addresses
        assert CUSTOMER not in addresses

    async def test_taking_a_number_back_removes_it_from_the_pool(
        self, db_session, async_session
    ):
        from api.db import db_client

        org = await _org(async_session, "revoke")
        config = await _config(async_session, org)
        row = await _number(async_session, org, config, SHARED, is_shared_outbound=True)

        await db_client.set_shared_outbound(row.id, shared=False)
        rows = await db_client.list_shared_outbound_numbers()

        assert SHARED not in [r.address_normalized for r in rows]
