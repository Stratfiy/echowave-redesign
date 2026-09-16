"""An identifier is not something to guess at.

Asked to read a Facebook page's conversations, Decibyl passed the id it
had from ``GET_CURRENT_USER``. Facebook answered that the page was not in
the managed pages and helpfully listed five real ones. Asked in the same
turn for a LinkedIn organisation's network size, it passed the *Facebook*
numeric id as ``organization_id`` and LinkedIn returned a 500.

Neither was a tool fault. Both were the same move: needing an identifier
it did not have, it reached for one it had seen.

In a diagnostic that is a wasted call. On the write path it is somebody
else's record -- and a card carrying a wrong id reads exactly like a card
carrying a right one, so the person confirming it has nothing to notice.
The rules already say never to invent a figure, a name or a document;
an id belongs on that list, and it is the item where being wrong is
hardest to see.
"""

from __future__ import annotations

from api.services.workflow.decibyl import SYSTEM


class TestTheRuleIsThere:
    def test_identifiers_are_named_as_something_not_to_infer(self):
        assert "identifier" in SYSTEM.lower()

    def test_it_says_where_a_real_one_comes_from(self):
        """Not "be careful" -- the three places a true one can come from,
        so the rule is a procedure rather than a warning."""
        lowered = SYSTEM.lower()
        for source in ("the context", "this thread", "from a read"):
            assert source in lowered, source

    def test_it_forbids_carrying_one_between_apps(self):
        """The LinkedIn 500, exactly: a Facebook id in a LinkedIn field."""
        assert "from another app" in SYSTEM.lower()

    def test_it_says_what_to_do_instead(self):
        """A rule that only forbids leaves the model with the same problem
        and no move. Fetching it, or asking, are the moves."""
        lowered = SYSTEM.lower()
        assert "fetch" in lowered and "say which" in lowered
