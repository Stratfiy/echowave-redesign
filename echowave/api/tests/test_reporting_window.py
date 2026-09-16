"""The window "today" is counted over, and the words used for it.

Decibyl told the founder "7 calls today" and, minutes later, "15 calls
today", off a window that was really ``now - 24 hours``. These pin down the
three ways that lied.
"""

from datetime import UTC, datetime, timedelta

from api.services import reporting_window as rw


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


class TestTodayIsACalendarDay:
    def test_today_starts_at_local_midnight_not_this_time_yesterday(self):
        # 14:53 in Chennai on the 16th. "Today" starts at 00:00 on the 16th
        # there, which is 18:30 UTC on the 15th -- not 09:23 UTC on the 15th,
        # which is what ``now - 24h`` would have given.
        window = rw.day_so_far("Asia/Kolkata", now=at("2026-09-16T09:23:00"))
        assert window.since == at("2026-09-15T18:30:00")
        assert window.label == "today"

    def test_the_window_does_not_move_as_the_clock_advances(self):
        # The whole complaint: two answers minutes apart disagreed because
        # yesterday's calls were falling out of a rolling window.
        first = rw.day_so_far("Asia/Kolkata", now=at("2026-09-16T09:23:00"))
        later = rw.day_so_far("Asia/Kolkata", now=at("2026-09-16T09:53:00"))
        assert first.since == later.since

    def test_a_rolling_window_would_have_moved(self):
        # Guards the test above from passing vacuously.
        first = at("2026-09-16T09:23:00") - timedelta(hours=24)
        later = at("2026-09-16T09:53:00") - timedelta(hours=24)
        assert first != later

    def test_midnight_is_the_operators_not_utc(self):
        # 20:00 in New York on the 15th is already the 16th in UTC. An
        # operator there asking about "today" means the 15th.
        window = rw.day_so_far("America/New_York", now=at("2026-09-16T00:30:00"))
        assert window.since == at("2026-09-15T04:00:00")


class TestAnUnknownZoneIsSaidRatherThanGuessed:
    def test_no_timezone_falls_back_to_utc_and_says_it_is_not_exact(self):
        window = rw.day_so_far(None, now=at("2026-09-16T09:23:00"))
        assert window.since == at("2026-09-16T00:00:00")
        assert window.exact is False

    def test_an_unloadable_zone_does_not_refuse_to_answer(self):
        # A bad row is a data problem. Refusing to report is worse than
        # reporting against UTC and marking it approximate.
        window = rw.day_so_far("Mars/Olympus_Mons", now=at("2026-09-16T09:23:00"))
        assert window.since == at("2026-09-16T00:00:00")
        assert window.exact is False

    def test_a_real_zone_is_exact(self):
        assert rw.day_so_far("Asia/Kolkata").exact is True


class TestARollingSpanIsNamedAsOne:
    def test_seven_days_is_not_called_this_week(self):
        # An operator reading "this week" on a Tuesday means since Monday.
        window = rw.last_days(7, now=at("2026-09-16T09:23:00"))
        assert window.label == "in the last 7 days"
        assert window.since == at("2026-09-09T09:23:00")

    def test_one_day_reads_as_a_day_not_days(self):
        assert rw.last_days(1).label == "in the last 1 day"
