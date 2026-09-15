"""An API key is an organisation credential, never a staff session (KAN-83).

The hole: ``_handle_api_key_auth`` hands back the user who created the key,
and that user may be Decibyl staff. A production key that a superadmin
minted for their own org would then pass ``get_staff`` / ``get_superuser``
and could call ``/admin/*`` or impersonate an account -- privilege
escalation from a string in a config file.

These prove the staff role is stripped from an API-key request, for both
tiers and for a production key (not only a sandbox one), and that an
ordinary member's key is unaffected.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.enums import STAFF_ROLE_RANK, StaffRole
from api.services.auth import depends


def _key(*, environment="production", created_by=42, organization_id=7):
    return SimpleNamespace(
        environment=environment,
        created_by=created_by,
        organization_id=organization_id,
        key_prefix="dcb_live_ab",
    )


def _user(staff_role):
    return SimpleNamespace(id=42, staff_role=staff_role, selected_organization_id=None)


@pytest.mark.asyncio
class TestAnApiKeyCarriesNoStaffRole:
    async def _auth(self, *, key, user, method="GET"):
        with (
            patch.object(
                depends.db_client, "validate_api_key", AsyncMock(return_value=key)
            ),
            patch.object(
                depends.db_client, "get_user_by_id", AsyncMock(return_value=user)
            ),
        ):
            return await depends._handle_api_key_auth("dcb_live_secret", method=method)

    async def test_a_superadmins_production_key_is_stripped_of_staff(self):
        user = await self._auth(key=_key(), user=_user(StaffRole.SUPERADMIN.value))
        assert user.staff_role is None
        # And so the staff rank check would refuse it.
        assert STAFF_ROLE_RANK.get(user.staff_role or "", -1) < 0

    async def test_a_support_tier_key_is_stripped_too(self):
        user = await self._auth(key=_key(), user=_user(StaffRole.SUPPORT.value))
        assert user.staff_role is None

    async def test_a_sandbox_key_is_also_stripped(self):
        user = await self._auth(
            key=_key(environment="sandbox"),
            user=_user(StaffRole.SUPERADMIN.value),
            method="GET",
        )
        assert user.staff_role is None

    async def test_an_ordinary_members_key_still_authenticates(self):
        user = await self._auth(key=_key(), user=_user(None))
        assert user.staff_role is None
        assert user.selected_organization_id == 7

    async def test_the_org_context_is_the_keys_org_not_the_users(self):
        user = await self._auth(key=_key(organization_id=99), user=_user(None))
        assert user.selected_organization_id == 99
