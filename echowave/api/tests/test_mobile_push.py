"""Push to the native app (MOBILE.md; flag ``mobile_push``).

Done when: the routes are not there while the flag is off; a phone's token is
kept for its owner only, updated rather than duplicated, refused when it is
not Expo's, moved when someone else signs in on the same phone, and revoked
on sign-out; Expo is asked once per batch and a ``DeviceNotRegistered`` answer
revokes the phone; the push channel reaches browsers and phones together, and
the app alone for its own notices (never by email); a reply, a card waiting
for an OK and the end of a call placed for the person reach their phone once,
with the generic lock-screen text while private previews are on; a phone
makes push "available" without browser keys; and the migration goes up and
down cleanly.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import inspect, select, text

from api import constants
from api.db import db_client
from api.db.mobile_push_models import MobilePushTokenModel
from api.services.identity import mobile_push, notifications, push
from api.tests.identity_support import client_as, flags, team  # noqa: F401

TOKEN = "ExponentPushToken[abcdefghijklmnop{}]"


@pytest.fixture
def on(monkeypatch):
    flags(monkeypatch, "MOBILE_PUSH_ENABLED", "IDENTITY_NOTIFICATIONS_ENABLED")
    # No browser keys: the app alone must be enough.
    monkeypatch.setattr(constants, "VAPID_PUBLIC_KEY", "")
    monkeypatch.setattr(constants, "VAPID_PRIVATE_KEY", "")


@pytest.fixture
def expo(monkeypatch):
    """Expo's push service: records each batch; answers ``ok`` per message
    unless a token is listed in ``gone`` or ``broken``."""
    state = SimpleNamespace(batches=[], gone=set(), broken=set())

    async def fake(messages):
        state.batches.append(messages)
        tickets = []
        for m in messages:
            if m["to"] in state.gone:
                tickets.append(
                    {"status": "error", "details": {"error": "DeviceNotRegistered"}}
                )
            elif m["to"] in state.broken:
                tickets.append(
                    {"status": "error", "details": {"error": "MessageRateExceeded"}}
                )
            else:
                tickets.append({"status": "ok", "id": "ticket"})
        return tickets

    monkeypatch.setattr(mobile_push, "_post", fake)
    return state


def _migration():
    """The revision file, loaded by path: its name starts with a digit."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/202610091200_mobile_push_tokens.py"
    )
    spec = importlib.util.spec_from_file_location("mobile_push_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sent(expo) -> list[dict]:
    return [m for batch in expo.batches for m in batch]


async def _register(c, token: str, platform: str = "android"):
    return await c.post(
        "/api/v1/me/mobile-push/tokens",
        json={"token": token, "platform": platform, "device_label": "Pixel 8"},
    )


async def _push_on(user_id: int, **extra):
    current = await notifications.get(user_id)
    return await notifications.save(
        user_id, {"channels": {"push": True}, **extra}, revision=current["revision"]
    )


@pytest.mark.asyncio
class TestRegistering:
    async def test_off_the_routes_are_not_there(self, team):
        async with client_as(team.as_user(team.member)) as c:
            assert (await c.get("/api/v1/me/mobile-push/devices")).status_code == 404
            assert (await _register(c, TOKEN.format(1))).status_code == 404

    async def test_a_phone_is_kept_once_and_listed_for_its_owner(self, team, on):
        token = TOKEN.format(team.member.id)
        async with client_as(team.as_user(team.member)) as c:
            first = await _register(c, token)
            again = await _register(c, token, platform="android")
            listed = (await c.get("/api/v1/me/mobile-push/devices")).json()
        assert first.status_code == 200, first.text
        assert first.json()["state"] == "active"
        assert first.json()["label"] == "Pixel 8"
        assert again.json()["id"] == first.json()["id"]  # updated, not duplicated
        assert [d["id"] for d in listed["devices"]] == [first.json()["id"]]
        async with client_as(team.as_user(team.owner)) as c:
            theirs = (await c.get("/api/v1/me/mobile-push/devices")).json()
        assert theirs["devices"] == []  # a colleague never sees it

    @pytest.mark.parametrize(
        "token",
        ["", "hello", "https://fcm.googleapis.com/x", "ExponentPushToken[]"],
    )
    async def test_anything_but_an_expo_token_is_refused(self, team, on, token):
        async with client_as(team.as_user(team.member)) as c:
            r = await _register(c, token)
        assert r.status_code == 422

    async def test_signing_in_as_someone_else_moves_the_phone(self, team, on):
        token = TOKEN.format(f"move{team.member.id}")
        async with client_as(team.as_user(team.member)) as c:
            await _register(c, token)
        async with client_as(team.as_user(team.owner)) as c:
            await _register(c, token)
        mine = await mobile_push.devices(team.member.id)
        theirs = await mobile_push.devices(team.owner.id)
        assert mine == [] and len(theirs) == 1

    async def test_sign_out_revokes_only_the_owners_token(self, team, on):
        token = TOKEN.format(f"out{team.member.id}")
        async with client_as(team.as_user(team.member)) as c:
            await _register(c, token)
        async with client_as(team.as_user(team.owner)) as c:
            r = await c.post(
                "/api/v1/me/mobile-push/tokens/remove", json={"token": token}
            )
        assert r.json() == {"removed": False}  # not theirs
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(
                "/api/v1/me/mobile-push/tokens/remove", json={"token": token}
            )
            twice = await c.post(
                "/api/v1/me/mobile-push/tokens/remove", json={"token": token}
            )
        assert r.json() == {"removed": True}
        assert twice.json() == {"removed": False}
        assert (await mobile_push.devices(team.member.id))[0]["state"] == "revoked"

    async def test_a_device_is_removed_from_the_list_by_its_owner_only(self, team, on):
        async with client_as(team.as_user(team.member)) as c:
            device = (await _register(c, TOKEN.format(f"rm{team.member.id}"))).json()
        async with client_as(team.as_user(team.owner)) as c:
            r = await c.delete(f"/api/v1/me/mobile-push/devices/{device['id']}")
        assert r.status_code == 404
        async with client_as(team.as_user(team.member)) as c:
            r = await c.delete(f"/api/v1/me/mobile-push/devices/{device['id']}")
        assert r.status_code == 200
        assert r.json()["devices"][0]["state"] == "revoked"


@pytest.mark.asyncio
class TestSending:
    async def test_every_live_phone_is_sent_in_one_request(self, team, on, expo):
        for n in range(2):
            await mobile_push.register(
                team.member.id, team.org, token=TOKEN.format(f"s{n}{team.member.id}"),
                platform="ios",
            )  # fmt: skip
        outcome = await mobile_push.send_to_user(
            team.member.id, {"title": "T", "body": "B", "url": "/tasks", "tag": "x"}
        )
        assert outcome == "sent"
        assert len(expo.batches) == 1 and len(expo.batches[0]) == 2
        message = expo.batches[0][0]
        assert message["data"] == {"url": "/tasks", "tag": "x"}
        assert message["channelId"] == "default"
        device = (await mobile_push.devices(team.member.id))[0]
        assert device["last_success_at"] is not None

    async def test_a_gone_install_is_revoked_and_a_failure_kept(self, team, on, expo):
        gone, broken = (
            TOKEN.format(f"g{team.member.id}"),
            TOKEN.format(f"b{team.member.id}"),
        )
        for token in (gone, broken):
            await mobile_push.register(
                team.member.id, team.org, token=token, platform="android"
            )
        expo.gone.add(gone)
        expo.broken.add(broken)
        assert (
            await mobile_push.send_to_user(team.member.id, {"title": "T"}) == "failed"
        )
        states = {d["state"] for d in await mobile_push.devices(team.member.id)}
        assert states == {"revoked", "failing"}
        # The revoked one is not tried again.
        await mobile_push.send_to_user(team.member.id, {"title": "T"})
        assert [m["to"] for m in expo.batches[-1]] == [broken]

    async def test_flag_off_for_the_workspace_sends_nothing(
        self, team, on, expo, monkeypatch
    ):
        await mobile_push.register(
            team.member.id, team.org, token=TOKEN.format(f"f{team.member.id}"),
            platform="android",
        )  # fmt: skip
        monkeypatch.setattr(constants, "MOBILE_PUSH_ENABLED", False)
        assert await mobile_push.send_to_user(team.member.id, {"title": "T"}) == "off"
        assert expo.batches == []

    async def test_expo_unreachable_is_failed_never_raised(self, team, on, monkeypatch):
        await mobile_push.register(
            team.member.id, team.org, token=TOKEN.format(f"u{team.member.id}"),
            platform="android",
        )  # fmt: skip
        monkeypatch.setattr(
            mobile_push, "_post", AsyncMock(side_effect=OSError("down"))
        )
        assert (
            await mobile_push.send_to_user(team.member.id, {"title": "T"}) == "failed"
        )


@pytest.mark.asyncio
class TestThePushChannel:
    async def test_browsers_and_phones_both_receive(self, team, on, expo, monkeypatch):
        monkeypatch.setattr(constants, "VAPID_PUBLIC_KEY", "BPublicKey")
        monkeypatch.setattr(constants, "VAPID_PRIVATE_KEY", "private")
        browsers = []

        async def web(sub, payload):
            browsers.append(payload)
            return push.OK

        monkeypatch.setattr(push, "send", web)
        await notifications.subscribe(
            team.member.id,
            endpoint=f"https://fcm.googleapis.com/fcm/send/m{team.member.id}",
            p256dh="k" * 20, auth="a" * 16, device_label="Chrome",
        )  # fmt: skip
        await mobile_push.register(
            team.member.id, team.org, token=TOKEN.format(f"w{team.member.id}"),
            platform="ios",
        )  # fmt: skip
        assert await notifications._push(team.member.id, {"title": "T"}) == "sent"
        assert len(browsers) == 1 and len(_sent(expo)) == 1
        # The app's own notices skip the browsers.
        assert (
            await notifications._push(team.member.id, {"title": "T"}, web=False)
            == "sent"
        )
        assert len(browsers) == 1 and len(_sent(expo)) == 2

    async def test_a_phone_makes_push_available_without_browser_keys(self, team, on):
        async with client_as(team.as_user(team.member)) as c:
            body = (await c.get("/api/v1/me/notifications")).json()
        assert body["availability"]["push"] == {"available": True, "reason": None}

    async def test_today_reminders_count_a_phone_as_a_device(self, team, on):
        from api.services.today import delivery

        before = await delivery.channel_state(team.org, team.member.id, delivery.PUSH)
        assert before["state"] == delivery.NEEDS_SETUP
        await mobile_push.register(
            team.member.id, team.org, token=TOKEN.format(f"t{team.member.id}"),
            platform="android",
        )  # fmt: skip
        after = await delivery.channel_state(team.org, team.member.id, delivery.PUSH)
        assert after["state"] == delivery.AVAILABLE


def test_outcomes_combine_without_hiding_a_kind():
    combined = notifications._combined
    assert combined("needs_setup", "off") == "needs_setup"  # flag off: as before
    assert combined("sent", "off") == "sent"
    assert combined("needs_setup", "sent") == "sent"
    assert combined("no_device", "failed") == "failed"
    assert combined("sent", "failed") == "partial"
    assert combined("failed", "failed") == "failed"
    assert combined("needs_setup", "no_device") == "no_device"


@pytest.mark.asyncio
class TestAnnouncements:
    async def _phone(self, team):
        await mobile_push.register(
            team.member.id, team.org, token=TOKEN.format(f"a{team.member.id}"),
            platform="android",
        )  # fmt: skip
        await _push_on(team.member.id, channels={"push": True, "email": True})

    async def test_a_reply_reaches_the_phone_once_and_never_by_email(
        self, team, on, expo
    ):
        await self._phone(team)
        with patch.object(
            notifications, "_email", AsyncMock(return_value="sent")
        ) as mail:
            first = await mobile_push.announce_reply(
                organization_id=team.org, user_id=team.member.id, thread_id="t-1",
                event_id=9001, body="Here is the plan for Monday.",
            )  # fmt: skip
            again = await mobile_push.announce_reply(
                organization_id=team.org, user_id=team.member.id, thread_id="t-1",
                event_id=9001, body="Here is the plan for Monday.",
            )  # fmt: skip
        assert first == {"push": "sent"}
        assert again == {"push": "duplicate"}
        mail.assert_not_awaited()
        [message] = _sent(expo)
        # Private previews are on by default: generic lock-screen words.
        assert message["title"] == notifications.GENERIC_TITLE
        assert message["body"] == notifications.GENERIC_BODY
        assert message["data"]["url"] == "/overview?thread=t-1"

    async def test_with_previews_on_the_words_are_shown(self, team, on, expo):
        await self._phone(team)
        await _push_on(team.member.id, private_previews=False)
        await mobile_push.announce_approval(
            organization_id=team.org, user_id=team.member.id, event_id=77,
            label="Send the invoice to Acme Print",
        )  # fmt: skip
        [message] = _sent(expo)
        assert message["title"] == "Decibyl wants your OK"
        assert message["body"] == "Send the invoice to Acme Print"
        assert message["data"]["url"] == "/tasks/approvals/77"

    async def test_a_topic_turned_off_stops_it(self, team, on, expo):
        await self._phone(team)
        await _push_on(team.member.id, topics={"approvals": {"on": False}})
        out = await mobile_push.announce_approval(
            organization_id=team.org, user_id=team.member.id, event_id=78, label="x"
        )
        assert out == {"all": "off"} and _sent(expo) == []

    async def test_flag_off_announces_nothing(self, team, on, expo, monkeypatch):
        await self._phone(team)
        monkeypatch.setattr(constants, "MOBILE_PUSH_ENABLED", False)
        out = await mobile_push.announce_reply(
            organization_id=team.org, user_id=team.member.id, thread_id=None,
            event_id=1, body="x",
        )  # fmt: skip
        assert out == {"all": "off"} and _sent(expo) == []

    async def test_a_proposed_card_announces_to_the_person_who_asked(
        self, team, on, expo
    ):
        from api.services import acting
        from api.services.workflow import actions

        await self._phone(team)
        resolved = {
            "action": "run_tool",
            "args": {"tool_uuid": "u-1"},
            "label": "Gmail — Send Email via gmail",
            "why": "Asked in the thread",
            "effect": "Runs in gmail.",
            "reversible": False,
            "state": "proposed",
        }
        with (
            patch.object(actions, "resolve", AsyncMock(return_value=resolved)),
            patch.object(actions, "_already_proposed", AsyncMock(return_value=None)),
            acting.acting_as(team.member.id),
        ):
            out = await actions.propose(
                organization_id=team.org,
                workflow_id=None,
                workflow_run_id=None,
                arguments={"action": "run_tool", "tool_uuid": "u-1"},
                in_channel=False,
            )
        assert out["status"] == "proposed"
        [message] = _sent(expo)
        assert message["data"]["url"] == f"/tasks/approvals/{out['event_id']}"

    async def test_the_end_of_a_call_placed_for_the_person(self, team, on, expo):
        await self._phone(team)
        out = await mobile_push.announce_call(
            organization_id=team.org, user_id=team.member.id, workflow_run_id=5,
            summary="Answered · 1m 20s",
        )  # fmt: skip
        assert out == {"push": "sent"}

    async def test_nobody_to_tell_is_not_an_error(self, team, on, expo):
        out = await mobile_push.announce_call(
            organization_id=team.org, user_id=None, workflow_run_id=6, summary="x"
        )
        assert out == {"all": "no_person"}


@pytest.mark.asyncio
class TestMigration:
    async def test_down_then_up_leaves_the_table_as_the_model_says(self, test_engine):
        """Run inside one transaction and roll back, so the session's database
        is untouched: PostgreSQL DDL is transactional."""
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        migration = _migration()

        async with test_engine.connect() as conn:
            trans = await conn.begin()
            try:

                def run(sync_conn):
                    ops = Operations(MigrationContext.configure(sync_conn))
                    with Operations.context(ops.migration_context):
                        migration.downgrade()
                        gone = (
                            "mobile_push_tokens"
                            not in inspect(sync_conn).get_table_names()
                        )
                        migration.upgrade()
                    columns = {
                        c["name"]
                        for c in inspect(sync_conn).get_columns("mobile_push_tokens")
                    }
                    return gone, columns

                gone, columns = await conn.run_sync(run)
            finally:
                await trans.rollback()
        assert gone
        assert columns == {c.name for c in MobilePushTokenModel.__table__.columns}

    async def test_the_revision_follows_the_head_it_was_written_on(self):
        migration = _migration()

        assert migration.revision == "20261009mobile"
        assert migration.down_revision == "20261009people"


@pytest.mark.asyncio
async def test_the_table_exists_after_migrations(test_engine):
    async with test_engine.connect() as conn:
        found = await conn.scalar(
            text("SELECT to_regclass('public.mobile_push_tokens') IS NOT NULL")
        )
    assert found
    async with db_client.async_session() as session:
        assert (
            await session.scalars(select(MobilePushTokenModel).limit(1))
        ).all() is not None
