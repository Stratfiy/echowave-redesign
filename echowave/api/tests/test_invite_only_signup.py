"""INVITE-1 (KAN-273): with invite-only on, no code means no account.

Service tests run against the real test database (the claim is one
conditional UPDATE, so its behaviour is the SQL, not a mock). Route tests
check that a refusal creates nothing and a good code redeems once.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.routes.auth as auth_routes
from api import constants
from api.services.auth import signup_invites


@pytest.fixture
def invite_only(monkeypatch):
    monkeypatch.setattr(constants, "INVITE_ONLY_SIGNUP_ENABLED", True)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")


@pytest.fixture
def invites_off(monkeypatch):
    monkeypatch.setattr(constants, "INVITE_ONLY_SIGNUP_ENABLED", False)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")


async def _staff(db_session):
    user, _ = await db_session.get_or_create_user_by_provider_id("staff-invites")
    return user


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_codes_are_normalised_the_way_people_type_them():
    assert signup_invites.normalise(" abcd-2345 ") == "ABCD2345"
    assert signup_invites.normalise("abcd 2345") == "ABCD2345"
    assert signup_invites.normalise(None) == ""
    assert signup_invites.display("ABCD2345") == "ABCD-2345"


def test_generated_codes_avoid_ambiguous_characters():
    for _ in range(200):
        code = signup_invites.generate_code()
        assert len(code) == signup_invites.CODE_LENGTH
        assert not set(code) & set("01OI")


# ---------------------------------------------------------------------------
# claim(): the table
# ---------------------------------------------------------------------------


async def test_no_code_is_refused_when_invite_only(db_session, invite_only):
    with pytest.raises(signup_invites.InviteRefused) as caught:
        await signup_invites.claim(None, "a@example.com")
    assert str(caught.value) == signup_invites.MISSING


async def test_no_code_passes_when_invites_are_off(db_session, invites_off):
    assert await signup_invites.claim(None, "a@example.com") is None
    assert await signup_invites.claim("NOPE-NOPE", "a@example.com") is None


async def test_unknown_code_is_refused(db_session, invite_only):
    with pytest.raises(signup_invites.InviteRefused) as caught:
        await signup_invites.claim("ZZZZ-ZZZZ", "a@example.com")
    assert str(caught.value) == signup_invites.UNKNOWN


async def test_a_good_code_admits_one_account_then_reads_used(db_session, invite_only):
    staff = await _staff(db_session)
    [row] = await signup_invites.mint(count=1, created_by_user_id=staff.id)
    typed = signup_invites.display(row.code).lower()  # as a person would type it

    first = await signup_invites.claim(typed, "a@example.com")
    assert first is not None and first.invite_id == row.id

    with pytest.raises(signup_invites.InviteRefused) as caught:
        await signup_invites.claim(typed, "b@example.com")
    assert str(caught.value) == signup_invites.USED


async def test_a_group_code_admits_max_uses_accounts(db_session, invite_only):
    staff = await _staff(db_session)
    [row] = await signup_invites.mint(
        count=1, created_by_user_id=staff.id, max_uses=2, note="clinic group"
    )
    assert await signup_invites.claim(row.code, "a@example.com")
    assert await signup_invites.claim(row.code, "b@example.com")
    with pytest.raises(signup_invites.InviteRefused):
        await signup_invites.claim(row.code, "c@example.com")


async def test_an_email_pinned_code_refuses_another_address(db_session, invite_only):
    staff = await _staff(db_session)
    [row] = await signup_invites.mint(
        count=1, created_by_user_id=staff.id, email="Owner@Clinic.in"
    )
    with pytest.raises(signup_invites.InviteRefused) as caught:
        await signup_invites.claim(row.code, "someone@else.in")
    assert str(caught.value) == signup_invites.WRONG_EMAIL
    assert await signup_invites.claim(row.code, "owner@clinic.in")


async def test_expired_and_revoked_codes_are_refused(db_session, invite_only):
    staff = await _staff(db_session)
    [expired] = await signup_invites.mint(
        count=1,
        created_by_user_id=staff.id,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    with pytest.raises(signup_invites.InviteRefused) as caught:
        await signup_invites.claim(expired.code, "a@example.com")
    assert str(caught.value) == signup_invites.EXPIRED

    [revoked] = await signup_invites.mint(count=1, created_by_user_id=staff.id)
    assert await signup_invites.revoke(revoked.id) is True
    assert await signup_invites.revoke(revoked.id) is False
    with pytest.raises(signup_invites.InviteRefused):
        await signup_invites.claim(revoked.code, "a@example.com")


async def test_mint_limits_and_listing(db_session, invite_only):
    staff = await _staff(db_session)
    with pytest.raises(ValueError):
        await signup_invites.mint(count=0, created_by_user_id=staff.id)
    with pytest.raises(ValueError):
        await signup_invites.mint(count=2, created_by_user_id=staff.id, email="x@y.in")

    rows = await signup_invites.mint(count=5, created_by_user_id=staff.id, note="batch")
    assert len({r.code for r in rows}) == 5

    claim = await signup_invites.claim(rows[0].code, "a@example.com")
    await signup_invites.record_redemption(
        claim,
        email="A@example.com",
        user_id=staff.id,
        organization_id=None,
        door="password",
    )
    listed = {i["id"]: i for i in await signup_invites.list_invites()}
    assert listed[rows[0].id]["uses"] == 1
    assert listed[rows[0].id]["redemptions"][0]["email"] == "a@example.com"
    assert listed[rows[0].id]["code"] == signup_invites.display(rows[0].code)


# ---------------------------------------------------------------------------
# The password door: a refused code creates nothing
# ---------------------------------------------------------------------------


def _signup_app(monkeypatch):
    monkeypatch.setattr(auth_routes, "ENABLE_SIGNUP", True)
    monkeypatch.setattr(auth_routes.agreements, "SIGNUP_AGREEMENTS", ())
    monkeypatch.setattr(
        auth_routes.db_client, "get_user_by_email", AsyncMock(return_value=None)
    )
    create = AsyncMock()
    monkeypatch.setattr(auth_routes.db_client, "create_user_with_email", create)
    app = FastAPI()
    app.include_router(auth_routes.router)
    return TestClient(app), create


def test_password_signup_without_a_code_creates_nothing(monkeypatch, invite_only):
    monkeypatch.setattr(
        auth_routes.signup_invites,
        "claim",
        AsyncMock(side_effect=signup_invites.InviteRefused(signup_invites.MISSING)),
    )
    client, create = _signup_app(monkeypatch)

    response = client.post(
        "/auth/signup",
        json={"email": "a@example.com", "password": "password123"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": signup_invites.MISSING}
    create.assert_not_awaited()


def test_password_signup_passes_the_code_and_email_to_claim(monkeypatch, invite_only):
    claim = AsyncMock(side_effect=signup_invites.InviteRefused(signup_invites.USED))
    monkeypatch.setattr(auth_routes.signup_invites, "claim", claim)
    client, _ = _signup_app(monkeypatch)

    client.post(
        "/auth/signup",
        json={
            "email": "a@example.com",
            "password": "password123",
            "invite_code": "abcd-2345",
        },
    )

    claim.assert_awaited_once_with("abcd-2345", "a@example.com")


def test_an_existing_email_is_a_409_before_any_use_is_spent(monkeypatch, invite_only):
    claim = AsyncMock()
    monkeypatch.setattr(auth_routes.signup_invites, "claim", claim)
    client, _ = _signup_app(monkeypatch)
    monkeypatch.setattr(
        auth_routes.db_client,
        "get_user_by_email",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    )

    response = client.post(
        "/auth/signup",
        json={"email": "a@example.com", "password": "password123", "invite_code": "X"},
    )

    assert response.status_code == 409
    claim.assert_not_awaited()


# ---------------------------------------------------------------------------
# The Google door carries the code through the signed state
# ---------------------------------------------------------------------------


def test_the_google_state_carries_the_invite_code(monkeypatch):
    from api.services.auth import google_oauth as g

    state = g._issue_state(nonce="n", next_path=None, invite_code="ABCD2345")
    assert g.invite_code_from_state(state) == "ABCD2345"
    assert g.invite_code_from_state(g._issue_state(nonce="n", next_path=None)) is None


def test_the_flag_is_reported_so_the_signup_screen_can_ask_for_a_code(invite_only):
    from api.services import features

    assert features.public()["invite_only_signup"] is True
