"""The agent can ask what is free before it promises a time.

Before this the calendar tool could only *create*. An agent therefore guessed
availability from whatever its prompt said, offered a slot, and the only
correction was a refusal at write time. The live evidence was a clinic day
with three appointments and noon plainly empty: the caller asked for noon, was
refused, and the database recorded no tool call against noon at all -- the
agent never looked.

These tests are written against the platform, not against the account that
found the bug. Hours, timezone and calendar all come from whatever each
organization has configured; ``TestNothingHereIsAccountSpecific`` is the one
that keeps it that way.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.integrations.google_calendar import availability
from api.services.workflow import agent_hours

#: 9:30 to 13:00 in minutes past midnight.
MORNING = (570, 780)


def _event(start, end, **extra):
    body = {
        "status": "confirmed",
        "start": {"dateTime": f"2026-09-14T{start}:00+05:30"},
        "end": {"dateTime": f"2026-09-14T{end}:00+05:30"},
    }
    body.update(extra)
    return body


class TestOpenWindows:
    """``is_open`` answers "now"; offering a time needs "when"."""

    def test_a_configured_window_becomes_minutes(self):
        schedule = {
            "enabled": True,
            "slots": [{"day_of_week": 0, "start_time": "09:30", "end_time": "13:00"}],
        }
        assert agent_hours.open_windows(schedule, date(2026, 9, 14)) == [MORNING]

    def test_a_day_with_no_window_is_closed(self):
        schedule = {
            "enabled": True,
            "slots": [{"day_of_week": 0, "start_time": "09:30", "end_time": "13:00"}],
        }
        # Tuesday.
        assert agent_hours.open_windows(schedule, date(2026, 9, 15)) == []

    @pytest.mark.parametrize(
        "schedule",
        [
            None,
            {},
            {"enabled": False},
            {"enabled": True, "slots": []},
            {"enabled": True, "slots": [{"day_of_week": 0, "start_time": "oops"}]},
        ],
    )
    def test_hours_nobody_could_configure_mean_the_whole_day(self, schedule):
        """Same fail-open contract as ``is_open``. An organization that has set
        no hours must not have every slot refused -- its calendar alone decides
        what is free."""
        assert agent_hours.open_windows(schedule, date(2026, 9, 14)) == [
            agent_hours.DAY
        ]

    def test_two_windows_in_a_day_are_both_offered(self):
        """A clinic with a lunch break. Both halves have to be bookable."""
        schedule = {
            "enabled": True,
            "slots": [
                {"day_of_week": 0, "start_time": "09:30", "end_time": "13:00"},
                {"day_of_week": 0, "start_time": "17:00", "end_time": "20:00"},
            ],
        }
        assert agent_hours.open_windows(schedule, date(2026, 9, 14)) == [
            MORNING,
            (1020, 1200),
        ]

    def test_overlapping_windows_are_merged_not_offered_twice(self):
        schedule = {
            "enabled": True,
            "slots": [
                {"day_of_week": 0, "start_time": "09:00", "end_time": "12:00"},
                {"day_of_week": 0, "start_time": "11:00", "end_time": "14:00"},
            ],
        }
        assert agent_hours.open_windows(schedule, date(2026, 9, 14)) == [(540, 840)]

    def test_an_overnight_window_covers_both_sides_of_midnight(self):
        """A support line open 22:00 to 02:00 is open at one in the morning,
        which is the reading ``is_open`` already takes."""
        schedule = {
            "enabled": True,
            "slots": [{"day_of_week": 0, "start_time": "22:00", "end_time": "02:00"}],
        }
        monday = agent_hours.open_windows(schedule, date(2026, 9, 14))
        tuesday = agent_hours.open_windows(schedule, date(2026, 9, 15))
        assert monday == [(1320, 1440)]
        assert tuesday == [(0, 120)]


class TestFreeSlots:
    def test_the_slot_that_was_refused_is_offered(self):
        """The regression. Open 9:30-13:00, booked at 9:30, 11:00 and 11:30 --
        noon is free and must appear."""
        busy = [(570, 600), (660, 690), (690, 720)]
        slots = availability.free_slots(
            open_windows=[MORNING], busy=busy, duration_minutes=30
        )
        assert "12:00" in slots

    def test_a_taken_slot_is_never_offered(self):
        slots = availability.free_slots(
            open_windows=[MORNING], busy=[(660, 720)], duration_minutes=30
        )
        assert "11:00" not in slots and "11:30" not in slots

    def test_an_appointment_that_would_overrun_the_close_is_not_offered(self):
        """A 60-minute slot at 12:30 ends at 13:30, half an hour after the
        clinic shuts. Offering it books staff who have gone home."""
        # The whole day's offers rather than the first handful: the readable
        # cap would otherwise hide the boundary this test is about.
        slots = availability.free_slots(
            open_windows=[MORNING], busy=[], duration_minutes=60, limit=100
        )
        assert "12:00" in slots  # ends exactly at 13:00
        assert "12:30" not in slots  # would end at 13:30

    def test_starts_land_on_the_clock_not_on_the_opening_minute(self):
        """Open at 9:35 and the first offer is 9:45. A caller asked to choose
        between 9:35 and 9:50 hears a machine."""
        slots = availability.free_slots(
            open_windows=[(575, 780)], busy=[], duration_minutes=30
        )
        assert slots[0] == "09:45"

    def test_a_full_day_returns_nothing_rather_than_a_wrong_time(self):
        slots = availability.free_slots(
            open_windows=[MORNING], busy=[MORNING], duration_minutes=30
        )
        assert slots == []

    def test_the_list_stays_short_enough_to_read_out(self):
        slots = availability.free_slots(
            open_windows=[agent_hours.DAY], busy=[], duration_minutes=30
        )
        assert len(slots) == availability.MAX_SLOTS


class TestWhatCountsAsBusy:
    """One definition of busy, shared with the write. If availability and the
    conflict check disagreed, the agent would offer a slot and then fail to
    book it."""

    def _spans(self, items):
        return availability._busy_spans(
            items, availability._zone("Asia/Kolkata"), date(2026, 9, 14)
        )

    def test_a_real_appointment_blocks(self):
        assert self._spans([_event("11:00", "11:30")]) == [(660, 690)]

    def test_an_all_day_holiday_does_not_eat_the_day(self):
        """The same rule as the booking path. One Ganesh Chaturthi entry must
        not make every slot unavailable."""
        holiday = {
            "status": "confirmed",
            "summary": "Ganesh Chaturthi",
            "start": {"date": "2026-09-14"},
            "end": {"date": "2026-09-15"},
        }
        assert self._spans([holiday]) == []

    def test_an_event_marked_free_does_not_block(self):
        assert self._spans([_event("11:00", "11:30", transparency="transparent")]) == []

    def test_a_cancelled_event_does_not_block(self):
        assert self._spans([_event("11:00", "11:30", status="cancelled")]) == []

    def test_an_event_running_in_from_yesterday_still_occupies_this_morning(self):
        """Clamped to the day rather than dropped. Discarding it would free a
        chair that is taken."""
        overnight = {
            "status": "confirmed",
            "start": {"dateTime": "2026-09-13T22:00:00+05:30"},
            "end": {"dateTime": "2026-09-14T10:00:00+05:30"},
        }
        assert self._spans([overnight]) == [(0, 600)]

    def test_back_to_back_appointments_merge_into_one_span(self):
        spans = self._spans([_event("11:00", "11:30"), _event("11:30", "12:00")])
        assert spans == [(660, 720)]


class TestTheToolContract:
    def _tool(self):
        return SimpleNamespace(
            name="Book appointment", tool_uuid="u1", description="Books a slot."
        )

    def test_the_two_functions_cannot_collide(self):
        """One configured tool yields two functions. Two functions with one
        name would be resolved by the model calling whichever it saw last."""
        from api.services.integrations.google_calendar.client import (
            google_calendar_function_schema,
        )

        tool = self._tool()
        booking = google_calendar_function_schema(tool)["function"]["name"]
        checking = availability.function_schema(tool)["function"]["name"]
        assert booking != checking

    @pytest.mark.asyncio
    async def test_a_date_it_cannot_read_is_refused_with_instructions(self):
        result = await availability.execute_check_availability(
            self._tool(), {"date": "next Monday"}, organization_id=7
        )
        assert result["status"] == "error"
        assert "YYYY-MM-DD" in result["error"]

    @pytest.mark.asyncio
    async def test_a_closed_day_is_a_definite_answer_not_an_error(self):
        """So the agent offers a different day rather than apologising."""
        with (
            patch.object(
                availability,
                "resolve_open_windows",
                AsyncMock(return_value=[]),
            ),
            patch(
                "api.services.integrations.google_calendar.client.booking_timezone",
                AsyncMock(return_value="Asia/Kolkata"),
            ),
        ):
            result = await availability.execute_check_availability(
                self._tool(), {"date": "2026-09-15"}, organization_id=7
            )
        assert result["status"] == "success"
        assert result["data"]["open"] is False
        assert result["data"]["free"] == []

    @pytest.mark.asyncio
    async def test_a_calendar_it_could_not_read_never_reports_a_free_day(self):
        """The one place this departs from the fail-open posture elsewhere.
        Answering "everything is free" from a failed read has the agent promise
        slots confidently and wrongly; "I could not check" leaves it able to
        take a message."""
        from api.services.integrations.google_calendar import oauth as gcal_oauth

        with (
            patch.object(
                availability, "resolve_open_windows", AsyncMock(return_value=[MORNING])
            ),
            patch(
                "api.services.integrations.google_calendar.client.booking_timezone",
                AsyncMock(return_value="Asia/Kolkata"),
            ),
            patch(
                "api.services.integrations.google_calendar.oauth.get_valid_access_token",
                AsyncMock(return_value="token"),
            ),
            patch(
                "api.services.integrations.google_calendar.oauth.get_status",
                AsyncMock(
                    return_value=gcal_oauth.ConnectionStatus(
                        connected=True,
                        connected_email=None,
                        calendar_id="primary",
                        updated_at=None,
                    )
                ),
            ),
            patch("api.db.db_client") as mock_db,
            patch.object(availability, "busy_events", AsyncMock(return_value=None)),
        ):
            mock_db.async_session.return_value.__aenter__.return_value = AsyncMock()
            result = await availability.execute_check_availability(
                self._tool(), {"date": "2026-09-14"}, organization_id=7
            )
        assert result["status"] == "error"
        assert "not promise" in result["error"]


class TestNothingHereIsAccountSpecific:
    """The fix has to work for every customer, not the one that found it.

    Hours come from each agent's own schedule or its organization's, the
    timezone from the organization, the calendar from the organization's own
    connection. Nothing is defaulted to a clinic's timetable, an industry's
    working week, or one account's id.
    """

    def test_no_hardcoded_hours_or_account_names(self):
        import pathlib

        source = pathlib.Path(availability.__file__).read_text()
        # Times appear in docstrings and tests, never as configuration.
        for forbidden in ("narayani", "Narayani", "dental", "clinic_id"):
            assert forbidden not in source, forbidden

    def test_the_default_duration_matches_the_booking_tool(self):
        """A slot offered has to be a slot that will fit. Two different
        defaults would offer 30 minutes and book 45."""
        from api.services.integrations.google_calendar.client import (
            FUNCTION_PARAMETERS as BOOKING,
        )

        assert (
            "Defaults to 30" in BOOKING["properties"]["duration_minutes"]["description"]
        )
        assert availability.DEFAULT_DURATION_MINUTES == 30

    def test_an_organization_with_no_hours_configured_can_still_be_booked(self):
        """The common case for a new account: nothing set up, a connected
        calendar, and it must still offer times."""
        windows = agent_hours.open_windows(None, date(2026, 9, 14))
        slots = availability.free_slots(
            open_windows=windows, busy=[], duration_minutes=30
        )
        assert slots
