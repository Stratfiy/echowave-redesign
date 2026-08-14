"""Accounts somebody else creates for you.

Two doors existed before this — self-serve signup and Google first sign-in —
and both are the new user acting for themselves. So an owner could not add a
colleague, and staff could not stand up an account for a customer they had just
sold to. The team screen could only ever subtract.

The hard part is not the row, it is that the invited person has no password.
``login`` refuses any account whose ``password_hash`` is falsy, which is what
keeps a Google-only account from being reachable by guessing at a placeholder.
So the properties worth holding are about the credential, not the workflow:

**No password is ever invented.** The user row is created without one and stays
unreachable by password until its owner sets theirs. A generated temporary
password mailed out is a working credential sitting in an inbox for as long as
the mailbox exists.

**The code is never stored.** Salted hash only, with a TTL and an attempt
ceiling — six digits is a million guesses, and the ceiling is what makes it a
credential rather than a formality.

**An invitation cannot take over an account that already works.** Otherwise it
is a password reset for any address we can spell.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.db.models import AccountInvitationModel, OrganizationModel, UserModel
from api.services.auth import invitations


async def _user(async_session, slug: str, *, password_hash=None, email=None):
    user = UserModel(
        provider_id=f"user-{slug}",
        email=email or f"{slug}@example.com",
        password_hash=password_hash,
    )
    async_session.add(user)
    await async_session.flush()
    return user


async def _org(async_session, slug: str):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    async_session.add(org)
    await async_session.flush()
    return org


@pytest.mark.asyncio
class TestIssuing:
    async def test_a_code_is_returned_but_never_stored(self, db_session, async_session):
        """A challenge table holding plaintext codes is a way for anyone with
        database access to take any invited account."""
        import sqlalchemy as sa

        issued = await invitations.issue(
            async_session,
            email="new@example.com",
            organization_id=None,
            invited_by=None,
        )

        row = (
            await async_session.execute(sa.select(AccountInvitationModel))
        ).scalar_one()
        assert issued.code not in (row.code_hash, row.code_salt)
        assert len(row.code_hash) == 64

    async def test_the_address_is_lower_cased(self, db_session, async_session):
        """Addresses arrive from a form. "Alice@Example.com" and
        "alice@example.com" are one person, and storing both would let the
        second invitation silently miss the first."""
        issued = await invitations.issue(
            async_session,
            email="  Alice@Example.COM ",
            organization_id=None,
            invited_by=None,
        )
        assert issued.email == "alice@example.com"

    async def test_issuing_again_replaces_the_pending_invitation(
        self, db_session, async_session
    ):
        """A resend means one valid code, not two. The older one has usually
        been forwarded, filed, or left in a mailbox somebody else can read."""
        first = await invitations.issue(
            async_session,
            email="resend@example.com",
            organization_id=None,
            invited_by=None,
        )
        await invitations.issue(
            async_session,
            email="resend@example.com",
            organization_id=None,
            invited_by=None,
        )

        with pytest.raises(invitations.InvitationError):
            await invitations.accept(
                async_session, email="resend@example.com", code=first.code
            )

    async def test_an_account_that_can_already_sign_in_is_refused(
        self, db_session, async_session
    ):
        """The property that stops this being a password reset for any address
        we can spell. Someone who can already sign in does not need a way to
        set a password, and issuing one would be a way to take their account."""
        await _user(
            async_session,
            "existing",
            password_hash="argon2-something",
            email="has@example.com",
        )

        with pytest.raises(invitations.InvitationError):
            await invitations.issue(
                async_session,
                email="has@example.com",
                organization_id=None,
                invited_by=None,
            )

    async def test_an_account_with_no_password_can_still_be_invited(
        self, db_session, async_session
    ):
        """A Google-only account, or one from an earlier unaccepted
        invitation. Neither can sign in with a password, so neither is at risk
        from being offered a way to set one."""
        await _user(async_session, "google", password_hash=None, email="g@example.com")

        issued = await invitations.issue(
            async_session,
            email="g@example.com",
            organization_id=None,
            invited_by=None,
        )
        assert issued.is_new_user is False

    async def test_a_nonsense_address_is_refused(self, db_session, async_session):
        for bad in ("", "   ", "not-an-address"):
            with pytest.raises(invitations.InvitationError):
                await invitations.issue(
                    async_session, email=bad, organization_id=None, invited_by=None
                )


@pytest.mark.asyncio
class TestAccepting:
    async def test_the_right_code_reports_what_it_was_for(
        self, db_session, async_session
    ):
        org = await _org(async_session, "accept")
        issued = await invitations.issue(
            async_session,
            email="join@example.com",
            organization_id=org.id,
            invited_by=None,
            role="admin",
        )

        accepted = await invitations.accept(
            async_session, email="join@example.com", code=issued.code
        )
        assert accepted.organization_id == org.id
        assert accepted.role == "admin"

    async def test_a_wrong_code_is_refused(self, db_session, async_session):
        await invitations.issue(
            async_session,
            email="wrong@example.com",
            organization_id=None,
            invited_by=None,
        )
        with pytest.raises(invitations.InvitationError):
            await invitations.accept(
                async_session, email="wrong@example.com", code="000000"
            )

    async def test_repeated_wrong_codes_cancel_the_invitation(
        self, db_session, async_session
    ):
        """Six digits is a million guesses and an invitation lives for days.
        Without the ceiling the code is a formality rather than a credential."""
        issued = await invitations.issue(
            async_session,
            email="brute@example.com",
            organization_id=None,
            invited_by=None,
        )

        for _ in range(invitations.MAX_ATTEMPTS):
            with pytest.raises(invitations.InvitationError):
                await invitations.accept(
                    async_session, email="brute@example.com", code="000000"
                )

        # Even the correct code no longer works.
        with pytest.raises(invitations.InvitationError):
            await invitations.accept(
                async_session, email="brute@example.com", code=issued.code
            )

    async def test_an_expired_invitation_is_refused(self, db_session, async_session):
        issued = await invitations.issue(
            async_session,
            email="stale@example.com",
            organization_id=None,
            invited_by=None,
        )
        later = datetime.now(UTC) + timedelta(
            hours=invitations.INVITATION_TTL_HOURS + 1
        )

        with pytest.raises(invitations.InvitationError):
            await invitations.accept(
                async_session, email="stale@example.com", code=issued.code, now=later
            )

    async def test_a_code_cannot_be_used_twice(self, db_session, async_session):
        """The one that matters after the account is live: a replayed
        acceptance must not reset the password of an account in use."""
        issued = await invitations.issue(
            async_session,
            email="replay@example.com",
            organization_id=None,
            invited_by=None,
        )
        await invitations.accept(
            async_session, email="replay@example.com", code=issued.code
        )

        with pytest.raises(invitations.InvitationError):
            await invitations.accept(
                async_session, email="replay@example.com", code=issued.code
            )

    async def test_accepting_with_nothing_pending_is_refused(
        self, db_session, async_session
    ):
        with pytest.raises(invitations.InvitationError):
            await invitations.accept(
                async_session, email="nobody@example.com", code="123456"
            )

    async def test_one_invitation_cannot_be_accepted_with_anothers_code(
        self, db_session, async_session
    ):
        """Codes are per address, and the lookup is by address. A code that
        works for any pending invitation would make six digits a master key."""
        mine = await invitations.issue(
            async_session, email="a@example.com", organization_id=None, invited_by=None
        )
        await invitations.issue(
            async_session, email="b@example.com", organization_id=None, invited_by=None
        )

        with pytest.raises(invitations.InvitationError):
            await invitations.accept(
                async_session, email="b@example.com", code=mine.code
            )


class TestTheEmail:
    def _issued(self):
        return invitations.IssuedInvitation(
            email="new@example.com",
            code="123456",
            expires_at=datetime.now(UTC),
            organization_id=None,
            is_new_user=True,
        )

    def test_it_carries_the_code_and_says_what_to_do(self):
        body = invitations.notice_body(self._issued(), invited_by_email=None)
        assert "123456" in body
        assert "password" in body.lower()

    def test_it_names_the_inviter_when_there_is_one(self):
        """An unexplained code from a product somebody has never heard of
        reads as phishing, and ignoring phishing is the correct response."""
        body = invitations.notice_body(
            self._issued(), invited_by_email="owner@example.com"
        )
        assert "owner@example.com" in body

    def test_it_tells_the_reader_what_to_do_if_it_was_not_them(self):
        body = invitations.notice_body(self._issued(), invited_by_email=None)
        assert "not expecting" in body.lower()


@pytest.mark.asyncio
class TestTheWholeRoundTrip:
    """Invite, then accept, through the real routes.

    The service-level tests above prove the code rules. This proves the thing
    an owner actually cares about: that the person they invited can sign in
    afterwards, is in the right organization, and has the role they were given.
    """

    async def _client(self, user, *, dependency):
        from contextlib import asynccontextmanager

        from httpx import ASGITransport, AsyncClient

        from api.app import app

        @asynccontextmanager
        async def _ctx():
            async def _override():
                return user

            app.dependency_overrides[dependency] = _override
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    yield client
            finally:
                app.dependency_overrides.pop(dependency, None)

        return _ctx()

    async def test_an_invited_colleague_can_sign_in_and_lands_in_the_org(
        self, db_session, async_session, monkeypatch
    ):
        from api.db import db_client
        from api.db.models import OrganizationMembershipModel
        from api.services.auth.depends import get_user_with_selected_organization

        # Mail is not the subject here, and an unconfigured SMTP would make
        # this a test of the mailer instead.
        sent = {}

        async def _fake_send(**kwargs):
            from api.services.messaging.email import SendResult

            sent.update(kwargs)
            return SendResult(ok=True)

        monkeypatch.setattr("api.routes.organization_members.send_email", _fake_send)

        org = await _org(async_session, "roundtrip")
        owner = await _user(
            async_session,
            "owner-rt",
            password_hash="hashed",
            email="owner-rt@example.com",
        )
        owner.selected_organization_id = org.id
        # A real OWNER membership rather than a bypassed check: the route is
        # Owner-only, and a test that overrode the role check would not notice
        # if it stopped being enforced.
        async_session.add(
            OrganizationMembershipModel(
                user_id=owner.id, organization_id=org.id, role="owner"
            )
        )
        await async_session.commit()

        async with await self._client(
            owner, dependency=get_user_with_selected_organization
        ) as client:
            invite = await client.post(
                "/api/v1/organizations/members",
                json={"email": "colleague@example.com", "role": "admin"},
            )

        assert invite.status_code == 200, invite.text
        assert invite.json()["is_new_user"] is True

        # The code exists only in the email that was "sent".
        code = "".join(c for c in sent["body_text"] if c.isdigit())[:6]

        async with await self._client(
            owner, dependency=get_user_with_selected_organization
        ) as client:
            accepted = await client.post(
                "/api/v1/auth/accept-invitation",
                json={
                    "email": "colleague@example.com",
                    "code": code,
                    "password": "a-long-enough-password",
                },
            )

        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["user"]["organization_id"] == org.id

        # And they can actually sign in, which is the whole point.
        created = await db_client.get_user_by_email("colleague@example.com")
        assert created is not None and created.password_hash
        membership = await db_client.get_membership(created.id, org.id)
        assert membership is not None and membership.role == "admin"

    async def test_a_member_cannot_invite(self, db_session, async_session):
        """Adding somebody grants standing access to every workflow, recording
        and number the account holds. That is not a member's decision."""
        from api.db.models import OrganizationMembershipModel
        from api.services.auth.depends import get_user_with_selected_organization

        org = await _org(async_session, "notowner")
        member = await _user(
            async_session, "member-rt", password_hash="hashed", email="m@example.com"
        )
        member.selected_organization_id = org.id
        async_session.add(
            OrganizationMembershipModel(
                user_id=member.id, organization_id=org.id, role="member"
            )
        )
        await async_session.commit()

        async with await self._client(
            member, dependency=get_user_with_selected_organization
        ) as client:
            response = await client.post(
                "/api/v1/organizations/members",
                json={"email": "someone@example.com"},
            )

        assert response.status_code == 403

    async def test_no_membership_exists_before_the_invitation_is_accepted(
        self, db_session, async_session
    ):
        """An unaccepted invitation must leave nothing behind. A membership
        row created at invite time shows on the roster as a member nobody can
        contact and nobody can explain."""
        from api.db import db_client

        org = await _org(async_session, "pending")
        await invitations.issue(
            async_session,
            email="pending@example.com",
            organization_id=org.id,
            invited_by=None,
            role="member",
        )
        await async_session.commit()

        assert await db_client.get_user_by_email("pending@example.com") is None


