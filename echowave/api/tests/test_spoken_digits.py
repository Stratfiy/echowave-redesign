"""Digit handling, which is what most of these calls are actually about.

The risk in this module is not that it fails to convert a number — it is that
it converts a sentence. Roughly half of these tests exist to prove ordinary
prose survives, because "double room", "do it" and "no" are words before they
are digits.
"""

import pytest

from api.services.pipecat.spoken_digits import (
    normalise_spoken_digits,
    spell_out_long_numbers,
)


class TestRunsOfSpokenDigits:
    @pytest.mark.parametrize(
        "spoken,expected",
        [
            ("nine eight seven six", "9876"),
            ("nine eight oh two", "9802"),
            ("one two three four five", "12345"),
            ("zero one two three", "0123"),
        ],
    )
    def test_plain_digit_runs(self, spoken, expected):
        assert normalise_spoken_digits(spoken) == expected

    @pytest.mark.parametrize(
        "spoken,expected",
        [
            ("double five one two", "5512"),
            ("nine double eight seven", "9887"),
            ("triple seven one two", "77712"),
        ],
    )
    def test_double_and_triple(self, spoken, expected):
        """In India a repeated digit is normally read as double/triple.

        Deepgram returns the words; without this the number is unusable.
        """
        assert normalise_spoken_digits(spoken) == expected

    def test_hindi_digits_inside_english(self):
        """Code-mixed speech switches register mid-number.

        An English-language transcriber writes the Hindi digit words out
        phonetically, so the run arrives as a mix.
        """
        assert normalise_spoken_digits("nau nau eight saat") == "9987"

    def test_a_mixed_run_of_words_and_already_converted_digits(self):
        """`numerals` converts most of a run, leaving the odd word behind.

        This is the common real shape, not the all-words case.
        """
        assert normalise_spoken_digits("98 double 7 six 5") == "987765"

    def test_glue_between_groups_is_dropped(self):
        assert normalise_spoken_digits("nine eight and seven six") == "9876"


