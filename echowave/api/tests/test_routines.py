"""When a routine runs, and every named way it does not.

A bot on a routine is one nobody rings. There is no caller to notice a run that did
not happen and no transcript to explain it, so the schedule is the product and
a silent skip is the failure mode. Every test here is about a moment.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from api.services.workflow.routines import (
    ATTENTION_SKIPS,
    CATCH_UP_MINUTES,
    Anchor,
    Cadence,
    RoutineSpec,
    SkipReason,
    decide,
    may_arm,
    next_slot,
    targets_for_day,
)

IST = ZoneInfo("Asia/Kolkata")

#: Mon-Fri, 09:30 to 18:00. The shape of every clinic we have.
WEEKDAY_HOURS = {
    "enabled": True,
    "slots": [
        {"day_of_week": day, "start_time": "09:30", "end_time": "18:00"}
        for day in range(0, 5)
    ],
}


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def armed(**kwargs):
    """A routine that is switched on and has been test-run."""
    base = {
        "cadence": Cadence.DAILY,
        "anchor": Anchor.OPENING,
        "is_active": True,
        "tested_at": at(2026, 1, 1, 12),
    }
    base.update(kwargs)
    return RoutineSpec(**base)


class TestArming:
    """A routine arms only after a test run."""

    def test_an_untested_routine_may_not_arm(self):
        assert may_arm(RoutineSpec(cadence=Cadence.DAILY)) is False

    def test_a_tested_routine_may_arm(self):
        assert may_arm(RoutineSpec(cadence=Cadence.DAILY, tested_at=at(2026, 1, 1, 9)))

    def test_an_active_but_untested_routine_never_fires(self):
        # Belt and braces: the route should refuse the toggle, and the runtime
        # refuses again. The first time a bot runs unsupervised it writes
        # into somebody's real accounting software.
        spec = RoutineSpec(cadence=Cadence.DAILY, is_active=True, tested_at=None)
        decision = decide(
            spec, now=at(2026, 9, 14, 10), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.fire is False
        assert decision.reason is SkipReason.NEVER_TESTED


class TestAnchoredToTheBusiness:
    """ "Every morning" means when the shop opens, not 09:00."""

    def test_opening_anchor_follows_the_configured_hours(self):
        # Monday. Opens 09:30, so that is the slot -- not nine o'clock.
        assert targets_for_day(
            armed(), datetime(2026, 9, 14, tzinfo=IST).date(), WEEKDAY_HOURS
        ) == [9 * 60 + 30]

    def test_moving_the_opening_time_moves_the_run(self):
        # The bug a cron string would have: the clinic moves to 10:00 and the
        # report keeps arriving at 09:30, an hour before anybody is there.
        later = {
            "enabled": True,
            "slots": [{"day_of_week": 0, "start_time": "10:00", "end_time": "18:00"}],
        }
        assert targets_for_day(
            armed(), datetime(2026, 9, 14, tzinfo=IST).date(), later
        ) == [10 * 60]

    def test_closing_anchor_with_a_negative_offset_runs_before_shutting(self):
        spec = armed(anchor=Anchor.CLOSING, offset_minutes=-30)
        assert targets_for_day(
            spec, datetime(2026, 9, 14, tzinfo=IST).date(), WEEKDAY_HOURS
        ) == [17 * 60 + 30]

    def test_an_offset_past_midnight_sticks_to_the_end_of_its_own_day(self):
        # Clamped rather than wrapped: a run belongs to the day whose closing
        # it follows, not to 00:30 the next one where it reads as another
        # day's figures.
        spec = armed(anchor=Anchor.CLOSING, offset_minutes=12 * 60)
        assert targets_for_day(
            spec, datetime(2026, 9, 14, tzinfo=IST).date(), WEEKDAY_HOURS
        ) == [24 * 60 - 1]

    def test_unknown_hours_fail_open_to_the_whole_day(self):
        # Same contract as agent_hours: a business nobody configured must not
        # have every run refused.
        assert targets_for_day(
            armed(), datetime(2026, 9, 14, tzinfo=IST).date(), None
        ) == [0]

    def test_a_clock_anchor_is_honoured_when_the_business_is_shut(self):
        # Somebody who typed 06:00 for a routine that sweeps yesterday's orders
        # meant 06:00. Refusing it would be us overruling them.
        spec = armed(anchor=Anchor.CLOCK, at_minute=6 * 60)
        assert targets_for_day(
            spec, datetime(2026, 9, 14, tzinfo=IST).date(), WEEKDAY_HOURS
        ) == [6 * 60]

    def test_a_clock_anchor_is_honoured_on_a_closed_day(self):
        spec = armed(anchor=Anchor.CLOCK, at_minute=6 * 60)
        # Sunday.
        assert targets_for_day(
            spec, datetime(2026, 9, 13, tzinfo=IST).date(), WEEKDAY_HOURS
        ) == [6 * 60]


class TestCadence:
    def test_weekdays_skips_saturday_and_sunday(self):
        spec = armed(cadence=Cadence.WEEKDAYS)
        assert (
            targets_for_day(
                spec, datetime(2026, 9, 19, tzinfo=IST).date(), WEEKDAY_HOURS
            )
            == []
        )

    def test_weekly_runs_only_on_its_own_weekday(self):
        spec = armed(cadence=Cadence.WEEKLY, weekday=2)
        monday = datetime(2026, 9, 14, tzinfo=IST).date()
        wednesday = datetime(2026, 9, 16, tzinfo=IST).date()
        assert targets_for_day(spec, monday, WEEKDAY_HOURS) == []
        assert targets_for_day(spec, wednesday, WEEKDAY_HOURS) == [9 * 60 + 30]

    def test_hourly_runs_only_while_the_business_is_open(self):
        # An hourly sweep running through the night would bill twenty-four
        # runs of nothing and bury the eight that mattered.
        spec = armed(cadence=Cadence.HOURLY, at_minute=15)
        slots = targets_for_day(
            spec, datetime(2026, 9, 14, tzinfo=IST).date(), WEEKDAY_HOURS
        )
        assert slots == [h * 60 + 15 for h in range(10, 18)]

    def test_hourly_reads_only_the_minute_past_the_hour(self):
        spec = armed(cadence=Cadence.HOURLY, at_minute=9 * 60 + 45)
        slots = targets_for_day(
            spec, datetime(2026, 9, 14, tzinfo=IST).date(), WEEKDAY_HOURS
        )
        assert slots[0] == 9 * 60 + 45


class TestFiring:
    def test_it_fires_at_its_slot(self):
        decision = decide(
            armed(), now=at(2026, 9, 14, 9, 30), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.fire is True
        assert decision.slot == at(2026, 9, 14, 9, 30)

    def test_it_does_not_fire_before_its_slot(self):
        decision = decide(
            armed(), now=at(2026, 9, 14, 9, 0), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.fire is False
        assert decision.reason is SkipReason.NOT_YET
        # The slot still comes back, so a screen can say when.
        assert decision.slot == at(2026, 9, 14, 9, 30)
        assert "09:30" in decision.detail

    def test_it_does_not_fire_twice_for_one_slot(self):
        # The whole reason a minute tick is safe.
        spec = armed(last_fired_at=at(2026, 9, 14, 9, 30))
        decision = decide(
            spec, now=at(2026, 9, 14, 9, 35), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.fire is False
        assert decision.reason is SkipReason.ALREADY_RAN

    def test_yesterdays_run_does_not_satisfy_today(self):
        spec = armed(last_fired_at=at(2026, 9, 14, 9, 30))
        decision = decide(
            spec, now=at(2026, 9, 15, 9, 30), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.fire is True

    def test_an_hourly_routine_fires_once_per_hour(self):
        spec = armed(
            cadence=Cadence.HOURLY, at_minute=0, last_fired_at=at(2026, 9, 14, 10)
        )
        assert (
            decide(
                spec,
                now=at(2026, 9, 14, 10, 30),
                zone=IST,
                business_hours=WEEKDAY_HOURS,
            ).reason
            is SkipReason.ALREADY_RAN
        )
        assert (
            decide(
                spec, now=at(2026, 9, 14, 11), zone=IST, business_hours=WEEKDAY_HOURS
            ).fire
            is True
        )


class TestLateness:
    """Late is better than missing, but not indefinitely."""

    def test_a_short_delay_still_sends(self):
        decision = decide(
            armed(),
            now=at(2026, 9, 14, 9, 30) + timedelta(minutes=CATCH_UP_MINUTES),
            zone=IST,
            business_hours=WEEKDAY_HOURS,
        )
        assert decision.fire is True

    def test_a_long_outage_does_not_send_a_morning_report_in_the_afternoon(self):
        decision = decide(
            armed(), now=at(2026, 9, 14, 15), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.fire is False
        assert decision.reason is SkipReason.MISSED

    def test_a_missed_run_is_worth_telling_somebody_about(self):
        # The one skip that means something went wrong. If this were not in
        # ATTENTION_SKIPS a bot could stop for a day in silence, which is the
        # exact failure this whole module is built against.
        assert SkipReason.MISSED in ATTENTION_SKIPS
        decision = decide(
            armed(), now=at(2026, 9, 14, 15), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.needs_attention is True

    def test_an_ordinary_skip_is_not_an_alert(self):
        for reason in (
            SkipReason.NOT_YET,
            SkipReason.ALREADY_RAN,
            SkipReason.CLOSED,
            SkipReason.WRONG_DAY,
            SkipReason.INACTIVE,
        ):
            assert reason not in ATTENTION_SKIPS


class TestNotRunning:
    def test_a_switched_off_routine_says_so_rather_than_saying_closed(self):
        # Order of the checks matters: "closed today" is a true statement that
        # answers the wrong question for somebody who switched it off.
        spec = armed(is_active=False)
        decision = decide(
            spec, now=at(2026, 9, 13, 10), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.reason is SkipReason.INACTIVE

    def test_a_closed_day_is_closed_not_a_missed_run(self):
        # Sunday. Not a failure, and must never be reported as one.
        decision = decide(
            armed(), now=at(2026, 9, 13, 10), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.fire is False
        assert decision.reason is SkipReason.CLOSED
        assert decision.needs_attention is False

    def test_the_wrong_weekday_is_told_apart_from_a_closed_day(self):
        # A Wednesday routine on a Monday the clinic is open. "Closed today"
        # would be a lie, and the difference is "it is Sunday" versus "your
        # Monday report is broken".
        spec = armed(cadence=Cadence.WEEKLY, weekday=2)
        decision = decide(
            spec, now=at(2026, 9, 14, 10), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.reason is SkipReason.WRONG_DAY

    def test_a_broken_connector_stops_the_run_and_names_the_app(self):
        # Running anyway produces a run that reports nothing, which looks to
        # the operator exactly like the bot being broken.
        spec = armed(needs_apps=("shopify", "gmail"))
        decision = decide(
            spec,
            now=at(2026, 9, 14, 9, 30),
            zone=IST,
            business_hours=WEEKDAY_HOURS,
            broken_apps=["shopify"],
        )
        assert decision.fire is False
        assert decision.reason is SkipReason.CONNECTOR_BROKEN
        assert "shopify" in decision.detail
        assert decision.needs_attention is True

    def test_an_unrelated_broken_connector_does_not_stop_the_run(self):
        spec = armed(needs_apps=("shopify",))
        decision = decide(
            spec,
            now=at(2026, 9, 14, 9, 30),
            zone=IST,
            business_hours=WEEKDAY_HOURS,
            broken_apps=["hubspot"],
        )
        assert decision.fire is True


class TestZones:
    def test_the_moment_is_read_in_the_organisations_zone(self):
        # 04:00 UTC is exactly the 09:30 IST slot. Judging it in UTC would
        # read minute 240, before the 570 the window opens at, and skip a due
        # run as NOT_YET.
        utc_now = datetime(2026, 9, 14, 4, 0, tzinfo=ZoneInfo("UTC"))
        decision = decide(
            armed(last_fired_at=None),
            now=utc_now,
            zone=IST,
            business_hours=WEEKDAY_HOURS,
        )
        assert decision.fire is True

    def test_a_last_fired_stamp_in_utc_is_compared_in_the_local_zone(self):
        spec = armed(last_fired_at=datetime(2026, 9, 14, 4, 0, tzinfo=ZoneInfo("UTC")))
        # 04:00 UTC is 09:30 IST -- the slot itself.
        decision = decide(
            spec, now=at(2026, 9, 14, 9, 35), zone=IST, business_hours=WEEKDAY_HOURS
        )
        assert decision.reason is SkipReason.ALREADY_RAN


class TestNextSlot:
    def test_it_names_the_next_run(self):
        assert next_slot(
            armed(), now=at(2026, 9, 14, 10), zone=IST, business_hours=WEEKDAY_HOURS
        ) == at(2026, 9, 15, 9, 30)

    def test_it_skips_the_weekend_for_a_weekday_routine(self):
        spec = armed(cadence=Cadence.WEEKDAYS)
        # Friday afternoon, so the next one is Monday.
        assert next_slot(
            spec, now=at(2026, 9, 18, 14), zone=IST, business_hours=WEEKDAY_HOURS
        ) == at(2026, 9, 21, 9, 30)

    def test_it_returns_none_when_nothing_is_ever_due(self):
        # A Sunday routine for a business that never opens on Sunday. Better
        # shown as "never" than as a date it will not honour.
        spec = armed(cadence=Cadence.WEEKLY, weekday=6)
        assert (
            next_slot(
                spec, now=at(2026, 9, 14, 10), zone=IST, business_hours=WEEKDAY_HOURS
            )
            is None
        )
