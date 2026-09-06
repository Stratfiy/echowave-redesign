"""Collecting what the caller typed on the keypad.

The risk being tested is the one that makes the feature useless rather than
broken: an entry that arrives in pieces. Nobody presses one key and waits, so
handing the agent "9", then "8", then "7" would produce a reply after each one.
"""

import pytest

from api.services.pipecat.dtmf_rules import (
    DEFAULT_QUIET_SECONDS,
    MAX_DIGITS,
    TERMINATOR,
    DigitBuffer,
    describe,
)


def _press_all(buffer: DigitBuffer, keys: str) -> list[str]:
    return [entry for key in keys if (entry := buffer.press(key)) is not None]


class TestWhenAnEntryEnds:
    def test_typing_does_not_end_it(self):
        """Otherwise the agent answers after every single key."""
        assert _press_all(DigitBuffer(), "98765") == []

    def test_hash_ends_it(self):
        assert _press_all(DigitBuffer(), f"9876543210{TERMINATOR}") == ["9876543210"]

    def test_the_hash_is_not_part_of_it(self):
        """A caller entering "1234#" means 1234, not 1234 and a hash."""
        assert _press_all(DigitBuffer(), f"1234{TERMINATOR}") == ["1234"]

    def test_a_pause_ends_it(self):
        """The only signal available for a caller who does not know about #."""
        buffer = DigitBuffer()
        _press_all(buffer, "1234")
        assert buffer.flush() == "1234"

    def test_a_full_buffer_ends_it(self):
        """A stuck key must not be able to grow the context without limit."""
        buffer = DigitBuffer()
        assert _press_all(buffer, "1" * MAX_DIGITS) == ["1" * MAX_DIGITS]

    def test_it_keeps_going_after_one_entry(self):
        """A caller giving an account number and then a menu choice."""
        buffer = DigitBuffer()
        assert _press_all(buffer, f"1234{TERMINATOR}5{TERMINATOR}") == ["1234", "5"]


class TestNotProducingAnEmptyTurn:
    def test_a_lone_hash_produces_nothing(self):
        assert DigitBuffer().press(TERMINATOR) is None

    def test_a_pause_with_nothing_typed_produces_nothing(self):
        """The quiet timer fires again after a flush; it must stay silent."""
        buffer = DigitBuffer()
        _press_all(buffer, f"12{TERMINATOR}")
        assert buffer.flush() is None

    @pytest.mark.parametrize("key", ["", "12", None, 7, "  "])
    def test_a_key_that_is_not_a_key_is_ignored(self, key):
        buffer = DigitBuffer()
        assert buffer.press(key) is None
        assert buffer.pending == ""


class TestStarIsADigit:
    def test_star_is_collected_rather_than_treated_as_a_terminator(self):
        """`*` means something in plenty of menus and ends nothing."""
        assert _press_all(DigitBuffer(), f"*9{TERMINATOR}") == ["*9"]


class TestHowItReachesTheModel:
    def test_says_the_keys_were_pressed_rather_than_spoken(self):
        """An agent that knows these were typed can trust them.

        The caller read them off their own screen; a spoken ten-digit number is
        the least reliable thing on the call and is worth reading back.
        """
        message = describe("9876543210")
        assert "9876543210" in message
        assert "pressed" in message.lower()

    def test_reads_as_an_event_in_the_transcript(self):
        """Bare digits on a line would look like something we misheard."""
        assert describe("1234").startswith("[")


def test_the_quiet_window_is_a_trade_not_a_default():
    """Too short splits a phone number in two; too long feels asleep."""
    assert 1.0 <= DEFAULT_QUIET_SECONDS <= 2.5
