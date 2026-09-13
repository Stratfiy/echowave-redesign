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

from api.services.workflow.mentions import handle_for, resolve

ROSTER = [
    {"id": 1, "name": "Ops bot"},
    {"id": 2, "name": "Sales bot"},
    {"id": 3, "name": "Sales bot India"},
    {"id": 4, "name": "Narayani Dental front desk"},
    {"id": 5, "name": "Meera — Decibyl Sales Assistant"},
]


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
