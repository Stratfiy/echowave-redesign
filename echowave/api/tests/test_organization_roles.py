"""The org-level role matrix: member / admin / owner.

Replaces a roleless many-to-many table where every member had identical
access. The properties worth guarding: a fresh membership defaults to
MEMBER, re-syncing an existing membership never resets an already-granted
role, and the organization can never be left with zero Owners — that would
be a dead end nobody in the product could recover from.
"""

from __future__ import annotations

import pytest

from api.db.models import OrganizationMembershipModel, OrganizationModel, UserModel
from api.enums import OrganizationRole


async def _org_with_member(
    async_session, slug: str, *, role: str = OrganizationRole.OWNER.value
) -> tuple[UserModel, OrganizationModel]:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}", selected_organization_id=None)
    async_session.add_all([org, user])
    await async_session.flush()
    user.selected_organization_id = org.id
    async_session.add(
        OrganizationMembershipModel(user_id=user.id, organization_id=org.id, role=role)
    )
    await async_session.flush()
    return user, org


@pytest.mark.asyncio
class TestAddUserToOrganization:
    async def test_defaults_to_member(self, async_session, db_session):
        org = OrganizationModel(provider_id="org-default-role", quota_decibyl_tokens=0)
        user = UserModel(provider_id="user-default-role")
        async_session.add_all([org, user])
        await async_session.flush()

        await db_session.add_user_to_organization(user.id, org.id)

        membership = await db_session.get_membership(user.id, org.id)
        assert membership is not None
        assert membership.role == OrganizationRole.MEMBER.value

    async def test_an_explicit_role_is_honoured(self, async_session, db_session):
        org = OrganizationModel(provider_id="org-explicit-role", quota_decibyl_tokens=0)
        user = UserModel(provider_id="user-explicit-role")
        async_session.add_all([org, user])
        await async_session.flush()

        await db_session.add_user_to_organization(
            user.id, org.id, role=OrganizationRole.OWNER.value
        )

        membership = await db_session.get_membership(user.id, org.id)
        assert membership.role == OrganizationRole.OWNER.value

    async def test_a_repeat_add_does_not_reset_an_already_granted_role(
        self, async_session, db_session
    ):
        """The re-sync path in get_user calls this on every login where the
        team differs; it must never quietly demote someone back to MEMBER."""
        user, org = await _org_with_member(
            async_session, "resync", role=OrganizationRole.ADMIN.value
        )

        await db_session.add_user_to_organization(user.id, org.id)

        membership = await db_session.get_membership(user.id, org.id)
        assert membership.role == OrganizationRole.ADMIN.value


@pytest.mark.asyncio
class TestMembershipQueries:
    async def test_list_organization_members_includes_role(
        self, async_session, db_session
    ):
        user, org = await _org_with_member(
            async_session, "list", role=OrganizationRole.ADMIN.value
        )

        members = await db_session.list_organization_members(org.id)

        assert len(members) == 1
        assert members[0].user.id == user.id
        assert members[0].role == OrganizationRole.ADMIN.value

    async def test_get_membership_returns_none_for_a_non_member(
        self, async_session, db_session
    ):
        org = OrganizationModel(provider_id="org-nonmember", quota_decibyl_tokens=0)
        user = UserModel(provider_id="user-nonmember")
        async_session.add_all([org, user])
        await async_session.flush()

        assert await db_session.get_membership(user.id, org.id) is None

    async def test_is_user_member_of_organization(self, async_session, db_session):
        user, org = await _org_with_member(async_session, "ismember")
        other = UserModel(provider_id="user-notinorg")
        async_session.add(other)
        await async_session.flush()

        assert await db_session.is_user_member_of_organization(user.id, org.id)
        assert not await db_session.is_user_member_of_organization(other.id, org.id)


@pytest.mark.asyncio
class TestUpdateAndRemoveMembership:
    async def test_update_member_role_changes_it(self, async_session, db_session):
        user, org = await _org_with_member(
            async_session, "update", role=OrganizationRole.MEMBER.value
        )

        updated = await db_session.update_member_role(
            user.id, org.id, OrganizationRole.ADMIN.value
        )

        assert updated is not None
        assert updated.role == OrganizationRole.ADMIN.value

    async def test_update_member_role_returns_none_for_a_non_member(
        self, async_session, db_session
    ):
        org = OrganizationModel(provider_id="org-update-none", quota_decibyl_tokens=0)
        user = UserModel(provider_id="user-update-none")
        async_session.add_all([org, user])
        await async_session.flush()

        result = await db_session.update_member_role(
            user.id, org.id, OrganizationRole.ADMIN.value
        )
        assert result is None

    async def test_remove_user_from_organization(self, async_session, db_session):
        user, org = await _org_with_member(async_session, "remove")

        removed = await db_session.remove_user_from_organization(user.id, org.id)

        assert removed is True
        assert await db_session.get_membership(user.id, org.id) is None

    async def test_removing_a_non_member_returns_false(self, async_session, db_session):
        org = OrganizationModel(provider_id="org-remove-none", quota_decibyl_tokens=0)
        user = UserModel(provider_id="user-remove-none")
        async_session.add_all([org, user])
        await async_session.flush()

        assert await db_session.remove_user_from_organization(user.id, org.id) is False


