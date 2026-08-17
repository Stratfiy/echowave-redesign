"""The Google callback has to survive the accounts that already exist.

Two defects made "Sign in with Google" unusable, and neither was reachable
from the happy path a test would naturally write — a brand-new account took
different branches from a returning one, and only the returning one was broken.

The shape of the bug is worth keeping in mind rather than just the bug: one
``if/else`` was answering two unrelated questions at once ("does this person
have an organization" and "has this address been verified"), so an account that
answered them differently fell through a branch that bound nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from api.db.models import OrganizationModel, UserModel
from api.services.auth import google_oauth


@pytest.fixture
def no_default_model_config():
    """Skip the default-model step; it reaches an external service."""
    with patch(
        "api.services.auth.provisioning.create_user_configuration_with_mps_key",
        new=AsyncMock(return_value=None),
    ):
        yield


def _identity(email: str):
    return google_oauth.GoogleIdentity(
        subject="google-subject",
        email=email,
        email_verified=True,
        name="A Returning Customer",
        picture=None,
    )


async def _existing_account(session, slug: str, *, verified: bool, mfa: bool = False):
    """An account created before Google sign-in existed."""
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}", email=f"{slug}@example.com")
    session.add_all([org, user])
    await session.flush()
    user.selected_organization_id = org.id
    user.email_verified_at = datetime.now(UTC) if verified else None
    user.mfa_enabled = mfa
    await session.commit()
    return user


@pytest.mark.asyncio
class TestAnExistingAccountCanSignInWithGoogle:
    async def test_an_unverified_existing_account_gets_a_session(
        self, db_session, async_session, no_default_model_config
    ):
        """The regression that broke every pre-existing account.

        Every account predating email verification has ``email_verified_at``
        NULL. That branch marked the address verified and skipped the branch
        that bound ``organization`` and ``event``, so the next line raised
        UnboundLocalError — a 500 and a raw error page rather than a sign-in,
        on the *first* Google sign-in of every account that already existed.
        """
        user = await _existing_account(async_session, "unverified", verified=False)

        from api.routes import auth as auth_routes

        with patch.object(
            google_oauth,
            "complete_sign_in",
            new=AsyncMock(return_value=(_identity(user.email), None, None)),
        ):
            response = await auth_routes.google_callback(code="c", state="s")

        # A redirect carrying a token, not a 500.
        assert response.status_code == 303
        assert "/auth/google?token=" in response.headers["location"]

    async def test_a_verified_existing_account_still_signs_in(
        self, db_session, async_session, no_default_model_config
    ):
        """Guard against fixing one branch by breaking the other."""
        user = await _existing_account(async_session, "verified", verified=True)

        from api.routes import auth as auth_routes

        with patch.object(
            google_oauth,
            "complete_sign_in",
            new=AsyncMock(return_value=(_identity(user.email), None, None)),
        ):
            response = await auth_routes.google_callback(code="c", state="s")

        assert response.status_code == 303
        assert "/auth/google?token=" in response.headers["location"]


@pytest.mark.asyncio
class TestGoogleDoesNotBypassTheSecondFactor:
    async def test_an_mfa_account_is_sent_to_the_password_form(
        self, db_session, async_session, no_default_model_config
    ):
        """A second factor is a second factor whichever door you come through.

        The password path refuses to mint a session until the code is given.
        This path did not consult `mfa_enabled` at all, so anyone who could get
        Google to vouch for the address walked past it.
        """
        user = await _existing_account(async_session, "mfa-on", verified=True, mfa=True)

        from api.routes import auth as auth_routes

        with patch.object(
            google_oauth,
            "complete_sign_in",
            new=AsyncMock(return_value=(_identity(user.email), None, None)),
        ):
            response = await auth_routes.google_callback(code="c", state="s")

        location = response.headers["location"]
        assert "/auth/login?error=" in location
        assert "token=" not in location
