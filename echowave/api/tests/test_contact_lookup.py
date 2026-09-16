"""Contacts are part of what the account knows, not only what a phone knows.

The defect: a contact was reachable from exactly one place, a ringing
phone. Ask Decibyl "what do we know about Ravi" and it answered from
memory, documents and the timeline, never once looking at the table with
the answer in it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import contact_lookup


def _contact(name=None, phone_raw="+91 98765 43210", attributes=None):
    return SimpleNamespace(
        name=name,
        phone_raw=phone_raw,
        phone_normalized="919876543210",
        attributes=attributes or {},
    )


class TestWhatIsWorthMatching:
    def test_a_name_is_taken_from_the_question(self):
        assert contact_lookup.terms("what do we know about Ravi") == ["Ravi"]

    def test_a_question_about_the_numbers_yields_no_name_worth_having(self):
        """Noise is allowed through; a missing name is not.

        "many" and "take" match no contact, so letting them through costs
        one more clause in one bounded query. Excluding a real name costs
        the answer and says nothing -- which is why the stopword list errs
        towards letting words through rather than being complete.
        """
        found = contact_lookup.terms("how many calls did we take today")
        assert "calls" not in found
        assert "today" not in found

    def test_an_empty_question_is_not_an_error(self):
        assert contact_lookup.terms("") == []
        assert contact_lookup.terms(None) == []

    def test_a_number_is_matched_on_its_last_ten_digits(self):
        """The account holds 9876543210; the person typed +91 98765 43210."""
        assert contact_lookup.terms("call +91 98765 43210") == ["9876543210"]

    def test_a_number_comes_before_the_words(self):
        found = contact_lookup.terms("did 9876543210 belong to Ravi")
        assert found[0] == "9876543210"

    def test_a_small_number_is_not_a_phone_number(self):
        """`did 12 people call` is a quantity, not somebody to look up."""
        assert "12" not in contact_lookup.terms("did 12 people call")

    def test_a_two_letter_word_is_not_a_name(self):
        assert contact_lookup.terms("is he ok") == []

    def test_the_number_of_terms_is_bounded(self):
        many = "Ravi Kumar Priya Suresh Lakshmi Venkat Anand"
        assert len(contact_lookup.terms(many)) == contact_lookup.MAX_TERMS

    def test_a_repeated_word_is_taken_once(self):
        found = contact_lookup.terms("Ravi, and Ravi again")
        assert found.count("Ravi") == 1

    def test_a_number_written_with_separators_is_matched_whole(self):
        """Splitting on the spaces first finds 98765 and 43210 and looks up
        neither of them. Found by measuring, not by reading the code."""
        for written in ("+91 98765 43210", "98765-43210", "98765 43210"):
            assert contact_lookup.terms(written)[0] == "9876543210", written

    def test_a_landline_with_brackets_is_matched(self):
        assert contact_lookup.terms("(044) 2233 4455")[0] == "4422334455"

    def test_a_date_is_not_a_phone_number(self):
        assert contact_lookup.terms("on 12 Oct 2026") == ["Oct"]


class TestReadingThem:
    def test_a_contact_reads_as_a_name_and_a_number(self):
        assert contact_lookup.block([_contact("Ravi")]) == "- Ravi (+91 98765 43210)"

    def test_the_number_as_uploaded_is_shown_not_our_canonical_form(self):
        """An operator recognises the spelling they typed."""
        assert "+91 98765 43210" in contact_lookup.block([_contact("Ravi")])

    def test_whatever_the_account_chose_to_keep_is_shown(self):
        """`attributes` is open by design, so it is rendered not enumerated."""
        line = contact_lookup.block(
            [_contact("Ravi", attributes={"policy": "PX-9", "due": "12 Oct"})]
        )
        assert "policy: PX-9" in line
        assert "due: 12 Oct" in line

    def test_a_contact_with_no_name_is_never_a_blank_line(self):
        assert contact_lookup.block([_contact(None)]).startswith("- Unnamed")

    def test_a_paragraph_in_a_column_cannot_spend_the_prompt(self):
        line = contact_lookup.block([_contact("Ravi", attributes={"note": "x" * 900})])
        assert len(line) < 250

    def test_more_than_the_cap_is_not_rendered(self):
        lines = contact_lookup.block([_contact(f"P{n}") for n in range(20)])
        assert len(lines.splitlines()) == contact_lookup.MAX_CONTACTS


class TestReadingIsNeverFatal:
    @pytest.mark.asyncio
    async def test_a_question_with_nothing_to_match_never_queries(self):
        """A bounded query is cheap, but a query for nothing is still waste."""
        with patch.object(
            contact_lookup.db_client, "search_contacts_for_organization", AsyncMock()
        ) as search:
            assert await contact_lookup.matching(1, "is he ok") == []
        search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_contact_book_that_cannot_be_read_is_a_prompt_without_one(self):
        with patch.object(
            contact_lookup.db_client,
            "search_contacts_for_organization",
            AsyncMock(side_effect=RuntimeError("down")),
        ):
            assert await contact_lookup.matching(1, "who is Ravi") == []

    @pytest.mark.asyncio
    async def test_the_terms_reach_the_query(self):
        with patch.object(
            contact_lookup.db_client,
            "search_contacts_for_organization",
            AsyncMock(return_value=[_contact("Ravi")]),
        ) as search:
            found = await contact_lookup.matching(1, "who is Ravi")
        assert len(found) == 1
        assert search.await_args.args[1] == ["Ravi"]
        assert search.await_args.kwargs["limit"] == contact_lookup.MAX_CONTACTS


class TestContactsAreInTheKnowledgeBase:
    """The contact book and the uploaded files are one idea to the person
    asking, so they are one heading to the model reading."""

    def test_a_matched_contact_is_offered_as_knowledge(self):
        from api.services.workflow import decibyl

        block = decibyl.knowledge_block({"chunks": []}, [_contact("Ravi")])
        assert "Ravi" in block

    def test_a_contact_is_read_before_a_passage(self):
        """A question naming somebody is usually about them; a passage that
        merely reads like them is the weaker answer."""
        from api.services.workflow import decibyl

        block = decibyl.knowledge_block(
            {"chunks": [{"text": "our refund policy", "document_name": "policy.pdf"}]},
            [_contact("Ravi")],
        )
        assert block.index("Ravi") < block.index("refund policy")

    def test_an_account_with_no_embeddings_still_learns_about_its_customers(self):
        """The document half needs embeddings configured. The contact half
        does not, so the free tier is not answered with silence."""
        from api.services.workflow import decibyl

        block = decibyl.knowledge_block(
            {"status": "unavailable", "chunks": []}, [_contact("Ravi")]
        )
        assert "Ravi" in block
        assert "Nothing in the knowledge base" not in block

    def test_nothing_matching_says_so_once(self):
        from api.services.workflow import decibyl

        assert (
            decibyl.knowledge_block({"chunks": []}, [])
            == "Nothing in the knowledge base matches that."
        )
