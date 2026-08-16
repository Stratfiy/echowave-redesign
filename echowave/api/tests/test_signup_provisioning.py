"""Who owns an account the moment it is created.

``provision_new_account`` is the one thing both front doors share — password
signup and the Google callback both call it — so whatever role it hands out is
the role every new customer starts with. It handed out the default, MEMBER, to
the only person in an organization that had just been created *for* them.

That is not a cosmetic mistake. Every ADMIN-gated surface answered the founder
with a 403 — provider keys, the billing profile, the autopay mandate, removing
a number from the do-not-call list, minting a tool credential — and the route
that would have fixed it, promoting a member, is OWNER-gated. A brand-new
account was a dead end from the moment it existed, and the symptom a customer
reports is "I signed up and half the product says I need to ask an admin",
with no admin to ask.

So the assertion that matters is not the string in the column: it is that the
founder actually clears the dependency those routes are guarded with.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from api.db.models import OrganizationMembershipModel, OrganizationModel, UserModel
from api.enums import OrganizationRole
from api.services.auth.depends import require_organization_role
from api.services.auth.provisioning import provision_new_account


@pytest.fixture
def no_default_model_config():
    """Skip the default-model step.

    It reaches MPS over HTTP and is already best-effort — ``provision_new_account``
    swallows its failure on purpose, so leaving it in would only trade a
    connection refusal for ten seconds of nothing.
    """
    with patch(
        "api.services.auth.provisioning.create_user_configuration_with_mps_key",
        new=AsyncMock(return_value=None),
    ):
        yield


async def _new_user(async_session, slug: str) -> UserModel:
    user = UserModel(provider_id=f"user-{slug}", selected_organization_id=None)
    async_session.add(user)
    await async_session.flush()
    return user


@pytest.mark.asyncio
class TestANewAccountOwnsItself:
    async def test_the_founder_is_an_owner(
        self, async_session, db_session, no_default_model_config
    ):
        user = await _new_user(async_session, "founder")

        organization = await provision_new_account(user)

        membership = await db_session.get_membership(user.id, organization.id)
        assert membership is not None
        assert membership.role == OrganizationRole.OWNER.value

    async def test_the_founder_clears_the_admin_gate(
        self, async_session, db_session, no_default_model_config
    ):
        """The point of the role, rather than the role itself.

        Asserted against the real dependency every admin-only route depends
        on, so this fails if the rank table, the membership lookup or the
        provisioned role ever disagree — not just if someone edits a literal.
        """
        user = await _new_user(async_session, "founder-admin-gate")
        await provision_new_account(user)
        await async_session.refresh(user)

        gate = require_organization_role(OrganizationRole.ADMIN)
        assert await gate(user) is user

    async def test_the_founder_clears_the_owner_gate(
        self, async_session, db_session, no_default_model_config
    ):
        """Membership management is OWNER-gated, and it is the way out of every
        other lockout: an account whose founder cannot invite or promote anyone
        can never grow a second administrator."""
        user = await _new_user(async_session, "founder-owner-gate")
        await provision_new_account(user)
        await async_session.refresh(user)

        gate = require_organization_role(OrganizationRole.OWNER)
        assert await gate(user) is user

    async def test_the_account_is_usable_at_all(
        self, async_session, db_session, no_default_model_config
    ):
        """Provisioning also has to select the organization. Without it every
        org-scoped route 400s with "No organization selected" before the role
        is ever consulted, which looks like a different bug entirely."""
        user = await _new_user(async_session, "founder-selected")

        organization = await provision_new_account(user)

        await async_session.refresh(user)
        assert user.selected_organization_id == organization.id


@pytest.mark.asyncio
class TestProvisioningIsNotAWayIn:
    async def test_it_never_promotes_an_existing_membership(
        self, async_session, db_session, no_default_model_config
    ):
        """The Google door re-provisions an account that never finished signing
        up. If that path could overwrite a role, a member of an organization
        somebody else administers could reach OWNER by signing in again —
        so the promotion has to be create-only, not upsert."""
        org = OrganizationModel(
            provider_id="org-already-a-member", quota_decibyl_tokens=0
        )
        user = await _new_user(async_session, "already-a-member")
        async_session.add(org)
        await async_session.flush()
        async_session.add(
            OrganizationMembershipModel(
                user_id=user.id,
                organization_id=org.id,
                role=OrganizationRole.MEMBER.value,
            )
        )
        await async_session.flush()

        # Provisioning attaches the organization derived from this user's own
        # provider id, so reach past it and re-run the membership step the way
        # a repeat provision would.
        await db_session.add_user_to_organization(
            user.id, org.id, role=OrganizationRole.OWNER.value
        )

        membership = await db_session.get_membership(user.id, org.id)
        assert membership.role == OrganizationRole.MEMBER.value

        user.selected_organization_id = org.id
        await async_session.flush()
        gate = require_organization_role(OrganizationRole.ADMIN)
        with pytest.raises(HTTPException) as exc:
            await gate(user)
        assert exc.value.status_code == 403


# The migration's own statement, so this tests what will actually run against
# production rather than a retyped copy that can drift from it.
PROMOTE_FOUNDERS_SQL = importlib.import_module(
    "api.alembic.versions.c3f7a1d20b64_promote_founders_of_ownerless_orgs"
).PROMOTE_FOUNDERS_SQL


@pytest.mark.asyncio
class TestTheBackfillPicksTheRightPerson:
    """The accounts created while provisioning handed out MEMBER.

    They cannot fix themselves — that is the whole problem — so the repair has
    to be a migration, and a migration that promotes the wrong person hands one
    customer's account to another of its members. Worth watching it choose.
    """

    async def _membership(
        self, async_session, org: OrganizationModel, slug: str, role: str, day: int
    ) -> UserModel:
        user = UserModel(provider_id=f"user-backfill-{slug}")
        async_session.add(user)
        await async_session.flush()
        async_session.add(
            OrganizationMembershipModel(
                user_id=user.id,
                organization_id=org.id,
                role=role,
                created_at=datetime(2026, 1, day, tzinfo=UTC),
            )
        )
        await async_session.flush()
        return user

    async def _org(self, async_session, slug: str) -> OrganizationModel:
        org = OrganizationModel(
            provider_id=f"org-backfill-{slug}", quota_decibyl_tokens=0
        )
        async_session.add(org)
        await async_session.flush()
        return org

    async def _roles(self, async_session, org: OrganizationModel) -> dict[int, str]:
        rows = await async_session.execute(
            select(
                OrganizationMembershipModel.user_id, OrganizationMembershipModel.role
            ).where(OrganizationMembershipModel.organization_id == org.id)
        )
        return dict(rows.all())

    async def test_it_promotes_the_founder_and_leaves_the_colleague_alone(
        self, async_session
    ):
        org = await self._org(async_session, "broken")
        founder = await self._membership(
            async_session, org, "broken-founder", OrganizationRole.MEMBER.value, 1
        )
        colleague = await self._membership(
            async_session, org, "broken-colleague", OrganizationRole.MEMBER.value, 2
        )

        await async_session.execute(text(PROMOTE_FOUNDERS_SQL))

        roles = await self._roles(async_session, org)
        assert roles[founder.id] == OrganizationRole.OWNER.value
        # Exactly one Owner. Promoting everybody would be the easy way to end
        # the lockout and would also hand each member the others' billing.
        assert roles[colleague.id] == OrganizationRole.MEMBER.value

    async def test_it_leaves_a_healthy_organization_untouched(self, async_session):
        org = await self._org(async_session, "healthy")
        # The earliest row is deliberately *not* the Owner: an organization
        # that transferred ownership must not have it handed back.
        early_member = await self._membership(
            async_session, org, "healthy-early", OrganizationRole.MEMBER.value, 1
        )
        owner = await self._membership(
            async_session, org, "healthy-owner", OrganizationRole.OWNER.value, 2
        )

        await async_session.execute(text(PROMOTE_FOUNDERS_SQL))

        roles = await self._roles(async_session, org)
        assert roles[early_member.id] == OrganizationRole.MEMBER.value
        assert roles[owner.id] == OrganizationRole.OWNER.value

    async def test_a_lone_founder_gets_their_account_back(self, async_session):
        org = await self._org(async_session, "solo")
        founder = await self._membership(
            async_session, org, "solo-founder", OrganizationRole.MEMBER.value, 1
        )

        await async_session.execute(text(PROMOTE_FOUNDERS_SQL))

        roles = await self._roles(async_session, org)
        assert roles[founder.id] == OrganizationRole.OWNER.value

    async def test_running_it_twice_changes_nothing(self, async_session):
        """Migrations get replayed — a restored backup, a re-run deploy. The
        second pass must not walk the organization one seat further."""
        org = await self._org(async_session, "idempotent")
        founder = await self._membership(
            async_session, org, "idem-founder", OrganizationRole.MEMBER.value, 1
        )
        colleague = await self._membership(
            async_session, org, "idem-colleague", OrganizationRole.MEMBER.value, 2
        )

        await async_session.execute(text(PROMOTE_FOUNDERS_SQL))
        await async_session.execute(text(PROMOTE_FOUNDERS_SQL))

        roles = await self._roles(async_session, org)
        assert roles[founder.id] == OrganizationRole.OWNER.value
        assert roles[colleague.id] == OrganizationRole.MEMBER.value
