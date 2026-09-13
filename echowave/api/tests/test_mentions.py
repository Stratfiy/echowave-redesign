"""Which bot a person meant, and when we should admit we do not know.

A channel holds several bots. "@Ops bot chase the suppliers" has to resolve to
exactly one workflow, and the expensive failure is not silence -- it is the
wrong bot answering confidently, which reads to the customer as the product
ignoring what they typed.
"""

from api.services.workflow.mentions import resolve

ROSTER = [
    {"id": 1, "name": "Ops bot"},
    {"id": 2, "name": "Sales bot"},
    {"id": 3, "name": "Sales bot India"},
    {"id": 4, "name": "Narayani Dental front desk"},
    {"id": 5, "name": "Meera — Decibyl Sales Assistant"},
]


class TestItFindsWhoWasAddressed:
    def test_a_plain_mention(self):
        assert resolve("@Ops bot chase the suppliers", ROSTER).mentioned == [
            (1, "ops bot")
        ]

    def test_a_name_with_spaces_in_it(self):
        """Bot names are sentences, not handles. Stopping at the first space
        would resolve "@Narayani Dental front desk" to nothing."""
        found = resolve("@Narayani Dental front desk are we open?", ROSTER).mentioned
        assert found == [(4, "narayani dental front desk")]

    def test_the_longer_name_wins(self):
        """ "@Sales bot India" must not resolve to "Sales bot" with the word
        India left over as prose -- that silently addresses a different
        teammate and reads as the product mishearing you."""
        assert resolve("@Sales bot India send the quote", ROSTER).mentioned == [
            (3, "sales bot india")
        ]

    def test_punctuation_in_a_name_survives(self):
        found = resolve("@Meera — Decibyl Sales Assistant hi", ROSTER).mentioned
        assert found == [(5, "meera — decibyl sales assistant")]

    def test_two_bots_in_one_message_keep_their_order(self):
        found = resolve("@Ops bot and @Sales bot both please", ROSTER).mentioned
        assert [m.workflow_id for m in found] == [1, 2]

    def test_the_same_bot_twice_is_one_addressee(self):
        found = resolve("@Ops bot and again @Ops bot", ROSTER).mentioned
        assert [m.workflow_id for m in found] == [1]

    def test_case_and_spacing_do_not_matter(self):
        assert resolve("@ops   BOT hello", ROSTER).mentioned == [(1, "ops bot")]


class TestItRefusesToGuess:
    def test_a_name_that_matches_nothing_is_reported_not_approximated(self):
        """Picking the closest name is a bot answering a question addressed to
        a different bot. Silence is recoverable; a confident wrong answer to a
        customer is not."""
        result = resolve("@Op bot chase the suppliers", ROSTER)
        assert result.mentioned == []
        assert result.unknown == ["op"]

    def test_an_email_address_is_not_a_mention_or_an_unknown_one(self):
        """The second half is the one that matters. An @ inside a word yields
        "clinic.example", which matches no bot -- so without a leading
        boundary, typing a customer's email address would have the channel
        answer "I do not know who clinic.example is"."""
        result = resolve("mail it to ramesh@clinic.example today", ROSTER)
        assert result.mentioned == []
        assert result.unknown == []

    def test_plain_text_addresses_nobody(self):
        result = resolve("chase the overdue supplier updates", ROSTER)
        assert result == ([], [])

    def test_a_bot_outside_the_roster_is_unknown(self):
        """The roster is the tenancy answer: which bots this person may
        address. A name not on it must not resolve even if it exists."""
        result = resolve("@Support bot help", ROSTER)
        assert result.mentioned == []
        assert result.unknown == ["support"]

    def test_a_bare_at_sign_is_not_a_mention(self):
        assert resolve("meet @ 4pm", ROSTER).mentioned == []

    def test_empty_text_is_handled(self):
        assert resolve("", ROSTER) == ([], [])
