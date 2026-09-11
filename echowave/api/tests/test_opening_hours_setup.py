"""Opening hours asked once, kept by the platform, and spoken by the agent.

Hours were a setup question already -- and a free-text one, rendered into the
prompt. So the agent could recite the clinic's hours and the platform could
not keep them: a call at eleven at night was answered and a slot agreed that
nobody would honour.

The fix is not to parse the sentence the operator typed. It is to ask for the
week, and derive the sentence from it. Structure is the source; prose is
downstream. The other direction means a misreading silently closes a number,
which is the failure this platform guards against everywhere else.
"""

from api.services.workflow.setup_fields import KNOWN_FIELDS, hours_sentence


def _schedule(*slots, enabled=True):
    return {
        "enabled": enabled,
        "timezone": "Asia/Kolkata",
        "slots": [
            {"day_of_week": d, "start_time": s, "end_time": e} for d, s, e in slots
        ],
    }


class TestTheQuestionAsked:
    def test_hours_are_collected_as_hours_not_as_a_sentence(self):
        assert KNOWN_FIELDS["opening_hours"].kind == "hours"

    def test_everything_else_is_still_text(self):
        assert KNOWN_FIELDS["business_name"].kind == "text"


class TestTheSentenceTheAgentSays:
    def test_narayani(self):
        """The clinic's real hours, and the line its prompt hand-writes today."""
        clinic = _schedule(
            *[
                (d, s, e)
                for d in range(6)
                for s, e in (("09:30", "13:00"), ("16:00", "20:00"))
            ]
        )
        assert (
            hours_sentence(clinic)
            == "Monday to Saturday, 9:30 am to 1 pm and 4 pm to 8 pm."
        )

    def test_days_that_differ_are_said_separately(self):
        weekdays_and_saturday = _schedule(
            *[(d, "10:00", "19:00") for d in range(5)], (5, "10:00", "14:00")
        )
        assert hours_sentence(weekdays_and_saturday) == (
            "Monday to Friday, 10 am to 7 pm. Saturday, 10 am to 2 pm."
        )

    def test_one_day_alone_is_not_a_range(self):
        assert hours_sentence(_schedule((2, "09:00", "17:00"))) == (
            "Wednesday, 9 am to 5 pm."
        )

    def test_a_night_shift_reads_as_one(self):
        every_night = _schedule(*[(d, "22:00", "02:00") for d in range(7)])
        assert hours_sentence(every_night) == "Monday to Sunday, 10 pm to 2 am."

    def test_noon_and_midnight_are_words(self):
        """ "12 pm to 12 am" is how a clock talks, not a receptionist."""
        assert hours_sentence(_schedule((0, "12:00", "00:00"))) == (
            "Monday, noon to midnight."
        )

    def test_a_half_hour_keeps_its_minutes(self):
        assert "9:30 am" in hours_sentence(_schedule((0, "09:30", "17:00")))


class TestWhenThereIsNothingToSay:
    def test_a_schedule_that_is_off_says_nothing(self):
        """Rather than "we are open all the time", which is a claim the
        operator never made."""
        assert hours_sentence(_schedule((0, "09:00", "17:00"), enabled=False)) == ""

    def test_no_schedule_says_nothing(self):
        assert hours_sentence(None) == ""
        assert hours_sentence({}) == ""
        assert hours_sentence("Monday to Friday") == ""

    def test_no_slots_says_nothing(self):
        assert hours_sentence({"enabled": True, "slots": []}) == ""

    def test_a_malformed_slot_is_skipped_not_spoken(self):
        schedule = {
            "enabled": True,
            "slots": [
                {"day_of_week": 9, "start_time": "09:00", "end_time": "17:00"},
                {"day_of_week": 0, "start_time": "09:00", "end_time": "17:00"},
            ],
        }
        assert hours_sentence(schedule) == "Monday, 9 am to 5 pm."
