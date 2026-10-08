"""The Call and Appointment runtime (handoff 6).

Done when: nothing books until a person grants it; the policy saves with a
revision and refuses a stale save; slots come from the hours the business
keeps, minus bookings, inside lead time and horizon, and hours nobody set
are needs-setup rather than "open all day"; a booking needs structured
details, honours the verification rule, never overlaps another (even two at
once), and stays inside its own workspace; the call's tool never raises and
never reads out an existing booking; escalation leaves a line for the team.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from api.services.voice import appointments
from api.tests.support.voice import all_on, clean, client_as, make_people

IST = ZoneInfo("Asia/Kolkata")
#: A Monday well in the future, so lead time never interferes.
DAY = date(2031, 3, 3)
NOW = datetime(2031, 3, 1, 9, 0, tzinfo=IST)


@pytest.fixture
async def people(test_engine):
    p = await make_people("voice-a")
    yield p
    await clean(p)


@pytest.fixture
def hours(monkeypatch):
    """Open 09:00-11:00 every weekday, in India time."""

    async def schedule(_org, _workflow, _run=None):
        return {
            "enabled": True,
            "timezone": "Asia/Kolkata",
            "slots": [
                {"day_of_week": d, "start_time": "09:00", "end_time": "11:00"}
                for d in range(5)
            ],
        }

    async def zone(_org):
        return IST

    monkeypatch.setattr(appointments, "_schedule", schedule)
    monkeypatch.setattr(appointments, "_zone", zone)


async def _grant(people, **changes):
    current = await appointments.get_policy(people.org)
    return await appointments.save_policy(
        people.org, changes, revision=current["revision"], user_id=people.a.id
    )


def _at(hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime(DAY.year, DAY.month, DAY.day, int(h), int(m), tzinfo=IST)


async def _book(people, hhmm="09:00", org=None, **kw):
    details = dict(caller_name="Asha", caller_number="98765 43210", reason="Cleaning")
    details.update(kw)
    return await appointments.book(
        organization_id=org or people.org, starts_at=_at(hhmm), now=NOW, **details
    )


@pytest.mark.asyncio
class TestPolicy:
    async def test_off_until_granted(self, people):
        policy = await appointments.get_policy(people.org)
        assert policy["booking"] == "off" and policy["revision"] == 0

    async def test_saved_with_a_revision_and_stale_refused(self, people):
        saved = await _grant(people, booking="suggest", services=["Cleaning", " "])
        assert saved["revision"] == 1 and saved["services"] == ["Cleaning"]
        with pytest.raises(appointments.PolicyConflict) as stale:
            await appointments.save_policy(
                people.org, {"booking": "book"}, revision=0, user_id=people.a.id
            )
        assert stale.value.stored["booking"] == "suggest"

    @pytest.mark.parametrize(
        "changes",
        [
            {"booking": "always"},
            {"duration_minutes": 7},
            {"horizon_days": 0},
            {"escalate_to": "not a number"},
            {"who_knows": 1},
        ],
    )
    async def test_invalid_is_refused(self, people, changes):
        with pytest.raises(appointments.PolicyInvalid):
            await appointments.save_policy(
                people.org, changes, revision=0, user_id=people.a.id
            )

    async def test_a_helper_from_another_workspace_is_refused(self, people):
        from api.db import db_client

        foreign = await db_client.create_workflow(
            name="Theirs",
            workflow_definition={"nodes": [], "edges": []},
            user_id=people.c.id,
            organization_id=people.other,
        )
        with pytest.raises(appointments.PolicyInvalid):
            await _grant(people, call_workflow_id=foreign.id)

    async def test_only_admins_change_it_over_http(self, people, monkeypatch):
        all_on(monkeypatch)
        async with client_as(people.as_b) as c:
            denied = await c.put(
                "/api/v1/voice/appointments/policy",
                json={"revision": 0, "booking": "book"},
            )
            read = await c.get("/api/v1/voice/appointments/policy")
        assert denied.status_code == 403
        assert read.status_code == 200 and read.json()["booking"] == "off"
        async with client_as(people.as_a) as c:
            ok = await c.put(
                "/api/v1/voice/appointments/policy",
                json={"revision": 0, "booking": "book", "escalate_to": "098765 43210"},
            )
        assert ok.status_code == 200
        assert ok.json()["escalate_to"] == "+919876543210"

    async def test_routes_are_404_while_off(self, people):
        async with client_as(people.as_a) as c:
            assert (await c.get("/api/v1/voice/appointments/policy")).status_code == 404


@pytest.mark.asyncio
class TestSlots:
    async def test_booking_off_offers_nothing(self, people, hours):
        result = await appointments.open_slots(
            organization_id=people.org, day=DAY, now=NOW
        )
        assert result["status"] == "booking_off" and result["slots"] == []
        assert "call back" in result["say"]

    async def test_no_hours_is_needs_setup_not_all_day(self, people, monkeypatch):
        await _grant(people, booking="book")

        async def nothing(_org, _workflow, _run=None):
            return None

        monkeypatch.setattr(appointments, "_schedule", nothing)
        result = await appointments.open_slots(
            organization_id=people.org, day=DAY, now=NOW
        )
        assert result["status"] == "needs_setup" and result["slots"] == []

    async def test_slots_inside_hours_minus_bookings(self, people, hours):
        await _grant(people, booking="book", duration_minutes=30)
        first = await appointments.open_slots(
            organization_id=people.org, day=DAY, now=NOW
        )
        assert first["slots"] == ["09:00", "09:15", "09:30", "09:45", "10:00", "10:15"]
        assert first["may_book"] is True and first["timezone"] == "Asia/Kolkata"
        await _book(people, "09:30")
        after = await appointments.open_slots(
            organization_id=people.org, day=DAY, now=NOW, limit=20
        )
        assert after["slots"] == ["09:00", "10:00", "10:15", "10:30"]

    async def test_lead_time_and_horizon(self, people, hours):
        await _grant(people, booking="suggest", lead_minutes=60, horizon_days=1)
        today = await appointments.open_slots(
            organization_id=people.org, day=DAY, now=_at("09:20")
        )
        assert today["slots"] == ["10:30"]
        assert today["may_book"] is False
        later = await appointments.open_slots(
            organization_id=people.org, day=DAY + timedelta(days=3), now=_at("09:20")
        )
        assert later["status"] == "outside_policy"


@pytest.mark.asyncio
class TestBooking:
    async def test_suggest_never_books(self, people, hours):
        await _grant(people, booking="suggest")
        with pytest.raises(appointments.BookingRefused) as refused:
            await _book(people)
        assert refused.value.code == "booking_not_allowed"

    async def test_details_are_asked_not_guessed(self, people, hours):
        await _grant(people, booking="book")
        with pytest.raises(appointments.BookingRefused) as refused:
            await _book(people, caller_name="", reason="")
        assert refused.value.code == "details_missing"
        assert "name" in str(refused.value) and "reason" in str(refused.value)

    async def test_known_caller_rule(self, people, hours):
        await _grant(people, booking="book", verification="known_caller")
        with pytest.raises(appointments.BookingRefused) as refused:
            await _book(people)
        assert refused.value.code == "caller_not_verified"
        booked = await _book(people, caller_known=True)
        assert booked["status"] == "booked"

    async def test_books_and_stores_the_details(self, people, hours):
        await _grant(people, booking="book", services=["Cleaning"])
        booked = await _book(people, "10:00", service="Cleaning")
        assert booked["caller_number"] == "+919876543210"
        assert booked["starts_at"].startswith("2031-03-03T04:30")  # 10:00 IST in UTC
        assert [a["id"] for a in await appointments.upcoming(people.org)] == [
            booked["id"]
        ]

    async def test_an_unknown_service_is_refused(self, people, hours):
        await _grant(people, booking="book", services=["Cleaning"])
        with pytest.raises(appointments.BookingRefused) as refused:
            await _book(people, service="Surgery")
        assert refused.value.code == "unknown_service"

    async def test_a_time_outside_the_hours_is_refused(self, people, hours):
        await _grant(people, booking="book")
        with pytest.raises(appointments.BookingRefused) as refused:
            await _book(people, "15:00")
        assert refused.value.code == "slot_taken"

    async def test_no_double_booking_even_at_once(self, people, hours):
        await _grant(people, booking="book")
        results = await asyncio.gather(
            _book(people, "09:00"),
            _book(people, "09:15", caller_name="Ravi"),
            return_exceptions=True,
        )
        booked = [r for r in results if isinstance(r, dict)]
        refused = [r for r in results if isinstance(r, appointments.BookingRefused)]
        assert len(booked) == 1 and len(refused) == 1
        assert refused[0].code == "slot_taken"

    async def test_another_workspace_neither_sees_nor_blocks(self, people, hours):
        await _grant(people, booking="book")
        mine = await _book(people, "09:00")
        await appointments.save_policy(
            people.other, {"booking": "book"}, revision=0, user_id=people.c.id
        )
        theirs = await _book(people, "09:00", org=people.other)
        assert theirs["id"] != mine["id"]
        assert [a["id"] for a in await appointments.upcoming(people.other)] == [
            theirs["id"]
        ]


@pytest.mark.asyncio
class TestTheCallsTool:
    async def test_never_raises_and_says_something(self, people, hours, monkeypatch):
        all_on(monkeypatch)
        await _grant(people, booking="book")
        bad = await appointments.run_tool(
            appointments.BOOK_TOOL,
            {"date": "soon"},
            organization_id=people.org,
            workflow_id=None,
            workflow_run_id=None,
        )
        assert bad["status"] == "refused" and bad["say"]

    async def test_books_from_a_call(self, people, hours, monkeypatch):
        all_on(monkeypatch)
        await _grant(people, booking="book")
        monkeypatch.setattr(
            appointments,
            "datetime",
            type(
                "FrozenDatetime",
                (datetime,),
                {"now": staticmethod(lambda tz=None: NOW)},
            ),
        )
        result = await appointments.run_tool(
            appointments.BOOK_TOOL,
            {
                "date": DAY.isoformat(),
                "time": "10:15",
                "name": "Asha",
                "phone_number": "9876543210",
                "reason": "Pain",
            },
            organization_id=people.org,
            workflow_id=None,
            workflow_run_id=None,
            call_context={"trigger_source": "call_for_me"},
        )
        assert result["status"] == "booked", result
        [booked] = await appointments.upcoming(people.org)
        assert booked["source"] == "call_for_me"

    async def test_offers_no_way_to_read_a_booking(self):
        names = {f["name"] for f in appointments.function_schemas()}
        assert names == {"appointment_slots", "book_appointment", "escalate_to_person"}

    async def test_off_says_unavailable(self, people):
        result = await appointments.run_tool(
            appointments.SLOTS_TOOL,
            {"date": DAY.isoformat()},
            organization_id=people.org,
            workflow_id=None,
            workflow_run_id=None,
        )
        assert result["status"] == "unavailable"

    async def test_escalation_leaves_a_line_and_says_whether_to_transfer(
        self, people, monkeypatch
    ):
        recorded = []

        async def record(**kw):
            recorded.append(kw)

        from api.services.workflow import agent_timeline

        monkeypatch.setattr(agent_timeline, "record", record)
        none = await appointments.escalate(
            organization_id=people.org,
            reason="Asked about a bill",
            caller_number="+919876543210",
            workflow_run_id=None,
        )
        assert none["transfer_to"] is None and "call them back" in none["say"]
        await _grant(people, escalate_to="+91 98765 00000")
        put_through = await appointments.escalate(
            organization_id=people.org,
            reason="Wants a person",
            caller_number=None,
            workflow_run_id=None,
        )
        assert put_through["transfer_to"] == "+919876500000"
        assert recorded[0]["summary"] == "A caller needs a person: Asked about a bill"
        assert recorded[0]["in_channel"] is False
