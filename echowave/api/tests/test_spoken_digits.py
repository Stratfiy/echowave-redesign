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
    def test_a_phone_number_is_spaced_for_tts(self):
        """Otherwise TTS reads it as a cardinal.

        "9876543210" spoken as "nine billion, eight hundred seventy six
        million…" is useless to somebody writing it down, and is the most
        common complaint about an agent reading a number back.
        """
        assert spell_out_long_numbers("call 9876543210 now") == (
            "call 9 8 7 6 5 4 3 2 1 0 now"
        )

    def test_an_otp_is_spaced(self):
        assert spell_out_long_numbers("your code is 4821") == "your code is 4 8 2 1"

    @pytest.mark.parametrize("text", ["2 people", "at 3 pm", "room 12", "for 999"])
    def test_short_numbers_are_quantities_and_stay_whole(self, text):
        """Spacing these out would be worse than the problem being fixed."""
        assert spell_out_long_numbers(text) == text

    def test_empty_input(self):
        assert spell_out_long_numbers("") == ""
