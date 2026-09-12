"""Which events on a calendar actually mean the chair is taken.

``events.list`` answers "what overlaps this window", and the booking tool
needs "is this slot busy". The gap between those two questions is the most
expensive bug this integration can have: a caller asks for a free slot, is
told no, and rings somebody else. Nothing in the transcript says why.

The tests below pin both directions, because loosening the rule too far is
the failure that puts two patients in one chair.
"""

from api.services.integrations.google_calendar.client import _occupies_the_chair


class TestWhatDoesNotMeanBusy:
    def test_a_holiday_published_as_free_does_not_block(self):
        """How a public holiday actually reaches a calendar: on Google's own
        holiday calendar, published Free. It does not close the clinic -- plenty
        of dental practices work Ganesh Chaturthi -- so the operator's own
        Busy/Free marking decides, not our guess about the date."""
        assert (
            _occupies_the_chair(
                {
                    "status": "confirmed",
                    "summary": "Ganesh Chaturthi",
                    "start": {"date": "2026-09-14"},
                    "end": {"date": "2026-09-15"},
                    "transparency": "transparent",
                }
            )
            is False
        )

    def test_an_event_the_owner_marked_free_does_not_block(self):
        """Google's own word for it. A reminder, a held placeholder, a travel
        note -- the owner has said this does not occupy them."""
        assert (
            _occupies_the_chair(
                {
                    "status": "confirmed",
                    "summary": "Order new gloves",
                    "transparency": "transparent",
                    "start": {"dateTime": "2026-09-14T12:00:00+05:30"},
                }
            )
            is False
        )

    def test_the_free_marker_is_read_case_insensitively(self):
        assert (
            _occupies_the_chair(
                {
                    "transparency": "TRANSPARENT",
                    "start": {"dateTime": "2026-09-14T12:00:00+05:30"},
                }
            )
            is False
        )

    def test_a_cancelled_event_is_not_an_event(self):
        assert (
            _occupies_the_chair(
                {
                    "status": "cancelled",
                    "summary": "Mr Kumar (cancelled)",
                    "start": {"dateTime": "2026-09-14T12:00:00+05:30"},
                }
            )
            is False
        )


class TestWhatDoesMeanBusy:
    """The half that stops the fix becoming a double-booking."""

    def test_a_real_timed_appointment_blocks(self):
        assert (
            _occupies_the_chair(
                {
                    "status": "confirmed",
                    "summary": "Mrs Lakshmi -- root canal",
                    "start": {"dateTime": "2026-09-14T12:00:00+05:30"},
                    "end": {"dateTime": "2026-09-14T13:00:00+05:30"},
                }
            )
            is True
        )

    def test_the_default_busy_marker_blocks(self):
        """``transparency: "opaque"`` is Google's word for busy, and it is the
        default. Reading it as anything but a conflict would free every slot."""
        assert (
            _occupies_the_chair(
                {
                    "transparency": "opaque",
                    "start": {"dateTime": "2026-09-14T12:00:00+05:30"},
                }
            )
            is True
        )

    def test_an_unrecognised_shape_counts_as_busy(self):
        """The rule only removes a conflict on a marker Google sets explicitly.
        An event we cannot read is a real one until proven otherwise -- the
        cost of over-blocking is a second look at the calendar, the cost of
        under-blocking is two people in one chair."""
        assert _occupies_the_chair({"summary": "something"}) is True
        assert _occupies_the_chair({"start": None, "summary": "x"}) is True
        assert _occupies_the_chair({"start": "2026-09-14", "summary": "x"}) is True

    def test_a_doctor_on_leave_closes_the_day(self):
        """The case an earlier version of this file got backwards. An all-day
        entry on the calendar a clinic books against is the clinic closing
        itself, and booking through it sends a patient to a locked door."""
        assert (
            _occupies_the_chair(
                {
                    "status": "confirmed",
                    "summary": "Dr Anitha on leave",
                    "start": {"date": "2026-09-14"},
                    "end": {"date": "2026-09-15"},
                }
            )
            is True
        )

    def test_a_multi_day_closure_blocks_every_day_of_it(self):
        assert (
            _occupies_the_chair(
                {
                    "status": "confirmed",
                    "summary": "Clinic closed for Diwali",
                    "start": {"date": "2026-11-08"},
                    "end": {"date": "2026-11-12"},
                }
            )
            is True
        )

    def test_an_all_day_entry_marked_busy_blocks(self):
        """Explicitly Busy has to win. This returned False before, so an
        operator who went out of their way to say "we are shut" was ignored."""
        assert (
            _occupies_the_chair(
                {
                    "status": "confirmed",
                    "summary": "Equipment service",
                    "start": {"date": "2026-09-14"},
                    "transparency": "opaque",
                }
            )
            is True
        )

    def test_an_accepted_invitation_still_blocks(self):
        assert (
            _occupies_the_chair(
                {
                    "status": "confirmed",
                    "summary": "Supplier visit",
                    "transparency": None,
                    "start": {"dateTime": "2026-09-14T12:00:00+05:30"},
                }
            )
            is True
        )


class TestTheRefusalTellsTheAgentWhatToDoNext:
    """A closure and a clash are different problems, and the sentence the model
    reads is the only thing that distinguishes them.

    "Try a different time" on a day the clinic is shut walks the agent through
    every slot from open to close, refusing each one, while the caller waits.
    """

    def _conflict(self, event):
        """The branch `execute_google_calendar_tool` takes on a conflict,
        exercised through the same fields it reads."""
        start = event.get("start") or {}
        return bool(start.get("date") and not start.get("dateTime"))

    def test_a_closure_is_recognised_as_one(self):
        assert self._conflict({"start": {"date": "2026-09-14"}}) is True

    def test_a_booked_slot_is_not_a_closure(self):
        assert (
            self._conflict({"start": {"dateTime": "2026-09-14T11:00:00+05:30"}})
            is False
        )
