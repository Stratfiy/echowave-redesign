"""Launch stream `shell`: the door (screens 01-02), the landing and Stop.

Service tests run against the test database, because "submitting twice
creates one request" is a property of the unique index, not of Python.
Route tests check each flag hides its routes while off.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api import constants
from api.services.auth import signup_invites
from api.services.shell import early_access, languages, onboarding
from api.services.workflow import decibyl, reply_draft, reply_stop


@pytest.fixture
def flags_off(monkeypatch):
    for name in (
        "EARLY_ACCESS_ENABLED",
        "FIRST_TASK_ONBOARDING_ENABLED",
        "CHAT_SHELL_ENABLED",
        "SHELL_MOBILE_ENABLED",
    ):
        monkeypatch.setattr(constants, name, False)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")


@pytest.fixture
def early_on(monkeypatch, flags_off):
    monkeypatch.setattr(constants, "EARLY_ACCESS_ENABLED", True)


@pytest.fixture
def onboarding_on(monkeypatch, flags_off):
    monkeypatch.setattr(constants, "FIRST_TASK_ONBOARDING_ENABLED", True)


@pytest.fixture
def chat_on(monkeypatch, flags_off):
    monkeypatch.setattr(constants, "CHAT_SHELL_ENABLED", True)


async def _staff(db_session):
    user, _ = await db_session.get_or_create_user_by_provider_id("staff-shell")
    return user


# ---------------------------------------------------------------------------
# Flags are registered and off by default
# ---------------------------------------------------------------------------


def test_every_shell_flag_is_registered_described_and_off(flags_off):
    from api.services import features

    for name in ("early_access", "first_task_onboarding", "chat_shell", "shell_mobile"):
        assert name in features.FLAGS
        assert name in features.DESCRIPTIONS
        assert features.is_on(name) is False


def test_languages_list_native_names_and_voice():
    assert languages.BY_CODE["ta"].native == "தமிழ்"
    assert languages.BY_CODE["hi"].english == "Hindi"
    # A text-only language is still offered, and says so.
    assert languages.BY_CODE["ur"].voice is False
    assert not languages.is_supported("xx")
    assert not languages.is_supported(None)


# ---------------------------------------------------------------------------
# Screen 01: the waitlist
# ---------------------------------------------------------------------------


async def test_submitting_twice_creates_one_request(db_session, early_on):
    first = await early_access.join(email="Asha@Example.in", language="ta")
    again = await early_access.join(email=" asha@example.in ", language="en")
    assert (first.state, first.created) == (early_access.WAITLISTED, True)
    assert (again.state, again.created) == (early_access.WAITLISTED, False)
    assert await early_access.count() == 1


async def test_a_registered_address_is_pointed_at_sign_in(db_session, early_on):
    with patch.object(
        early_access.db_client,
        "get_user_by_email",
        AsyncMock(return_value=SimpleNamespace(id=1)),
    ):
        result = await early_access.join(email="owner@clinic.in", language="en")
    assert result.state == early_access.ALREADY_REGISTERED
    assert await early_access.count() == 0


async def test_an_invited_address_is_told_to_check_its_mail(db_session, early_on):
    staff = await _staff(db_session)
    await signup_invites.mint(
        count=1, created_by_user_id=staff.id, email="invited@clinic.in"
    )
    result = await early_access.join(email="invited@clinic.in", language="en")
    assert result.state == early_access.INVITED
    assert await early_access.count() == 0


async def test_fields_a_person_can_fix_are_refused_with_a_reason(db_session, early_on):
    with pytest.raises(early_access.Invalid):
        await early_access.join(email="a@b.in", language="klingon")
    with pytest.raises(early_access.Invalid):
        await early_access.join(email="a@b.in", language="en", phone="call me")
    assert await early_access.count() == 0


def test_bound_emails_are_masked():
    assert early_access.mask_email("nithya@example.com") == "n*****@example.com"
    assert early_access.mask_email("a@x.in") == "*@x.in"
    assert early_access.mask_email(None) is None


# ---------------------------------------------------------------------------
# Screen 01: what an invitation link says
# ---------------------------------------------------------------------------


async def test_invite_status_reads_every_state_without_spending(db_session, early_on):
    staff = await _staff(db_session)
    [good] = await signup_invites.mint(
        count=1,
        created_by_user_id=staff.id,
        email="owner@clinic.in",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    status = await early_access.invite_status(signup_invites.display(good.code))
    assert status["state"] == early_access.VALID
    assert status["email_hint"] == "o****@clinic.in"
    assert status["expires_at"]
    assert status["code"] == signup_invites.display(good.code)
    # Reading it twice spends nothing: the bound address can still claim it.
    await early_access.invite_status(good.code)
    assert await signup_invites.claim(good.code, "owner@clinic.in")
    used = await early_access.invite_status(good.code)
    assert used["state"] == early_access.USED and "code" not in used

    [expired] = await signup_invites.mint(
        count=1,
        created_by_user_id=staff.id,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    assert (await early_access.invite_status(expired.code))[
        "state"
    ] == early_access.EXPIRED

    [revoked] = await signup_invites.mint(count=1, created_by_user_id=staff.id)
    await signup_invites.revoke(revoked.id)
    assert (await early_access.invite_status(revoked.code))[
        "state"
    ] == early_access.REVOKED

    assert (await early_access.invite_status("ZZZZ-ZZZZ"))[
        "state"
    ] == early_access.INVALID
    assert (await early_access.invite_status(""))["state"] == early_access.INVALID


async def test_public_routes_are_a_404_while_off(
    test_client_factory, db_session, flags_off
):
    user, _ = await db_session.get_or_create_user_by_provider_id("anon-shell")
    async with test_client_factory(user) as client:
        joined = await client.post(
            "/api/v1/public/early-access/waitlist",
            json={"email": "a@b.in", "language": "en"},
        )
        invite = await client.get("/api/v1/public/early-access/invites/ABCD2345")
    assert joined.status_code == 404
    assert invite.status_code == 404


async def test_public_routes_answer_while_on(test_client_factory, db_session, early_on):
    user, _ = await db_session.get_or_create_user_by_provider_id("anon-shell-on")
    async with test_client_factory(user) as client:
        first = await client.post(
            "/api/v1/public/early-access/waitlist",
            json={
                "email": "route@b.in",
                "language": "hi",
                "first_task": "Plan my week",
            },
        )
        second = await client.post(
            "/api/v1/public/early-access/waitlist",
            json={"email": "route@b.in", "language": "hi"},
        )
        bad = await client.post(
            "/api/v1/public/early-access/waitlist",
            json={"email": "route2@b.in", "language": "zz"},
        )
        invite = await client.get("/api/v1/public/early-access/invites/NOPE")
        langs = await client.get("/api/v1/public/early-access/languages")
    assert first.json() == {"state": "waitlisted", "created": True}
    assert second.json() == {"state": "waitlisted", "created": False}
    assert bad.status_code == 422
    assert invite.json()["state"] == "invalid"
    assert {
        "code": "ta",
        "native": "தமிழ்",
        "english": "Tamil",
        "voice": True,
    } in langs.json()


# ---------------------------------------------------------------------------
# Screen 02: onboarding and the landing
# ---------------------------------------------------------------------------


async def test_a_detected_timezone_is_never_saved_unconfirmed(
    db_session, onboarding_on
):
    user, _ = await db_session.get_or_create_user_by_provider_id("tz-person")
    with pytest.raises(onboarding.Invalid):
        await onboarding.save(
            user.id, language="ta", timezone="Asia/Kolkata", timezone_confirmed=False
        )
    with pytest.raises(onboarding.Invalid):
        await onboarding.save(
            user.id, language="ta", timezone="Mars/Olympus", timezone_confirmed=True
        )
    with pytest.raises(onboarding.Invalid):
        await onboarding.save(
            user.id, language="zz", timezone="Asia/Kolkata", timezone_confirmed=True
        )
    assert await onboarding.get(user.id) == onboarding.EMPTY


async def test_new_people_land_on_onboarding_then_chat(db_session, onboarding_on):
    user, _ = await db_session.get_or_create_user_by_provider_id("new-person")
    assert await onboarding.landing(user.id, None) == onboarding.ONBOARDING_PATH

    saved = await onboarding.save(
        user.id,
        language="ta",
        timezone="Asia/Kolkata",
        timezone_confirmed=True,
        preferred_name="  Meena ",
    )
    assert saved.language == "ta" and saved.timezone_confirmed
    assert saved.preferred_name == "Meena"
    # Saved but no task yet: still onboarding.
    assert await onboarding.landing(user.id, None) == onboarding.ONBOARDING_PATH

    done = await onboarding.save(
        user.id,
        language="ta",
        timezone="Asia/Kolkata",
        timezone_confirmed=True,
        complete=True,
    )
    assert done.completed
    # Chat, never the build-an-agent journey, whatever the agent count.
    assert await onboarding.landing(user.id, None) == onboarding.CHAT_PATH


async def test_skipping_still_lands_in_chat(db_session, onboarding_on):
    user, _ = await db_session.get_or_create_user_by_provider_id("skipper")
    state = await onboarding.skip(user.id)
    assert state.completed and state.language is None
    assert await onboarding.landing(user.id, None) == onboarding.CHAT_PATH


async def test_landing_keeps_the_old_rule_while_off(db_session, flags_off):
    user, _ = await db_session.get_or_create_user_by_provider_id("old-rule")
    assert await onboarding.landing(user.id, None) is None


async def test_one_member_answering_changes_nothing_for_another(
    db_session, onboarding_on
):
    first, _ = await db_session.get_or_create_user_by_provider_id("member-a")
    second, _ = await db_session.get_or_create_user_by_provider_id("member-b")
    await onboarding.save(
        first.id,
        language="hi",
        timezone="Asia/Kolkata",
        timezone_confirmed=True,
        complete=True,
    )
    assert await onboarding.get(second.id) == onboarding.EMPTY
    assert await onboarding.landing(second.id, None) == onboarding.ONBOARDING_PATH


async def test_onboarding_routes(test_client_factory, db_session, onboarding_on):
    user, _ = await db_session.get_or_create_user_by_provider_id("route-person")
    async with test_client_factory(user) as client:
        landing = await client.get("/api/v1/shell/landing")
        read = await client.get("/api/v1/shell/onboarding")
        refused = await client.put(
            "/api/v1/shell/onboarding",
            json={
                "language": "ta",
                "timezone": "Asia/Kolkata",
                "timezone_confirmed": False,
            },
        )
        saved = await client.put(
            "/api/v1/shell/onboarding",
            json={
                "language": "ta",
                "timezone": "Asia/Kolkata",
                "timezone_confirmed": True,
                "complete": True,
            },
        )
        after = await client.get("/api/v1/shell/landing")
    assert landing.json() == {"path": "/welcome"}
    assert read.json()["enabled"] is True and read.json()["completed"] is False
    assert any(lang["native"] == "தமிழ்" for lang in read.json()["languages"])
    assert refused.status_code == 422
    assert saved.json()["completed"] is True
    assert after.json() == {"path": "/overview"}


async def test_onboarding_routes_are_hidden_while_off(
    test_client_factory, db_session, flags_off
):
    user, _ = await db_session.get_or_create_user_by_provider_id("route-off")
    async with test_client_factory(user) as client:
        landing = await client.get("/api/v1/shell/landing")
        read = await client.get("/api/v1/shell/onboarding")
        skip = await client.post("/api/v1/shell/onboarding/skip")
    assert landing.json() == {"path": None}
    assert read.status_code == 404
    assert skip.status_code == 404


# ---------------------------------------------------------------------------
# Screen 04: Stop keeps the partial reply
# ---------------------------------------------------------------------------


def test_the_stop_note_is_keyed_like_the_draft():
    assert reply_stop.key(7) == "reply_stop:7:assistant"
    assert reply_stop.key(7, "t-1") == "reply_stop:7:assistant:t-1"
    assert reply_draft.key(7, thread_id="t-1") == "reply_draft:7:assistant:t-1"


async def test_stop_route_is_hidden_while_off(
    test_client_factory, db_session, flags_off
):
    user, _ = await db_session.get_or_create_user_by_provider_id("stop-off")
    async with test_client_factory(user) as client:
        response = await client.post("/api/v1/shell/chat/stop", json={})
    assert response.status_code in (400, 404)


def _session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _turn_patches(record: AsyncMock, requested: AsyncMock):
    return (
        patch.object(decibyl, "build_context", new=AsyncMock(return_value="ctx")),
        patch.object(decibyl, "office_context", new=AsyncMock(return_value="")),
        patch.object(decibyl.db_client, "agent_events", new=AsyncMock(return_value=[])),
        patch.object(decibyl.db_client, "async_session", return_value=_session()),
        patch(
            "api.services.agent_builder.settings.resolve_model",
            new=AsyncMock(
                return_value=SimpleNamespace(provider="openai", model="m", api_key="k")
            ),
        ),
        patch.object(decibyl.agent_timeline, "record", new=record),
        patch.object(decibyl.reply_draft, "clear", new=AsyncMock()),
        patch.object(decibyl.reply_draft, "set_draft", new=AsyncMock()),
        patch.object(decibyl.reply_stop, "clear", new=AsyncMock()),
        patch.object(decibyl.reply_stop, "requested", new=requested),
        patch.object(decibyl, "tools_for", new=AsyncMock(return_value=None)),
    )


async def _stream_with_chunks(**kwargs):
    from api.services.agent_builder.client import ModelReply

    on_text = kwargs["on_text"]
    await on_text("The first half of")
    # Time moves on between chunks; the throttle is a quarter second.
    await on_text("The first half of an answer that")
    return ModelReply(text="The first half of an answer that went on to finish.")


async def test_stop_records_the_partial_reply_as_stopped(chat_on):
    record = AsyncMock()
    requested = AsyncMock(return_value=True)
    with ExitStack() as stack:
        for p in _turn_patches(record, requested):
            stack.enter_context(p)
        stack.enter_context(
            patch("api.services.agent_builder.client.stream", new=_stream_with_chunks)
        )
        body = await decibyl.answer(7, "tell me everything", thread_id="t-1")
    assert body == "The first half of"
    payload = record.await_args.kwargs["payload"]
    assert payload["stopped"] is True
    assert payload["body"] == "The first half of"
    assert "failed" not in payload


async def test_without_the_flag_a_stop_note_is_ignored(flags_off):
    record = AsyncMock()
    requested = AsyncMock(return_value=True)
    with ExitStack() as stack:
        for p in _turn_patches(record, requested):
            stack.enter_context(p)
        stack.enter_context(
            patch("api.services.agent_builder.client.stream", new=_stream_with_chunks)
        )
        body = await decibyl.answer(7, "tell me everything")
    assert body == "The first half of an answer that went on to finish."
    assert "stopped" not in record.await_args.kwargs["payload"]
    requested.assert_not_awaited()


async def test_a_failed_turn_is_marked_failed_not_finished(flags_off):
    record = AsyncMock()

    async def broken(**kwargs):
        raise RuntimeError("vendor down")

    with ExitStack() as stack:
        for p in _turn_patches(record, AsyncMock(return_value=False)):
            stack.enter_context(p)
        stack.enter_context(
            patch("api.services.agent_builder.client.stream", new=broken)
        )
        await decibyl.answer(7, "hello")
    assert record.await_args.kwargs["payload"]["failed"] is True


def test_sources_read_names_the_reading_that_could_not_run():
    sources = decibyl.sources_read(
        bots=2, facts=0, knowledge={"status": "unavailable", "chunks": []}
    )
    assert [s["kind"] for s in sources] == ["team", "memory", "knowledge"]
    assert sources[2]["status"] == "unavailable"
    read = decibyl.sources_read(
        bots=1,
        facts=3,
        knowledge={
            "chunks": [
                {"document_name": "Price list.pdf", "text": "x"},
                {"document_name": "Price list.pdf", "text": "y"},
            ]
        },
    )
    assert read[2]["status"] == "read"
    assert read[2]["documents"] == ["Price list.pdf"]
    assert read[2]["detail"] == "2 passages"
