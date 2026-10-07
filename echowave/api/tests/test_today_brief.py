"""One daily brief with source coverage, and the end-of-day note (handoff
10, 22, 31.4; screens 07 and 20).

Done when: the brief is off until a person accepts it; it lists every source
and what happened to it; a calendar nobody read is never "no meetings" and a
partly failed read is never "nothing needs attention"; Refresh updates the
day's brief instead of writing another; one saved schedule delivers one
brief however often the tick runs; Test is one labelled occurrence;
WhatsApp and push say "needs setup" until they can really deliver; and one
person's brief is never another's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text

from api.db import db_client
from api.db.today_models import DailyBriefModel, TodayDeliveryModel
from api.services.today import brief, delivery, ticks
from api.services.today.scope import Conflict
from api.tests.today_helpers import clean, client_as, make_people, switch_on

# Thursday 8 October 2026, 09:01 in Kolkata.
AT_NINE = datetime(2026, 10, 8, 3, 31, tzinfo=UTC)


@pytest.fixture
async def people(test_engine):
    p = await make_people()
    yield p
    await clean(p)


@pytest.fixture
def on(monkeypatch):
    switch_on(monkeypatch, "daily_brief")


def _calls(*rows):
    return patch.object(
        db_client, "list_missed_calls", AsyncMock(return_value=list(rows))
    )


def _call(id_, caller, at, outcome="pending"):
    return SimpleNamespace(id=id_, caller=caller, received_at=at, outcome=outcome)


async def _accept(viewer, **changes):
    return await brief.save_settings(viewer, {"enabled": True, **changes}, revision=0)


async def _briefs(viewer):
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(DailyBriefModel).where(
                        DailyBriefModel.user_id == viewer.user_id
                    )
                )
            ).scalars()
        )


async def _deliveries(viewer):
    async with db_client.async_session() as session:
        return list(
            (
                await session.execute(
                    select(TodayDeliveryModel).where(
                        TodayDeliveryModel.user_id == viewer.user_id
                    )
                )
            ).scalars()
        )


@pytest.mark.asyncio
class TestOffUntilAccepted:
    async def test_the_default_is_off_with_nine_suggested(self, people, on):
        settings = await brief.get_settings(people.me)
        assert settings["enabled"] is False and settings["saved"] is False
        assert settings["local_time"] == "09:00"
        assert settings["timezone"] == "Asia/Kolkata"
        assert settings["next_sentence"].startswith("Off.")

    async def test_accepting_shows_the_next_brief(self, people, on):
        saved = await _accept(people.me)
        assert saved["enabled"] and saved["revision"] == 1
        assert saved["next_sentence"].startswith("Next brief: ")
        assert "(Asia/Kolkata)" in saved["next_sentence"]

    async def test_a_stale_save_is_a_conflict_showing_the_stored_value(
        self, people, on
    ):
        await _accept(people.me)
        await brief.save_settings(people.me, {"local_time": "08:00"}, revision=1)
        with pytest.raises(Conflict) as caught:
            await brief.save_settings(people.me, {"local_time": "07:00"}, revision=1)
        assert caught.value.stored["local_time"] == "08:00"

    async def test_channel_states_are_honest(self, people, on):
        states = {
            c["channel"]: c
            for c in (await brief.get_settings(people.me))["channel_states"]
        }
        assert states["in_app"]["state"] == "available"
        assert states["push"]["state"] == "needs_setup"
        assert states["whatsapp"]["state"] == "needs_setup"

    async def test_it_is_one_persons_choice(self, people, on):
        await _accept(people.me)
        assert (await brief.get_settings(people.colleague))["enabled"] is False


@pytest.mark.asyncio
class TestCoverage:
    async def test_every_source_is_listed_with_what_happened(self, people, on):
        with _calls():
            view = await brief.build(people.me, now=AT_NINE)
        by_kind = {s["kind"]: s for s in view["sources"]}
        assert set(by_kind) == {
            "approvals",
            "calendar",
            "reminders",
            "tasks",
            "missed_calls",
            "mail",
        }
        assert by_kind["calendar"]["needs_setup"] is True
        assert (
            by_kind["calendar"]["detail"]
            == "Connect your calendar to include appointments."
        )
        assert by_kind["mail"]["status"] == "unavailable"
        assert by_kind["approvals"]["status"] == "read"

    async def test_an_unread_calendar_is_never_no_meetings(self, people, on):
        with _calls():
            view = await brief.build(people.me, now=AT_NINE)
        assert "0 appointments" not in view["summary"]
        assert "no meetings" not in view["summary"].lower()
        assert "Connect your calendar to include appointments." in view["summary"]
        assert view["sections"]["appointments"]["calendar_read"] is False

    async def test_a_failed_source_makes_a_partial_brief_that_says_so(
        self, people, on, monkeypatch
    ):
        async def broken(viewer, start, end):
            return brief.Source(
                "calendar",
                "Calendar",
                status=brief.FAILED,
                detail="Calendar could not refresh.",
            )

        monkeypatch.setitem(brief.READERS, "calendar", broken)
        with _calls():
            view = await brief.build(people.me, now=AT_NINE)
        assert view["status"] == "partial"
        assert "Calendar could not refresh. This brief includes " in view["summary"]
        assert "nothing needs attention" not in view["summary"].lower()

    async def test_what_is_there_is_counted_in_sentences(self, people, on):
        with _calls(_call(1, "+919800000001", AT_NINE)):
            view = await brief.build(people.me, now=AT_NINE)
        assert "1 missed call not returned" in view["summary"]
        assert view["sections"]["missed_calls"]["count"] == 1
        suggestion = view["sections"]["suggestions"][0]
        assert suggestion["title"] == "Call +919800000001 back"
        assert suggestion["why"]

    async def test_a_read_calendar_counts_appointments(self, people, on, monkeypatch):
        async def calendar(viewer, start, end):
            return brief.Source(
                "calendar",
                "Calendar",
                items=[{"title": "Standup", "at": "2026-10-08T10:00:00+05:30"}],
            )

        monkeypatch.setitem(brief.READERS, "calendar", calendar)
        with _calls():
            view = await brief.build(people.me, now=AT_NINE)
        assert "1 appointment" in view["summary"]
        assert view["status"] == "complete"


@pytest.mark.asyncio
class TestOneBriefADay:
    async def test_refresh_updates_the_same_brief(self, people, on):
        with _calls():
            first = await brief.build(people.me, now=AT_NINE)
            later = AT_NINE.replace(minute=50)
            second = await brief.build(people.me, now=later)
        assert first["id"] == second["id"]
        assert second["refreshed_at"] == later.isoformat()
        assert len(await _briefs(people.me)) == 1

    async def test_one_schedule_one_delivered_brief(self, people, on):
        await _accept(people.me)
        with _calls():
            first = await ticks.deliver_due_briefs(now=AT_NINE)
            second = await ticks.deliver_due_briefs(now=AT_NINE.replace(minute=40))
        assert (first, second) == (1, 0)
        rows = await _deliveries(people.me)
        assert [(r.channel, r.status) for r in rows] == [("in_app", "sent")]
        assert (await _briefs(people.me))[0].delivered_at is not None

    async def test_not_before_its_time_nor_long_after(self, people, on):
        await _accept(people.me)
        with _calls():
            assert (
                await ticks.deliver_due_briefs(now=AT_NINE.replace(hour=3, minute=0))
                == 0
            )
            assert await ticks.deliver_due_briefs(now=AT_NINE.replace(hour=5)) == 0
        assert await _briefs(people.me) == []

    async def test_paused_keeps_the_settings_and_sends_nothing(self, people, on):
        await _accept(people.me)
        paused = await brief.save_settings(people.me, {"paused": True}, revision=1)
        assert paused["enabled"] is True and paused["next_sentence"].startswith(
            "Paused."
        )
        with _calls():
            assert await ticks.deliver_due_briefs(now=AT_NINE) == 0

    async def test_a_day_not_chosen_has_no_brief(self, people, on):
        await _accept(people.me, days=[0])  # Mondays only; the 8th is a Thursday
        with _calls():
            assert await ticks.deliver_due_briefs(now=AT_NINE) == 0

    async def test_switched_off_nothing_runs(self, people, on, monkeypatch):
        await _accept(people.me)
        from api import constants

        monkeypatch.setattr(constants, "DAILY_BRIEF_ENABLED", False)
        assert await ticks.deliver_due_briefs(now=AT_NINE) == 0


@pytest.mark.asyncio
class TestChannels:
    async def test_whatsapp_is_accepted_not_claimed_delivered(self, people, on):
        await _accept(people.me, channels=["in_app", "whatsapp"])
        identity = SimpleNamespace(
            channel="whatsapp",
            conversation_ref={"to": "+919800000002"},
            external_id="+919800000002",
        )
        sender = AsyncMock(return_value=("accepted", None, "whatsapp:wamid.1"))
        with (
            _calls(),
            patch(
                "api.services.messaging.platform_whatsapp.is_configured",
                return_value=True,
            ),
            patch(
                "api.services.messaging.channels.identities.for_member",
                AsyncMock(return_value=[identity]),
            ),
            patch(
                "api.services.messaging.whatsapp_inbound.session_open",
                AsyncMock(return_value=True),
            ),
            patch.object(delivery, "_send_whatsapp", sender),
        ):
            await ticks.deliver_due_briefs(now=AT_NINE)
        statuses = {r.channel: r.status for r in await _deliveries(people.me)}
        assert statuses == {"in_app": "sent", "whatsapp": "accepted"}
        assert "Your brief for 2026-10-08" in sender.await_args.args[2]

    async def test_a_closed_whatsapp_window_is_skipped_with_the_reason(
        self, people, on
    ):
        await _accept(people.me, channels=["whatsapp"])
        identity = SimpleNamespace(
            channel="whatsapp",
            conversation_ref={"to": "+919800000002"},
            external_id="+919800000002",
        )
        sender = AsyncMock()
        with (
            _calls(),
            patch(
                "api.services.messaging.platform_whatsapp.is_configured",
                return_value=True,
            ),
            patch(
                "api.services.messaging.channels.identities.for_member",
                AsyncMock(return_value=[identity]),
            ),
            patch(
                "api.services.messaging.whatsapp_inbound.session_open",
                AsyncMock(return_value=False),
            ),
            patch.object(delivery, "_send_whatsapp", sender),
        ):
            await ticks.deliver_due_briefs(now=AT_NINE)
        row = (await _deliveries(people.me))[0]
        assert row.status == "skipped" and row.reason_code == "outside_whatsapp_window"
        sender.assert_not_awaited()

    async def test_unconfigured_whatsapp_and_push_need_setup(self, people, on):
        await _accept(people.me, channels=["whatsapp", "push"])
        with _calls():
            await ticks.deliver_due_briefs(now=AT_NINE)
        statuses = {r.channel: r.status for r in await _deliveries(people.me)}
        assert statuses == {"whatsapp": "needs_setup", "push": "needs_setup"}


@pytest.mark.asyncio
class TestEndOfDay:
    async def test_the_note_counts_missed_calls_handled(self, people, monkeypatch):
        switch_on(monkeypatch, "end_of_day_note")
        evening = datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
        with _calls(
            _call(1, "+919800000001", AT_NINE, "called_back"),
            _call(2, "+919800000003", AT_NINE, "pending"),
        ):
            view = await brief.build(people.me, kind=brief.END_OF_DAY, now=evening)
        assert view["kind"] == "end_of_day"
        assert "1 of 2 missed calls called back" in view["summary"]
        assert (
            view["sections"]["missed_calls"]["waiting"][0]["caller"] == "+919800000003"
        )


@pytest.mark.asyncio
class TestArrival:
    async def test_off_the_routes_are_not_there(self, people):
        async with client_as(people.me_user) as client:
            assert (await client.get("/api/v1/today/brief")).status_code == 404
            assert (await client.get("/api/v1/today/end-of-day")).status_code == 404

    async def test_on_settings_refresh_and_test(self, people, on):
        async with client_as(people.me_user) as client:
            got = (await client.get("/api/v1/today/brief")).json()
            assert got["brief"] is None and got["settings"]["enabled"] is False
            saved = await client.put(
                "/api/v1/today/brief/settings", json={"revision": 0, "enabled": True}
            )
            assert saved.status_code == 200 and saved.json()["revision"] == 1
            stale = await client.put(
                "/api/v1/today/brief/settings",
                json={"revision": 0, "local_time": "07:00"},
            )
            assert (
                stale.status_code == 409
                and stale.json()["detail"]["stored"]["local_time"] == "09:00"
            )
            with _calls():
                fresh = (await client.post("/api/v1/today/brief/refresh")).json()
                tested = (await client.post("/api/v1/today/brief/test")).json()
            assert fresh["sources"]
            assert tested["deliveries"][0]["is_test"] is True
            # A test is not the day's delivery.
            assert (await client.get("/api/v1/today/brief")).json()["brief"][
                "delivered_at"
            ] is None
            bad = await client.put(
                "/api/v1/today/brief/settings",
                json={"revision": 1, "channels": ["fax"]},
            )
            assert bad.status_code == 422
            eod = await client.put(
                "/api/v1/today/brief/settings",
                json={"revision": 1, "end_of_day_enabled": True},
            )
            assert eod.status_code == 422  # its own switch is off

    async def test_another_persons_brief_is_not_theirs(self, people, on):
        with _calls():
            await brief.build(people.me, now=AT_NINE)
        assert await brief.latest(people.colleague) is None
        assert await brief.latest(people.stranger) is None
        async with db_client.async_session() as session:
            count = await session.scalar(
                text("SELECT count(*) FROM daily_briefs WHERE organization_id = :o"),
                {"o": people.me.organization_id},
            )
        assert count == 1
