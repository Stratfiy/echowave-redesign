"""Getting a second person into an organization.

There was no way to. Membership was mirrored from Stack Auth in SaaS mode and
created once at signup otherwise, so a self-hosted organization had exactly one
member forever — and every role below Owner was therefore unreachable, because
there was never anybody to be a member.

An invitation is a bearer credential for a seat in somebody's account, so most
of what follows is about the ways a credential goes wrong: stored in the clear,
forwarded to the wrong person, replayed after use, valid forever, or issued
three times over so revoking the visible one leaves two behind.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api.db.models import (
    OrganizationInvitationModel,
    OrganizationMembershipModel,
    OrganizationModel,
    UserModel,
)
from api.enums import OrganizationRole
from api.services.organizations import invitations
from api.services.organizations.invitations import InvitationError


async def _org(async_session, slug: str) -> OrganizationModel:
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


async def _user(async_session, email: str) -> UserModel:
    user = UserModel(provider_id=f"user-{email}", email=email)
    async_session.add(user)
    await async_session.flush()
    return user


async def _member(async_session, org, user, role=OrganizationRole.MEMBER.value):
    async_session.add(
        OrganizationMembershipModel(user_id=user.id, organization_id=org.id, role=role)
    )
    await async_session.flush()


async def _membership(async_session, org, user):
    return await async_session.scalar(
        select(OrganizationMembershipModel).where(
            OrganizationMembershipModel.user_id == user.id,
            OrganizationMembershipModel.organization_id == org.id,
        )
    )


@pytest.mark.asyncio
class TestOfferingASeat:
    async def test_an_invitation_can_be_accepted(self, async_session):
        """The whole point, and the thing that was impossible."""
        org = await _org(async_session, "accept-basic")
        joiner = await _user(async_session, "joiner@example.com")

        _row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            email="joiner@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        await invitations.accept(async_session, raw_token=token, user=joiner)

        membership = await _membership(async_session, org, joiner)
        assert membership is not None
        assert membership.role == OrganizationRole.MEMBER.value

    async def test_the_seat_carries_the_role_it_was_offered_at(self, async_session):
        org = await _org(async_session, "accept-admin")
        joiner = await _user(async_session, "admin-joiner@example.com")

        _row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            email="admin-joiner@example.com",
            role=OrganizationRole.ADMIN.value,
            invited_by=None,
        )
        await invitations.accept(async_session, raw_token=token, user=joiner)

        membership = await _membership(async_session, org, joiner)
        assert membership.role == OrganizationRole.ADMIN.value

    async def test_accepting_selects_the_organization(self, async_session):
        """Accepting and then appearing to be somewhere else reads as a
        failure, and is how somebody concludes the invitation did not work."""
        org = await _org(async_session, "accept-selects")
        joiner = await _user(async_session, "selects@example.com")

        _row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            email="selects@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        await invitations.accept(async_session, raw_token=token, user=joiner)

        assert joiner.selected_organization_id == org.id

    async def test_the_address_is_normalized(self, async_session):
        """Somebody types Joiner@Example.COM and the person signs in as
        joiner@example.com. Those are the same mailbox."""
        org = await _org(async_session, "normalize")
        joiner = await _user(async_session, "mixed@example.com")

        _row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            email="  Mixed@Example.COM ",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        await invitations.accept(async_session, raw_token=token, user=joiner)

        assert await _membership(async_session, org, joiner) is not None

    async def test_inviting_an_existing_member_says_so(self, async_session):
        """Rather than leaving an Owner waiting for an acceptance that will
        never come, when what they wanted was to change a role."""
        org = await _org(async_session, "already-in")
        existing = await _user(async_session, "already@example.com")
        await _member(async_session, org, existing)

        with pytest.raises(InvitationError) as exc:
            await invitations.invite(
                async_session,
                organization_id=org.id,
                email="already@example.com",
                role=OrganizationRole.ADMIN.value,
                invited_by=None,
            )
        assert "already in this organization" in str(exc.value)

    async def test_a_second_invitation_to_the_same_address_is_refused(
        self, async_session
    ):
        """Three clicks of Invite must not put three working links in one
        inbox — revoking the one on screen would leave two behind."""
        org = await _org(async_session, "double-invite")
        await invitations.invite(
            async_session,
            organization_id=org.id,
            email="dup@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )

        with pytest.raises(InvitationError) as exc:
            await invitations.invite(
                async_session,
                organization_id=org.id,
                email="dup@example.com",
                role=OrganizationRole.MEMBER.value,
                invited_by=None,
            )
        assert "already has an invitation" in str(exc.value)

    async def test_the_database_refuses_a_second_open_invitation(self, async_session):
        """The service checks, but the index is what holds under a race."""
        org = await _org(async_session, "double-invite-db")
        await invitations.invite(
            async_session,
            organization_id=org.id,
            email="race@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        async_session.add(
            OrganizationInvitationModel(
                organization_id=org.id,
                email="race@example.com",
                role=OrganizationRole.MEMBER.value,
                token_hash="a-different-hash",
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        with pytest.raises(Exception):
            await async_session.flush()

    async def test_an_unknown_role_is_refused(self, async_session):
        org = await _org(async_session, "bad-role")
        with pytest.raises(InvitationError):
            await invitations.invite(
                async_session,
                organization_id=org.id,
                email="x@example.com",
                role="superuser",
                invited_by=None,
            )


@pytest.mark.asyncio
class TestTheTokenIsACredential:
    async def test_it_is_never_stored_in_the_clear(self, async_session):
        """A backup of this table must not hand out seats."""
        org = await _org(async_session, "hashed")
        row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            email="hash@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )

        assert row.token_hash != token
        assert token not in row.token_hash
        assert row.token_hash == invitations.hash_token(token)

    async def test_two_invitations_do_not_share_a_token(self, async_session):
        org_a = await _org(async_session, "tok-a")
        org_b = await _org(async_session, "tok-b")
        _r1, first = await invitations.invite(
            async_session,
            organization_id=org_a.id,
            email="a@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        _r2, second = await invitations.invite(
            async_session,
            organization_id=org_b.id,
            email="b@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        assert first != second

    async def test_a_forwarded_link_does_not_work(self, async_session):
        """The one that matters most. An invitation lands in a mailbox, and a
        mailbox forwards — without the address check the seat goes to whoever
        opens the link first, which is not who was invited."""
        org = await _org(async_session, "forwarded")
        _row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            email="intended@example.com",
            role=OrganizationRole.OWNER.value,
            invited_by=None,
        )
        someone_else = await _user(async_session, "opportunist@example.com")

        with pytest.raises(InvitationError) as exc:
            await invitations.accept(async_session, raw_token=token, user=someone_else)

        assert "intended@example.com" in str(exc.value)
        assert await _membership(async_session, org, someone_else) is None

    async def test_a_used_invitation_cannot_be_replayed(self, async_session):
        org = await _org(async_session, "replay")
        joiner = await _user(async_session, "replay@example.com")
        _row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            email="replay@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        await invitations.accept(async_session, raw_token=token, user=joiner)

        with pytest.raises(InvitationError) as exc:
            await invitations.accept(async_session, raw_token=token, user=joiner)
        assert "already been used" in str(exc.value)

    async def test_an_expired_invitation_is_refused(self, async_session):
        """A credential with no end is one sitting in a mailbox somebody
        eventually loses control of."""
        org = await _org(async_session, "expired")
        joiner = await _user(async_session, "expired@example.com")
        row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            email="expired@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await async_session.flush()

        with pytest.raises(InvitationError) as exc:
            await invitations.accept(async_session, raw_token=token, user=joiner)
        assert "expired" in str(exc.value)
        assert await _membership(async_session, org, joiner) is None

    async def test_a_revoked_invitation_is_refused(self, async_session):
        org = await _org(async_session, "revoked")
        joiner = await _user(async_session, "revoked@example.com")
        row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            email="revoked@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        await invitations.revoke(
            async_session, organization_id=org.id, invitation_id=row.id
        )

        with pytest.raises(InvitationError):
            await invitations.accept(async_session, raw_token=token, user=joiner)
        assert await _membership(async_session, org, joiner) is None

    async def test_a_made_up_token_is_refused(self, async_session):
        with pytest.raises(InvitationError):
            await invitations.lookup(async_session, "not-a-real-token")

    async def test_revoking_is_scoped_to_the_organization(self, async_session):
        """An id from a request never implies ownership. Without the scope
        this is a write against another tenant's row."""
        mine = await _org(async_session, "revoke-mine")
        theirs = await _org(async_session, "revoke-theirs")
        row, _token = await invitations.invite(
            async_session,
            organization_id=theirs.id,
            email="theirs@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )

        with pytest.raises(InvitationError):
            await invitations.revoke(
                async_session, organization_id=mine.id, invitation_id=row.id
            )

        await async_session.refresh(row)
        assert row.revoked_at is None


@pytest.mark.asyncio
class TestAlreadyAMember:
    async def test_an_invitation_can_promote_but_never_demote(self, async_session):
        """A stale invitation must not become a demotion.

        Somebody invited as a member, promoted to admin before they got round
        to clicking, then clicking: the link is old news, not an instruction to
        take their admin away.
        """
        org = await _org(async_session, "no-demote")
        person = await _user(async_session, "nodemote@example.com")
        await _member(async_session, org, person, OrganizationRole.ADMIN.value)

        _row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            # Bypasses the "already a member" refusal by inviting before the
            # membership exists in a real flow; here it is set up directly to
            # isolate the accept-side rule.
            email="nodemote-other@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        person.email = "nodemote-other@example.com"
        await async_session.flush()

        await invitations.accept(async_session, raw_token=token, user=person)

        membership = await _membership(async_session, org, person)
        assert membership.role == OrganizationRole.ADMIN.value


@pytest.mark.asyncio
class TestTheRosterShowsWhoIsComing:
    async def test_pending_invitations_are_listed(self, async_session):
        """An invited person who has not accepted is invisible otherwise,
        which is how somebody gets invited twice."""
        org = await _org(async_session, "pending-list")
        await invitations.invite(
            async_session,
            organization_id=org.id,
            email="waiting@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )

        rows = await invitations.pending_for_organization(async_session, org.id)
        assert [r.email for r in rows] == ["waiting@example.com"]

    async def test_an_accepted_invitation_leaves_the_list(self, async_session):
        org = await _org(async_session, "pending-gone")
        joiner = await _user(async_session, "gone@example.com")
        _row, token = await invitations.invite(
            async_session,
            organization_id=org.id,
            email="gone@example.com",
            role=OrganizationRole.MEMBER.value,
            invited_by=None,
        )
        await invitations.accept(async_session, raw_token=token, user=joiner)

        assert await invitations.pending_for_organization(async_session, org.id) == []
