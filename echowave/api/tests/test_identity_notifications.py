"""Notification preferences and web push (screen 21; launch stream identity).

Done when: preferences are the person's own, saved with a revision that
turns a stale save into a conflict; optional suggestions start off, respect
their daily cap and quiet hours, while requested reminders keep their time;
a disabled or snoozed topic stops its deliveries; a retried job notifies
once; lock-screen text is generic while private previews are on; a push
service saying the permission is gone is reflected; and push subscriptions
only ever point at browsers' own push services.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from api import constants
from api.services.identity import notifications, push
from api.tests.identity_support import client_as, flags, team  # noqa: F401

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"


@pytest.fixture
def on(monkeypatch):
    flags(monkeypatch, "IDENTITY_NOTIFICATIONS_ENABLED")
    monkeypatch.setattr(constants, "VAPID_PUBLIC_KEY", "BPublicKey")
    monkeypatch.setattr(constants, "VAPID_PRIVATE_KEY", "private")


@pytest.fixture
def pushed(monkeypatch):
    """The push service: records each payload; answers ``ok`` unless told."""
    sent: list[dict] = []
    answer = {"value": push.OK}

    async def fake(subscription, payload):
        sent.append({"endpoint": subscription.endpoint, **payload})
        return answer["value"]

    monkeypatch.setattr(push, "send", fake)
    sent_answer = answer
    return sent, sent_answer


async def _subscribe(user_id: int, endpoint: str = ENDPOINT):
    await notifications.subscribe(
        user_id,
        endpoint=f"{endpoint}{user_id}",
        p256dh="k" * 20,
        auth="a" * 16,
        device_label="Chrome",
    )


async def _push_on(user_id: int, **extra):
    current = await notifications.get(user_id)
    return await notifications.save(
        user_id, {"channels": {"push": True}, **extra}, revision=current["revision"]
    )


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_routes_are_not_there(self, team):
        async with client_as(team.as_user(team.member)) as c:
            assert (await c.get("/api/v1/me/notifications")).status_code == 404

    async def test_defaults_and_both_permission_states_are_shown(self, team, on):
        async with client_as(team.as_user(team.member)) as c:
            body = (await c.get("/api/v1/me/notifications")).json()
        assert body["topics"]["suggestions"]["on"] is False  # off until chosen
        assert body["topics"]["reminders"]["on"] is True
        assert body["private_previews"] is True
        assert body["channels"]["push"] is False
        assert body["availability"]["push"]["available"] is True
        assert body["availability"]["in_app"]["available"] is False
        assert "shared by your workspace" in body["availability"]["in_app"]["reason"]
        assert body["push_public_key"] == "BPublicKey"
        assert body["suggestion_daily_cap"] == constants.NOTIFY_SUGGESTION_DAILY_CAP

    async def test_no_keys_is_needs_setup(self, team, on, monkeypatch):
        monkeypatch.setattr(constants, "VAPID_PRIVATE_KEY", "")
        async with client_as(team.as_user(team.member)) as c:
            body = (await c.get("/api/v1/me/notifications")).json()
            assert body["availability"]["push"] == {
                "available": False,
                "reason": "Push is not set up on this deployment yet.",
            }
            r = await c.post(
                "/api/v1/me/push-subscriptions",
                json={"endpoint": ENDPOINT, "keys": {"p256dh": "k", "auth": "a"}},
            )
        assert r.status_code == 503


@pytest.mark.asyncio
class TestSaving:
    async def test_saved_read_back_and_a_stale_save_conflicts(self, team, on):
        async with client_as(team.as_user(team.member)) as c:
            saved = await c.put(
                "/api/v1/me/notifications",
                json={
                    "revision": 0,
                    "channels": {"push": True},
                    "quiet_start": "22:00",
                    "quiet_end": "07:00",
                },
            )
            assert saved.status_code == 200 and saved.json()["revision"] == 1
            stale = await c.put(
                "/api/v1/me/notifications",
                json={"revision": 0, "channels": {"email": True}},
            )
            assert stale.status_code == 409
            again = (await c.get("/api/v1/me/notifications")).json()
        assert again["channels"]["push"] is True and again["channels"]["email"] is False
        assert again["quiet_start"] == "22:00"

    @pytest.mark.parametrize(
        "change",
        [
            {"channels": {"fax": True}},
            {"topics": {"gossip": {"on": True}}},
            {"quiet_start": "25:00", "quiet_end": "07:00"},
            {"quiet_start": "22:00"},
            {"topics": {"mail": {"snoozed_until": "2099-01-01T00:00:00+00:00"}}},
        ],
    )
    async def test_nonsense_is_refused(self, team, on, change):
        with pytest.raises(notifications.PreferencesInvalid):
            await notifications.save(team.member.id, change, revision=0)

    async def test_mine_never_change_a_colleagues(self, team, on):
        await notifications.save(
            team.owner.id, {"channels": {"email": True}}, revision=0
        )
        await notifications.save(
            team.member.id, {"channels": {"push": True}}, revision=0
        )
        owner = await notifications.get(team.owner.id)
        assert owner["channels"] == {"push": False, "email": True, "channel": False}
        async with client_as(team.as_user(team.member)) as c:
            r = await c.put(
                "/api/v1/me/notifications",
                json={"revision": 1, "user_id": team.owner.id},
            )
        assert r.status_code == 422


@pytest.mark.asyncio
class TestDevices:
    @pytest.mark.parametrize(
        "endpoint",
        [
            "http://fcm.googleapis.com/x",
            "https://169.254.169.254/latest/meta-data",
            "https://evil.example/push",
            "https://fcm.googleapis.com.evil.example/x",
            "https://fcm.googleapis.com:8443/x",
        ],
    )
    async def test_only_browsers_push_services(self, team, on, endpoint):
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post(
                "/api/v1/me/push-subscriptions",
                json={"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}},
            )
        assert r.status_code == 422

    @pytest.mark.parametrize(
        "endpoint",
        [
            ENDPOINT,
            "https://web.push.apple.com/abc",
            "https://wns2-par02p.notify.windows.com/w/?token=x",
        ],
    )
    async def test_the_real_ones_are_accepted(self, endpoint):
        assert push.endpoint_allowed(endpoint)

    async def test_a_colleagues_device_is_not_mine_to_remove(self, team, on):
        await _subscribe(team.owner.id)
        (device,) = await notifications.devices(team.owner.id)
        async with client_as(team.as_user(team.member)) as c:
            r = await c.delete(f"/api/v1/me/push-subscriptions/{device['id']}")
        assert r.status_code == 404
        assert (await notifications.devices(team.owner.id))[0]["state"] == "active"


@pytest.mark.asyncio
class TestDelivering:
    async def test_lock_screen_text_is_generic_by_default(self, team, on, pushed):
        sent, _ = pushed
        await _subscribe(team.member.id)
        await _push_on(team.member.id)
        outcome = await notifications.notify(
            team.member.id,
            topic="approvals",
            title="Pay Acme Print ₹4,800?",
            body="Invoice 42 waits for you",
            dedupe_key="k1",
        )
        assert outcome == {"push": "sent"}
        assert sent[0]["title"] == "Decibyl" and "4,800" not in sent[0]["body"]

    async def test_a_retried_job_notifies_once(self, team, on, pushed):
        sent, _ = pushed
        await _subscribe(team.member.id)
        await _push_on(team.member.id)
        for _ in range(2):
            await notifications.notify(
                team.member.id,
                topic="approvals",
                title="t",
                body="b",
                dedupe_key="same",
            )
        assert len(sent) == 1

    async def test_a_disabled_or_snoozed_topic_stops(self, team, on, pushed):
        sent, _ = pushed
        await _subscribe(team.member.id)
        await _push_on(team.member.id, topics={"mail": {"on": False}})
        assert await notifications.notify(
            team.member.id, topic="mail", title="t", body="b", dedupe_key="m1"
        ) == {"all": "off"}
        until = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        current = await notifications.get(team.member.id)
        await notifications.save(
            team.member.id,
            {"topics": {"approvals": {"snoozed_until": until}}},
            revision=current["revision"],
        )
        assert await notifications.notify(
            team.member.id, topic="approvals", title="t", body="b", dedupe_key="a1"
        ) == {"all": "snoozed"}
        assert sent == []

    async def test_quiet_hours_hold_suggestions_but_not_requested_reminders(
        self, team, on, pushed
    ):
        sent, _ = pushed
        await _subscribe(team.member.id)
        await _push_on(
            team.member.id,
            quiet_start="22:00",
            quiet_end="07:00",
            topics={"suggestions": {"on": True}},
        )
        night = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)  # 23:30 in India
        held = await notifications.notify(
            team.member.id,
            topic="suggestions",
            title="t",
            body="b",
            dedupe_key="s1",
            now=night,
        )
        kept = await notifications.notify(
            team.member.id,
            topic="reminders",
            title="t",
            body="b",
            dedupe_key="r1",
            now=night,
        )
        assert held == {"all": "quiet"} and kept == {"push": "sent"}

    async def test_suggestions_respect_the_daily_cap(
        self, team, on, pushed, monkeypatch
    ):
        monkeypatch.setattr(constants, "NOTIFY_SUGGESTION_DAILY_CAP", 2)
        await _subscribe(team.member.id)
        await _push_on(team.member.id, topics={"suggestions": {"on": True}})
        results = [
            await notifications.notify(
                team.member.id,
                topic="suggestions",
                title="t",
                body="b",
                dedupe_key=f"s{i}",
            )
            for i in range(3)
        ]
        assert results[:2] == [{"push": "sent"}, {"push": "sent"}]
        assert results[2] == {"all": "capped"}

    async def test_a_revoked_permission_is_reflected(self, team, on, pushed):
        sent, answer = pushed
        await _subscribe(team.member.id)
        await _push_on(team.member.id)
        answer["value"] = push.GONE
        await notifications.notify(
            team.member.id, topic="approvals", title="t", body="b", dedupe_key="g1"
        )
        (device,) = await notifications.devices(team.member.id)
        assert device["state"] == "revoked"
        # And nothing more is tried on it.
        again = await notifications.notify(
            team.member.id, topic="approvals", title="t", body="b", dedupe_key="g2"
        )
        assert again == {"push": "no_device"} and len(sent) == 1

    async def test_off_everywhere_nothing_happens(self, team, pushed, monkeypatch):
        sent, _ = pushed
        assert await notifications.notify(
            team.member.id, topic="approvals", title="t", body="b", dedupe_key="x"
        ) == {"all": "off"}
        assert sent == []

    async def test_the_test_push_is_labelled(self, team, on, pushed):
        sent, _ = pushed
        await _subscribe(team.member.id)
        async with client_as(team.as_user(team.member)) as c:
            r = await c.post("/api/v1/me/notifications/test")
        assert r.json() == {"push": "sent"} and sent[0]["title"] == "Decibyl test"


@pytest.mark.asyncio
class TestThePushService:
    async def test_gone_and_ok_are_read_from_the_status(self, monkeypatch, on):
        sub = type(
            "S", (), {"id": 1, "endpoint": ENDPOINT, "p256dh": "k", "auth": "a"}
        )()
        monkeypatch.setattr(push, "_send_sync", lambda info, data: 410)
        assert await push.send(sub, {"title": "t"}) == push.GONE
        monkeypatch.setattr(push, "_send_sync", lambda info, data: 201)
        assert await push.send(sub, {"title": "t"}) == push.OK
        monkeypatch.setattr(push, "_send_sync", lambda info, data: 500)
        assert await push.send(sub, {"title": "t"}) == "failed:500"

    async def test_an_endpoint_outside_the_list_is_never_called(self, monkeypatch, on):
        called = AsyncMock()
        monkeypatch.setattr(push, "_send_sync", called)
        sub = type(
            "S",
            (),
            {"id": 1, "endpoint": "https://evil.example/x", "p256dh": "k", "auth": "a"},
        )()
        assert await push.send(sub, {}) == "failed:endpoint_not_allowed"
        assert called.call_count == 0