@pytest.mark.asyncio
class TestCountOrganizationOwners:
    async def test_counts_only_owners(self, async_session, db_session):
        _owner, org = await _org_with_member(
            async_session, "owner1", role=OrganizationRole.OWNER.value
        )
        member = UserModel(provider_id="user-owner-count-member")
        admin = UserModel(provider_id="user-owner-count-admin")
        async_session.add_all([member, admin])
        await async_session.flush()
        async_session.add_all(
            [
                OrganizationMembershipModel(
                    user_id=member.id,
                    organization_id=org.id,
                    role=OrganizationRole.MEMBER.value,
                ),
                OrganizationMembershipModel(
                    user_id=admin.id,
                    organization_id=org.id,
                    role=OrganizationRole.ADMIN.value,
                ),
            ]
        )
        await async_session.flush()

        assert await db_session.count_organization_owners(org.id) == 1


@pytest.mark.asyncio
class TestOrganizationMembersRoutes:
    """HTTP-level: the actual permission boundary a request hits."""

    async def test_any_member_can_list_the_roster(
        self, async_session, db_session, test_client_factory
    ):
        user, _org = await _org_with_member(
            async_session, "roster-member", role=OrganizationRole.MEMBER.value
        )

        async with test_client_factory(user) as client:
            response = await client.get("/api/v1/organizations/members")

        assert response.status_code == 200
        member_ids = [m["user_id"] for m in response.json()["members"]]
        assert user.id in member_ids

    async def test_a_member_cannot_change_roles(
        self, async_session, db_session, test_client_factory
    ):
        user, org = await _org_with_member(
            async_session, "member-no-promote", role=OrganizationRole.MEMBER.value
        )
        other = UserModel(provider_id="user-member-target")
        async_session.add(other)
        await async_session.flush()
        async_session.add(
            OrganizationMembershipModel(
                user_id=other.id,
                organization_id=org.id,
                role=OrganizationRole.MEMBER.value,
            )
        )
        await async_session.flush()

        async with test_client_factory(user) as client:
            response = await client.patch(
                f"/api/v1/organizations/members/{other.id}",
                json={"role": "admin"},
            )

        assert response.status_code == 403

    async def test_an_owner_can_promote_a_member(
        self, async_session, db_session, test_client_factory
    ):
        owner, org = await _org_with_member(async_session, "owner-promotes")
        member = UserModel(provider_id="user-to-promote")
        async_session.add(member)
        await async_session.flush()
        async_session.add(
            OrganizationMembershipModel(
                user_id=member.id,
                organization_id=org.id,
                role=OrganizationRole.MEMBER.value,
            )
        )
        await async_session.flush()

        async with test_client_factory(owner) as client:
            response = await client.patch(
                f"/api/v1/organizations/members/{member.id}",
                json={"role": "admin"},
            )

        assert response.status_code == 200
        assert response.json()["role"] == "admin"

    async def test_the_last_owner_cannot_be_demoted(
        self, async_session, db_session, test_client_factory
    ):
        owner, org = await _org_with_member(async_session, "sole-owner")

        async with test_client_factory(owner) as client:
            response = await client.patch(
                f"/api/v1/organizations/members/{owner.id}",
                json={"role": "member"},
            )

        assert response.status_code == 400
        membership = await db_session.get_membership(owner.id, org.id)
        assert membership.role == OrganizationRole.OWNER.value

    async def test_the_last_owner_cannot_be_removed(
        self, async_session, db_session, test_client_factory
    ):
        owner, org = await _org_with_member(async_session, "sole-owner-remove")

        async with test_client_factory(owner) as client:
            response = await client.delete(f"/api/v1/organizations/members/{owner.id}")

        assert response.status_code == 400
        assert await db_session.get_membership(owner.id, org.id) is not None

    async def test_an_owner_can_remove_another_member(
        self, async_session, db_session, test_client_factory
    ):
        owner, org = await _org_with_member(async_session, "owner-removes")
        member = UserModel(provider_id="user-to-remove")
        async_session.add(member)
        await async_session.flush()
        async_session.add(
            OrganizationMembershipModel(
                user_id=member.id,
                organization_id=org.id,
                role=OrganizationRole.MEMBER.value,
            )
        )
        await async_session.flush()

        async with test_client_factory(owner) as client:
            response = await client.delete(f"/api/v1/organizations/members/{member.id}")

        assert response.status_code == 200
        assert await db_session.get_membership(member.id, org.id) is None

    async def test_a_second_owner_can_be_demoted(
        self, async_session, db_session, test_client_factory
    ):
        """The last-owner guard should not fire when there's a second one."""
        owner, org = await _org_with_member(async_session, "two-owners-a")
        second_owner = UserModel(provider_id="user-second-owner")
        async_session.add(second_owner)
        await async_session.flush()
        async_session.add(
            OrganizationMembershipModel(
                user_id=second_owner.id,
                organization_id=org.id,
                role=OrganizationRole.OWNER.value,
            )
        )
        await async_session.flush()

        async with test_client_factory(owner) as client:
            response = await client.patch(
                f"/api/v1/organizations/members/{second_owner.id}",
                json={"role": "member"},
            )

        assert response.status_code == 200


@pytest.mark.asyncio
class TestAuthUserResponseCarriesOrganizationRole:
    async def test_the_callers_role_in_their_selected_org_is_reported(
        self, async_session, db_session, test_client_factory
    ):
        user, _org = await _org_with_member(
            async_session, "authuser-role", role=OrganizationRole.ADMIN.value
        )

        async with test_client_factory(user) as client:
            response = await client.get("/api/v1/user/auth/user")

        assert response.status_code == 200
        body = response.json()
        assert body["organization_role"] == "admin"
        assert body["staff_role"] is None