class TestItAlwaysReturns:
    """The one failure mode that is worse than a wrong transcript.

    This processor runs on every non-realtime call, inside a try/except that
    cannot catch a hang: the caller hears silence until the call times out.
    It happened on ordinary speech — "call me and let you know" — because
    glue words are run tokens so they can sit inside a number, and a run of
    nothing but glue rewound the cursor to exactly where it started.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "call me and let you know",
            "and",
            "and and and",
            "dash",
            "- - -",
            "nine one two and",
            "and nine one two",
            "call and dash and dash",
        ],
    )
    def test_glue_on_its_own_does_not_hang(self, text):
        assert normalise_spoken_digits(text) == text

    def test_a_number_followed_by_glue_still_converts(self):
        assert (
            normalise_spoken_digits(
                "nine eight seven six five four three two one zero and thanks"
            )
            == "9876543210 and thanks"
        )


class TestProseIsLeftAlone:
    @pytest.mark.parametrize(
        "text",
        [
            "I would like a double room",
            "do it now please",
            "no I do not want that",
            "one moment",
            "can you charge it",
            "yes that is fine",
            "okay",
            "",
        ],
    )
    def test_ordinary_sentences_survive(self, text):
        """The failure that would matter more than the bug being fixed.

        Every one of these contains a word this module knows as a digit.
        """
        assert normalise_spoken_digits(text) == text

    def test_a_short_run_is_not_a_number(self):
        """ "do it" is two known tokens and is not 2-something.

        The minimum run length is the whole defence against prose being
        rewritten, so it is asserted directly.
        """
        assert normalise_spoken_digits("do it") == "do it"

    def test_a_repeater_with_nothing_to_repeat_is_left_alone(self):
        """A caller cut off mid-number.

        Inventing the missing digit would put a wrong number in the CRM, which
        is worse than leaving the words for a human to read.
        """
        assert normalise_spoken_digits("nine eight seven double") == (
            "nine eight seven double"
        )

    def test_surrounding_words_are_preserved(self):
        assert (
            normalise_spoken_digits("my number is nine eight seven six five")
            == "my number is 98765"
        )

    def test_trailing_punctuation_stays_put(self):
        assert normalise_spoken_digits("call nine eight seven six.") == "call 9876."


class TestReadingNumbersBack:
    """Spacing digits for TTS, and the much worse bug that rule can cause.

    Reading "9876543210" as a cardinal is useless to somebody writing it down.
    Reading "1200 rupees" as "one two zero zero rupees" is worse. Length alone
    cannot tell an OTP from a price, so this is narrow on purpose.
    """

    def test_a_ten_digit_number_is_always_spaced(self):
        assert spell_out_long_numbers("call me on 9876543210") == (
            "call me on 9 8 7 6 5 4 3 2 1 0"
        )

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("your OTP is 4821", "your OTP is 4 8 2 1"),
            ("order 88213 is ready", "order 8 8 2 1 3 is ready"),
            ("booking 55129 confirmed", "booking 5 5 1 2 9 confirmed"),
        ],
    )
    def test_a_short_number_is_spaced_when_named_as_a_code(self, text, expected):
        assert spell_out_long_numbers(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "that will be 1200 rupees",
            "the total is Rs 4500",
            "it costs ₹2999",
            "about 5 lakh",
        ],
    )
    def test_money_is_never_spelled_out(self, text):
        """The bug this rule exists to avoid.

        An agent saying "one two zero zero rupees" instead of "twelve hundred
        rupees" is a new problem traded for an old one.
        """
        assert spell_out_long_numbers(text) == text

    def test_money_anywhere_in_the_sentence_vetoes_the_whole_line(self):
        """Deliberately conservative.

        A sentence carrying both a code and a price is rare; getting the price
        wrong is not worth spacing the code.
        """
        text = "your code is 4821 and the fee is 500 rupees"
        assert spell_out_long_numbers(text) == text

    @pytest.mark.parametrize(
        "text", ["we opened in 1998", "since 2015", "room 12", "at 3 pm", "for 999"]
    )
    def test_ordinary_numbers_are_left_alone(self, text):
        """A year is four digits and is not a code.

        Nothing here names a reference, so nothing is spaced.
        """
        assert spell_out_long_numbers(text) == text

    def test_empty_input(self):
        assert spell_out_long_numbers("") == ""


class TestWordsAreMatchedAsWords:
    """Substring matching turned the read-back off on ordinary sentences.

    "rs" is inside "first", "hours" and "yours", so any of those vetoed the
    whole thing as if the sentence were about money — and a ten-digit mobile
    number went back to being read as "nine billion, eight hundred and seventy
    six million", which is the complaint this module exists to answer.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "Your delivery arrives in two hours, call 9876543210",
            "I will call you first on 9876543210",
            "Yours is 9876543210",
        ],
    )
    def test_a_word_containing_rs_is_not_money(self, text):
        assert "9 8 7 6 5 4 3 2 1 0" in spell_out_long_numbers(text)

    @pytest.mark.parametrize(
        "text,digits",
        [("That is 4500 rupees", "4500"), ("Fifty rupees, ref 4500", "4500")],
    )
    def test_real_money_still_vetoes(self, text, digits):
        assert digits in spell_out_long_numbers(text)
        assert "4 5 0 0" not in spell_out_long_numbers(text)

    def test_the_rupee_symbol_still_vetoes(self):
        """Punctuation rather than a word, so it stays a substring check."""
        assert spell_out_long_numbers("Your total is \u20b94500") == (
            "Your total is \u20b94500"
        )

    @pytest.mark.parametrize(
        "text", ["We are shipping 4821 units", "I prefer 4821 units"]
    )
    def test_a_word_containing_pin_or_ref_is_not_a_code(self, text):
        assert "4 8 2 1" not in spell_out_long_numbers(text)

    @pytest.mark.parametrize(
        "text", ["Your PIN is 4821", "Your reference is 4821", "Order 4821"]
    )
    def test_a_real_code_word_still_spaces_it(self, text):
        assert "4 8 2 1" in spell_out_long_numbers(text)
