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
    def test_an_all_day_event_does_not_block_the_whole_day(self):
        """A holiday, a birthday or "Dr Anitha on leave" overlaps every window
        from open to close. One of them must not refuse every booking that day."""
        assert (
            _occupies_the_chair(
                {
                    "status": "confirmed",
                    "summary": "Ganesh Chaturthi",
                    "start": {"date": "2026-09-14"},
                    "end": {"date": "2026-09-15"},
                }
            )
            is False
        )

    def test_a_multi_day_event_does_not_block_either(self):
        """Same marker, several days of it -- a conference, a shutdown week."""
        assert (
            _occupies_the_chair(
                {
                    "status": "confirmed",
                    "summary": "Clinic closed for Diwali",
                    "start": {"date": "2026-11-08"},
                    "end": {"date": "2026-11-12"},
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
