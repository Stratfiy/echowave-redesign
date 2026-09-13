"""Which bot a person meant, and when we should admit we do not know.

Bots are addressed by a handle rather than a display name, and that choice is
what makes this answerable. Display names are sentences -- "Narayani Dental
front desk" -- so matching one inside prose means deciding where the name ends,
and the answer is a guess: "@Sales bot India" is either that bot or "Sales bot"
followed by the word India. A handle ends at a space, so there is nothing to
decide.

The expensive failure here was never silence. It is the wrong bot answering
confidently, which reads to a customer as the product ignoring what they typed.
"""

from api.services.workflow.mentions import (
    MAX_HANDLE,
    available_handle,
    handle_for,
    resolve,
)

#: Deliberately carries no ``handle``, so every test written against it also
#: exercises the fallback for a bot whose handle was never assigned. The stored
#: column is covered by ``TestTheStoredHandleIsTheAddress`` below.
ROSTER = [
    {"id": 1, "name": "Ops bot"},
    {"id": 2, "name": "Sales bot"},
    {"id": 3, "name": "Sales bot India"},
    {"id": 4, "name": "Narayani Dental front desk"},
    {"id": 5, "name": "Meera — Decibyl Sales Assistant"},
]


class TestTheStoredHandleIsTheAddress:
    """The column decides, not the name.

    This is the whole reason the handle stopped being derived. A business
    should be able to call its bot "Narayani Dental front desk" -- which is
    what the people who work there call it -- and still have somebody reach it
    by typing five characters.
    """

    def test_the_column_is_what_resolves(self):
        roster = [{"id": 7, "name": "Narayani Dental front desk", "handle": "front"}]
        assert resolve("@front are we open?", roster).mentioned == [(7, "front")]

    def test_the_display_name_no_longer_addresses_anything(self):
        roster = [{"id": 7, "name": "Narayani Dental front desk", "handle": "front"}]
        result = resolve("@narayani-dental-front-desk hello", roster)
        assert result.mentioned == []
        assert result.unknown == ["narayani-dental-front-desk"]

    def test_a_rename_does_not_move_the_address(self):
        """The failure storing it exists to prevent: a derived handle changes
        when somebody edits the name, and every message that used the old one
        is suddenly addressed to nobody."""
        before = [{"id": 7, "name": "Front desk", "handle": "front-desk"}]
        after = [{"id": 7, "name": "Reception (Hosur)", "handle": "front-desk"}]
        assert resolve("@front-desk hello", before).mentioned == [(7, "front-desk")]
        assert resolve("@front-desk hello", after).mentioned == [(7, "front-desk")]

    def test_case_in_the_stored_handle_does_not_matter(self):
        roster = [{"id": 7, "name": "Ops", "handle": "Front-Desk"}]
        assert resolve("@front-desk hi", roster).mentioned == [(7, "front-desk")]

    def test_a_bot_with_no_handle_falls_back_to_its_name(self):
        """Not tidiness: a bot created on a path that has not been taught to
        assign a handle would otherwise be addressable by nothing at all, and
        nobody would find out. The fallback makes that case visible instead."""
        roster = [{"id": 8, "name": "Ops bot", "handle": None}]
        assert resolve("@ops-bot hi", roster).mentioned == [(8, "ops-bot")]

    def test_a_stored_handle_and_a_derived_one_can_collide_and_are_refused(self):
        roster = [
            {"id": 1, "name": "Something else", "handle": "ops-bot"},
            {"id": 2, "name": "Ops bot"},
        ]
        result = resolve("@ops-bot hi", roster)
        assert result.mentioned == []
        assert result.ambiguous == ["ops-bot"]


class TestPickingAFreeHandle:
    def test_the_obvious_handle_when_it_is_free(self):
        assert available_handle("Ops bot", []) == "ops-bot"

    def test_a_taken_handle_is_numbered_rather_than_refused(self):
        """The standalone script refused collisions, and that was right when
        the handle *was* the display name -- picking a winner would have
        quietly renamed somebody's bot. Nothing is lost now: both keep their
        names, and the second gets an address instead of none."""
        assert available_handle("Customer Support", ["customer-support"]) == (
            "customer-support-2"
        )

    def test_it_keeps_counting(self):
        taken = ["customer-support", "customer-support-2"]
        assert available_handle("Customer Support", taken) == "customer-support-3"

    def test_comparison_ignores_case(self):
        assert available_handle("Ops bot", ["OPS-BOT"]) == "ops-bot-2"

    def test_a_name_with_nothing_sluggable_gets_no_handle(self):
        """Empty rather than "workflow-41": an address nobody could guess is
        no better than no address, and the caller stores NULL."""
        assert available_handle("!!!", []) == ""
        assert available_handle("", []) == ""

    def test_a_suffix_never_pushes_it_past_the_column(self):
        long_name = "a" * 200
        first = available_handle(long_name, [])
        assert len(first) <= MAX_HANDLE
        second = available_handle(long_name, [first])
        assert len(second) <= MAX_HANDLE
        assert second != first
        assert second.endswith("-2")


