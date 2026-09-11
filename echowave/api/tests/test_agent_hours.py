"""Whether an agent is open, as the platform's own answer.

Narayani's hours live in its prompt, so the agent can say them and the
platform cannot keep them: a call at eleven at night is answered and a slot
agreed that nobody will honour. These pin the rule and, mostly, the ways it
must not misfire -- every one of which ends with a number off the air, which
is worse than the bug it would be fixing.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api.services.workflow.agent_hours import describe, is_open

IST = ZoneInfo("Asia/Kolkata")


def _at(day: int, hhmm: str) -> datetime:
    """A moment on a known weekday. 2026-09-07 is a Monday."""
    hour, minute = (int(p) for p in hhmm.split(":"))
    return datetime(2026, 9, 7 + day, hour, minute, tzinfo=IST)


def _clinic():
    """Narayani's actual hours: Mon-Sat, 9:30-13:00 and 16:00-20:00."""
    return {
        "enabled": True,
        "timezone": "Asia/Kolkata",
        "slots": [
            {"day_of_week": d, "start_time": s, "end_time": e}
            for d in range(0, 6)
            for s, e in (("09:30", "13:00"), ("16:00", "20:00"))
        ],
    }


class TestTheClinic:
    def test_open_in_the_morning_surgery(self):
        assert is_open(_clinic(), _at(0, "10:00")) is True

    def test_shut_over_lunch(self):
        assert is_open(_clinic(), _at(0, "14:30")) is False

    def test_open_again_in_the_evening(self):
        assert is_open(_clinic(), _at(0, "18:00")) is True

    def test_shut_at_eleven_at_night(self):
        """The call that started this."""
        assert is_open(_clinic(), _at(0, "23:00")) is False

    def test_shut_on_sunday(self):
        assert is_open(_clinic(), _at(6, "10:00")) is False

    def test_open_on_saturday(self):
        assert is_open(_clinic(), _at(5, "10:00")) is True

    def test_the_boundaries(self):
        """Open at the opening minute, shut at the closing one."""
        assert is_open(_clinic(), _at(0, "09:30")) is True
        assert is_open(_clinic(), _at(0, "09:29")) is False
        assert is_open(_clinic(), _at(0, "12:59")) is True
        assert is_open(_clinic(), _at(0, "13:00")) is False


class TestAWindowThatCrossesMidnight:
    """A night shift is an ordinary thing to want, and the campaign
    scheduler's string comparison cannot express one."""

    def _night(self):
        return {
            "enabled": True,
            "timezone": "Asia/Kolkata",
            "slots": [{"day_of_week": 0, "start_time": "22:00", "end_time": "02:00"}],
        }

    def test_open_before_midnight(self):
        assert is_open(self._night(), _at(0, "23:30")) is True

    def test_still_open_after_midnight(self):
        """Tuesday at 01:00 is Monday's slot, still running."""
        assert is_open(self._night(), _at(1, "01:00")) is True

    def test_shut_once_the_window_ends(self):
        assert is_open(self._night(), _at(1, "02:00")) is False

    def test_shut_in_the_afternoon(self):
        assert is_open(self._night(), _at(0, "15:00")) is False


class TestOpenIsAlwaysTheSafeAnswer:
    """Every one of these, answered the other way, takes a number off the air
    for a customer who never asked for a schedule."""

    def test_no_schedule_at_all(self):
        assert is_open(None) is True
        assert is_open({}) is True

    def test_a_schedule_that_is_switched_off(self):
        assert is_open({"enabled": False, "slots": [{"day_of_week": 0}]}) is True

    def test_enabled_with_no_slots(self):
        assert is_open({"enabled": True, "slots": []}) is True
        assert is_open({"enabled": True}) is True

    def test_an_unreadable_timezone(self):
        schedule = _clinic()
        schedule["timezone"] = "Mars/Olympus"
        assert is_open(schedule, _at(6, "03:00")) is True

    def test_something_that_is_not_a_schedule(self):
        assert is_open("weekdays") is True
        assert is_open(["mon"]) is True

    @pytest.mark.parametrize(
        "slot",
        [
            {"day_of_week": 0, "start_time": "nine", "end_time": "13:00"},
            {"day_of_week": 0, "start_time": "09:30"},
            {"day_of_week": 0, "start_time": "25:00", "end_time": "26:00"},
            "monday morning",
        ],
    )
    def test_a_malformed_slot_is_skipped_not_crashed(self, slot):
        """Skipped, so the other slots still decide. A schedule whose only
        slot is malformed has no usable slots and the agent stays open."""
        assert is_open({"enabled": True, "slots": [slot]}, _at(0, "10:00")) is True


class TestAZeroLengthWindow:
    def test_reads_as_closed_rather_than_always(self):
        """An operator who wanted all day switches the schedule off; one who
        typed the same time twice made a mistake, and 'always open' is the
        more surprising way to be wrong."""
        schedule = {
            "enabled": True,
            "slots": [{"day_of_week": 0, "start_time": "09:00", "end_time": "09:00"}],
        }
        assert is_open(schedule, _at(0, "09:00")) is False


class TestDescribing:
    def test_it_says_always_open_when_there_is_no_schedule(self):
        assert describe(None) == "always open"
        assert describe({"enabled": False}) == "always open"

    def test_it_counts_the_windows(self):
        assert "12 window(s)" in describe(_clinic())
