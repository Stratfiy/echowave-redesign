"""The schedule inside "every morning at 8am".

The defect, found by asking the live product to build the bot: the routine
runtime is complete -- a clock every minute, a runner, four cadences, three
anchors -- and nothing created a routine from what somebody said. "Reads my
Gmail every morning at 8am" produced a live bot whose triggers list was empty.
The eight o'clock survived only as prose in the spec, where no clock reads it.
"""

from api.services.workflow.routines import Anchor, Cadence
from api.services.workflow.schedule_from_words import parse


class TestTheRealCase:
    def test_the_words_that_built_a_bot_that_never_runs(self):
        found = parse(
            "Reads my Gmail every morning at 8am and posts me a short summary "
            "of what came in overnight."
        )
        assert found is not None
        assert found.cadence is Cadence.DAILY
        assert found.anchor is Anchor.CLOCK
        assert found.at_minute == 8 * 60


class TestMorningIsNotEightOClock:
    """The distinction Anchor exists for.

    Somebody who says "every morning" means when the shop opens, and the
    shop's opening time is recorded and changes. Pinning it to 09:00 goes
    quietly wrong the week a clinic moves to 10:00 -- the report still
    arrives, an hour before anybody is there to read it.
    """

    def test_morning_with_no_time_is_the_businesss_own_opening(self):
        assert parse("every morning post a summary").anchor is Anchor.OPENING

    def test_evening_with_no_time_is_its_closing(self):
        assert parse("every evening send the takings").anchor is Anchor.CLOSING

    def test_before_you_close_is_closing(self):
        assert parse("every day before you close, chase unpaid bills").anchor is (
            Anchor.CLOSING
        )

    def test_when_we_open_is_opening(self):
        assert parse("every day when we open, read the inbox").anchor is Anchor.OPENING

    def test_a_stated_time_wins_over_the_word(self):
        """Somebody who types 8am means 08:00, whatever else they wrote."""
        found = parse("every morning at 8am")
        assert found.anchor is Anchor.CLOCK
        assert found.at_minute == 480


class TestCadence:
    def test_hourly(self):
        assert parse("every hour, sweep the queue").cadence is Cadence.HOURLY

    def test_weekdays(self):
        assert parse("every weekday at 9am").cadence is Cadence.WEEKDAYS

    def test_monday_to_friday_is_weekdays(self):
        assert parse("monday to friday at 9am").cadence is Cadence.WEEKDAYS

    def test_weekly_carries_the_day(self):
        found = parse("every Tuesday at 10am")
        assert found.cadence is Cadence.WEEKLY
        assert found.weekday == 1

    def test_sunday_is_six(self):
        """0 = Monday, matching date.weekday()."""
        assert parse("every Sunday at 10am").weekday == 6

    def test_daily_is_the_default_shape(self):
        assert parse("every day at noon-ish, 12pm").cadence is Cadence.DAILY


class TestReadingTheTime:
    def test_am(self):
        assert parse("every day at 8am").at_minute == 480

    def test_pm(self):
        assert parse("every day at 5pm").at_minute == 17 * 60

    def test_twelve_pm_is_midday(self):
        assert parse("every day at 12pm").at_minute == 12 * 60

    def test_twelve_am_is_midnight(self):
        assert parse("every day at 12am").at_minute == 0

    def test_minutes(self):
        assert parse("every day at 9:30am").at_minute == 9 * 60 + 30

    def test_twenty_four_hour(self):
        assert parse("every day at 17:45").at_minute == 17 * 60 + 45

    def test_an_impossible_time_is_not_a_time(self):
        """25:00 is a typo, not a schedule. It falls back to the anchor."""
        assert parse("every day at 25:00").anchor is Anchor.OPENING

    def test_hourly_keeps_only_the_minute_past_the_hour(self):
        assert parse("every hour at 8:15am").at_minute == 15


class TestSilenceIsAnAnswer:
    """A spec with no schedule builds the bot exactly as before.

    Guessing a daily run for a bot nobody asked to schedule would be worse
    than the gap this closes.
    """

    def test_no_schedule_is_none(self):
        assert parse("answer the phone and book appointments") is None

    def test_empty_is_none(self):
        assert parse("") is None
        assert parse(None) is None
        assert parse("   ") is None

    def test_a_bare_number_is_not_a_time(self):
        """ "summarise 5 emails" must not become a five o'clock routine."""
        assert parse("summarise 5 emails each time") is None

    def test_a_one_off_errand_is_not_a_standing_order(self):
        """ "call them back at 5pm" is one errand, not a routine."""
        assert parse("call them back at 5pm") is None


class TestSayingItBack:
    """A card that is about to arm a schedule has to name it."""

    def test_a_clock_time_reads_as_a_time(self):
        assert parse("every day at 8am").said == "every day at 08:00"

    def test_an_opening_anchor_reads_as_the_business_hours(self):
        assert parse("every morning").said == "every day when you open"

    def test_a_closing_anchor_says_so(self):
        assert parse("every evening").said == "every day before you close"

    def test_a_weekly_names_the_day(self):
        assert parse("every Monday at 9am").said == "every Monday at 09:00"

    def test_weekdays_reads_as_weekdays(self):
        assert parse("every weekday at 9am").said == "every weekday at 09:00"

    def test_hourly_reads_as_hourly(self):
        assert parse("every hour").said == "every hour"