class TestTheHandleADisplayNameEarns:
    def test_spaces_become_hyphens_rather_than_vanishing(self):
        """ "frontdesk" is a handle somebody has to memorise; "front-desk" is
        one they can work out from the name on the screen."""
        assert handle_for("Narayani Dental front desk") == "narayani-dental-front-desk"

    def test_punctuation_is_dropped_not_kept(self):
        assert (
            handle_for("Meera — Decibyl Sales Assistant")
            == "meera-decibyl-sales-assistant"
        )

    def test_accents_fold_because_people_type_on_phones(self):
        """The display name keeps its accent. Only the address loses it."""
        assert handle_for("Meerá") == "meera"

    def test_it_does_not_start_or_end_on_a_separator(self):
        assert handle_for("  — Ops bot —  ") == "ops-bot"

    def test_an_empty_name_earns_no_handle(self):
        assert handle_for("") == ""


class TestItFindsWhoWasAddressed:
    def test_a_plain_mention(self):
        assert resolve("@ops-bot chase the suppliers", ROSTER).mentioned == [
            (1, "ops-bot")
        ]

    def test_a_long_name_is_one_handle(self):
        found = resolve("@narayani-dental-front-desk are we open?", ROSTER).mentioned
        assert found == [(4, "narayani-dental-front-desk")]

    def test_the_ambiguity_that_used_to_need_a_guess_is_gone(self):
        """ "@sales-bot-india" and "@sales-bot" are different handles. Nothing
        has to decide where the name ended."""
        assert resolve("@sales-bot-india send it", ROSTER).mentioned == [
            (3, "sales-bot-india")
        ]
        assert resolve("@sales-bot send it", ROSTER).mentioned == [(2, "sales-bot")]

    def test_case_does_not_matter(self):
        assert resolve("@OPS-BOT hello", ROSTER).mentioned == [(1, "ops-bot")]

    def test_two_bots_in_one_message_keep_their_order(self):
        found = resolve("@ops-bot and @sales-bot both please", ROSTER).mentioned
        assert [m.workflow_id for m in found] == [1, 2]

    def test_the_same_bot_twice_is_one_addressee(self):
        found = resolve("@ops-bot and again @ops-bot", ROSTER).mentioned
        assert [m.workflow_id for m in found] == [1]

    def test_a_mention_ends_at_punctuation(self):
        assert resolve("@ops-bot, please chase this", ROSTER).mentioned == [
            (1, "ops-bot")
        ]


class TestItRefusesToGuess:
    def test_a_handle_that_matches_nothing_is_reported_not_approximated(self):
        result = resolve("@op-bot chase the suppliers", ROSTER)
        assert result.mentioned == []
        assert result.unknown == ["op-bot"]

    def test_two_bots_sharing_a_handle_resolve_to_neither(self):
        """Two bots called "Sales bot" in one channel is a naming problem.
        Answering with whichever came back first would hide it behind a bot
        that sometimes replies and sometimes does not."""
        roster = [{"id": 8, "name": "Sales bot"}, {"id": 9, "name": "sales  bot"}]
        result = resolve("@sales-bot hello", roster)
        assert result.mentioned == []
        assert result.ambiguous == ["sales-bot"]

    def test_an_email_address_is_neither_a_mention_nor_an_unknown_one(self):
        """The second half is the one that matters. Without a leading
        boundary, typing a customer's email would have the channel answer
        "there is nobody here called clinic.example"."""
        result = resolve("mail it to ramesh@clinic.example today", ROSTER)
        assert result.mentioned == []
        assert result.unknown == []

    def test_plain_text_addresses_nobody(self):
        assert resolve("chase the overdue supplier updates", ROSTER) == ([], [], [])

    def test_a_bot_outside_the_roster_is_unknown(self):
        """The roster is the tenancy answer: which bots this person may
        address. A handle not on it must not resolve even if the bot exists."""
        result = resolve("@support-bot help", ROSTER)
        assert result.mentioned == []
        assert result.unknown == ["support-bot"]

    def test_a_bare_at_sign_is_not_a_mention(self):
        assert resolve("meet @ 4pm", ROSTER).mentioned == []

    def test_empty_text_is_handled(self):
        assert resolve("", ROSTER) == ([], [], [])
