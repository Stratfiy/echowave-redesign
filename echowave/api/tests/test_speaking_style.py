"""Speaking the way callers actually do.

The instruction is prose, so what can be tested is what it must not lose: it is
appended to an operator's own prompt, it must not fire unless asked for, and
the three failures it exists to prevent must each be addressed in it.
"""

import pytest

from api.services.workflow.speaking_style import (
    CODE_MIXED_INSTRUCTIONS,
    CONFIG_KEY,
    wants_code_mixed_speech,
)


class TestOnlyWhenAskedFor:
    def test_on_when_turned_on(self):
        assert wants_code_mixed_speech({CONFIG_KEY: True}) is True

    @pytest.mark.parametrize("configs", [{}, {CONFIG_KEY: False}, {CONFIG_KEY: None}])
    def test_off_by_default(self, configs):
        """It is appended to the operator's prompt.

        Somebody who wrote "reply only in formal Hindi" meant it, and this
        would quietly argue with them on every turn.
        """
        assert wants_code_mixed_speech(configs) is False

    @pytest.mark.parametrize("configs", [None, "nonsense", 7, []])
    def test_unreadable_configuration_is_off(self, configs):
        assert wants_code_mixed_speech(configs) is False


class TestWhatTheInstructionHasToSay:
    def test_says_to_mirror_the_caller(self):
        """The register is the caller's, not one we picked for them."""
        assert "Mirror the caller" in CODE_MIXED_INSTRUCTIONS

    def test_protects_the_words_that_have_no_everyday_translation(self):
        """The model told "speak Hindi" translates *everything*.

        Nobody is waiting for their औषधालय; they are waiting for their report.
        """
        for word in ("Appointment", "delivery", "OTP", "EMI"):
            assert word in CODE_MIXED_INSTRUCTIONS

    def test_pins_numbers_and_names_to_the_form_they_arrived_in(self):
        assert "Numbers, dates, times" in CODE_MIXED_INSTRUCTIONS

    def test_says_which_script_to_write_a_mixed_sentence_in(self):
        """Left open, a mixed sentence comes back in a script the voice reads
        letter by letter."""
        assert "Latin script" in CODE_MIXED_INSTRUCTIONS

    def test_forbids_narrating_the_switch(self):
        """ "Shall I continue in Hindi?" is the tell that this is a machine."""
        assert "Never comment on the language" in CODE_MIXED_INSTRUCTIONS

    def test_addresses_formality_not_only_vocabulary(self):
        """Correct words in a news-reader register still sounds like one."""
        assert "more formal than the caller" in CODE_MIXED_INSTRUCTIONS

    def test_is_a_labelled_block_rather_than_loose_text(self):
        """It is joined onto prompts an operator wrote; it has to be
        visibly ours when somebody reads the composed prompt back."""
        assert CODE_MIXED_INSTRUCTIONS.startswith("HOW TO SPEAK:")