@pytest.mark.asyncio
class TestTheFounderOwnsWhatTheyCreated:
    """Whoever creates an organization is its owner.

    That reads as obvious and was not the behaviour.
    ``add_user_to_organization`` defaults to MEMBER, and provisioning took the
    default — so everybody who signed up became a *member* of the organization
    they had just created, on an account nobody else was in. They could not
    change a role, remove anybody, or invite anybody.

    Nothing looked broken. The role machinery was enforced correctly the whole
    time; the founder simply never got the role, so it read as roles doing
    nothing rather than as one missing assignment.
    """

    async def test_signing_up_makes_you_the_owner(self, db_session, async_session):
        from api.db import db_client
        from api.services.auth.provisioning import provision_new_account

        user = await _user(async_session, "founder", email="founder@example.com")
        await async_session.commit()

        organization = await provision_new_account(user)

        membership = await db_client.get_membership(user.id, organization.id)
        assert membership is not None
        assert membership.role == "owner"

    async def test_provisioning_again_does_not_promote_anybody(
        self, db_session, async_session
    ):
        """Re-provisioning happens on a Stack Auth login re-confirming team
        membership. It must never quietly promote a member to owner."""
        from api.db import db_client
        from api.db.models import OrganizationMembershipModel
        from api.services.auth.provisioning import provision_new_account

        founder = await _user(async_session, "already", email="already@example.com")
        await async_session.commit()
        organization = await provision_new_account(founder)

        joiner = await _user(async_session, "joiner", email="joiner@example.com")
        async_session.add(
            OrganizationMembershipModel(
                user_id=joiner.id, organization_id=organization.id, role="member"
            )
        )
        await async_session.commit()

        # The organization already exists, so this is the "joining" path.
        await provision_new_account(founder)

        assert (
            await db_client.get_membership(joiner.id, organization.id)
        ).role == "member"

    async def test_the_owner_can_then_actually_invite(
        self, db_session, async_session, monkeypatch
    ):
        """The two halves together. Being owner is what makes the invite route
        reachable, and before this the founder never was one — so the feature
        was unreachable for every account that had ever signed up."""
        from api.services.auth.depends import get_user_with_selected_organization
        from api.services.auth.provisioning import provision_new_account

        async def _fake_send(**kwargs):
            from api.services.messaging.email import SendResult

            return SendResult(ok=True)

        monkeypatch.setattr("api.routes.organization_members.send_email", _fake_send)

        founder = await _user(async_session, "invites", email="invites@example.com")
        await async_session.commit()
        organization = await provision_new_account(founder)
        founder.selected_organization_id = organization.id
        await async_session.commit()

        async with await TestTheWholeRoundTrip()._client(
            founder, dependency=get_user_with_selected_organization
        ) as client:
            response = await client.post(
                "/api/v1/organizations/members",
                json={"email": "invited-by-founder@example.com"},
            )

        assert response.status_code == 200, response.text
