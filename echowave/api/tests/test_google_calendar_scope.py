"""The scopes we ask a clinic to consent to.

A scope string is the kind of value that regresses silently: nothing fails
when one is missing, a screen just stops being able to say something. This
file exists because exactly that happened -- the email scope was absent, so
``connected_email`` was null for every connection ever made and the screen
rendered "Connected as ." with a blank.
"""

from api.services.integrations.google_calendar.oauth import SCOPE

CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
USERINFO_EMAIL = "https://www.googleapis.com/auth/userinfo.email"


class TestWhatWeAskFor:
    def test_we_can_write_the_booking(self):
        assert CALENDAR_EVENTS in SCOPE

    def test_we_can_say_which_account_was_connected(self):
        """Without this the userinfo endpoint answers with no email, and the
        screen cannot name the account it is connected to."""
        assert USERINFO_EMAIL in SCOPE

    def test_we_do_not_ask_to_read_somebody_s_calendars(self):
        """We write events and never list calendars. A consent screen that
        asks for less is one more clinic that says yes, and a scope we do not
        use is one we would have to justify in a Google review."""
        assert "auth/calendar.readonly" not in SCOPE
        # The broad read-write scope, as a whole word: `calendar.events` is a
        # substring match away from a false pass here.
        assert "auth/calendar " not in f"{SCOPE} "

    def test_the_scopes_are_space_separated_as_google_requires(self):
        parts = SCOPE.split(" ")
        assert len(parts) == len(set(parts)), "a duplicated scope is rejected"
        assert all(part.startswith("https://") for part in parts)
