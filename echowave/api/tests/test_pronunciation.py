"""Respelling names, and the ways a naive find-and-replace goes wrong.

The lexicon is typed by a business owner, not a linguist, so its contents are
arbitrary text that ends up inside a regex and inside a replacement. Both are
places where "just use re.sub" silently does something else.
"""

import pytest

from api.services.pipecat.pronunciation import (
    MAX_ENTRIES,
    apply_lexicon,
    compile_lexicon,
    parse_lexicon,
)


def lexicon(*pairs):
    return compile_lexicon(parse_lexicon([{"find": f, "say": s} for f, s in pairs]))


class TestRespelling:
    def test_replaces_a_name(self):
        compiled = lexicon(("Chinnaswamy", "Chinna-swaamy"))
        assert apply_lexicon("Doctor Chinnaswamy is free at four", compiled) == (
            "Doctor Chinna-swaamy is free at four"
        )

    def test_is_case_insensitive(self):
        """A caller's name appears capitalised mid-sentence and not at all in
        a lowercase model response; the customer should not have to enter both.
        """
        compiled = lexicon(("hosur", "Hoh-soor"))
        assert apply_lexicon("our Hosur branch", compiled) == "our Hoh-soor branch"

    def test_does_not_match_inside_a_longer_word(self):
        """The failure that makes a lexicon dangerous.

        An entry for "Rao" must not turn "Raoul" into something else.
        """
        compiled = lexicon(("Rao", "Raao"))
        assert apply_lexicon("Raoul called", compiled) == "Raoul called"
        assert apply_lexicon("Rao called", compiled) == "Raao called"

    def test_longest_entry_wins(self):
        """Both entries can match the same text.

        If the short one is applied first the long one can never fire, and the
        customer's more specific instruction is the one silently ignored.
        """
        compiled = lexicon(("Rao", "Raao"), ("Dr Rao", "Doctor Raao"))
        assert apply_lexicon("Dr Rao is in", compiled) == "Doctor Raao is in"


class TestHostileAndClumsyInput:
    def test_a_term_with_regex_characters_is_matched_literally(self):
        """ "Dr. Rao" contains a full stop.

        Unescaped, that is a wildcard: it would match "Dr!Rao" and, worse, a
        customer whose term happens to be valid regex would quietly change
        other sentences.
        """
        compiled = lexicon(("Dr. Rao", "Doctor Raao"))
        assert apply_lexicon("Dr. Rao is in", compiled) == "Doctor Raao is in"
        assert apply_lexicon("DrXRao is in", compiled) == "DrXRao is in"

    def test_a_replacement_containing_a_backslash_is_spoken_not_interpreted(self):
        r"""``\1`` in the replacement is a group reference to ``re.sub``.

        Typed by a customer it is just text, and expanding it would raise or
        silently blank the word.
        """
        compiled = lexicon(("Rao", r"Ra\1o"))
        assert apply_lexicon("Rao called", compiled) == r"Ra\1o called"

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "not-a-list",
            [],
            [None],
            ["string-entry"],
            [{"find": "", "say": "x"}],
            [{"find": "x", "say": ""}],
            [{"find": 7, "say": "x"}],
            [{"nothing": "useful"}],
        ],
    )
    def test_malformed_configuration_is_skipped_not_raised(self, raw):
        """This is read while a call is being set up.

        A missing pronunciation is a blemish; an exception here is a call that
        never connects.
        """
        assert parse_lexicon(raw) == []

    def test_alternate_field_names_are_accepted(self):
        """Three shapes have been used in drafts of this feature."""
        assert parse_lexicon([{"from": "a", "to": "b"}]) == [("a", "b")]
        assert parse_lexicon([{"word": "a", "pronounce": "b"}]) == [("a", "b")]

    def test_the_lexicon_is_capped(self):
        """Every entry costs a regex pass on every sentence the agent speaks."""
        raw = [{"find": f"term{i}", "say": "x"} for i in range(MAX_ENTRIES + 50)]
        assert len(parse_lexicon(raw)) == MAX_ENTRIES

    def test_empty_text_and_empty_lexicon(self):
        assert apply_lexicon("", lexicon(("a", "b"))) == ""
        assert apply_lexicon("unchanged", []) == "unchanged"
