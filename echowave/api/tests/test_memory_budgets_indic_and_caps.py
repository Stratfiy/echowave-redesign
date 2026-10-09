"""Memory budgets on Indian-language text, and the remembered-facts cap.

Two places decide how much reaches a prompt without counting real tokens:

* ``chat_memory.tokens_of`` estimates a message's tokens to fill the chat
  window. At four characters a token, Tamil and Hindi text was counted at a
  fraction of what vendors bill for it, so a Tamil thread could run past
  the plan's budget by a multiple. Indian scripts now count at two.
* ``organisation_memory.remembered_block`` keeps the first
  ``MAX_REMEMBERED`` confirmed facts. ``recall_for_bot`` put every
  organisation fact before the bot's own, so an organisation past the cap
  silently lost every instruction that was the bot's alone.

The oversized-newest-message behaviour is pinned, not changed: the window
always keeps the newest message whole (the question being answered is never
dropped), so a single long paste is the one way past the budget. Clipping it
belongs where the history is assembled, not in the estimate.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import chat_memory
from api.services.workflow import organisation_memory as memory

TAMIL = "நாளை காலை எட்டரை மணிக்கு எனக்கு அழைத்து முன்மொழிவை அனுப்ப நினைவூட்டு"
HINDI = "कल सुबह साढ़े आठ बजे मुझे फ़ोन करके प्रस्ताव भेजने की याद दिलाना"
MIXED = "kal 8:30 ko call karke proposal bhejne ka remind karna, सुबह वाला"


class TestTheEstimateOnIndianScripts:
    def test_latin_text_is_unchanged(self):
        assert chat_memory.tokens_of("x" * 400) == 100
        assert chat_memory.tokens_of("ok") == 1

    @pytest.mark.parametrize("text", [TAMIL, HINDI])
    def test_indian_scripts_count_at_two_characters_a_token(self, text):
        indic = sum(1 for ch in text if 0x0900 <= ord(ch) <= 0x0DFF)
        assert indic > len(text) // 2  # the sample really is in that script
        estimate = chat_memory.tokens_of(text)
        # At least one token per two script characters; the old figure was a
        # quarter of the length, which undercounted by two times or more.
        assert estimate >= indic // chat_memory.INDIC_CHARS_PER_TOKEN
        assert estimate > -(-len(text) // chat_memory.CHARS_PER_TOKEN)

    def test_code_mixed_text_counts_each_part_at_its_own_rate(self):
        latin = sum(1 for ch in MIXED if ord(ch) < 0x0900)
        indic = len(MIXED) - latin
        assert chat_memory.tokens_of(MIXED) == -(-latin // 4) + -(-indic // 2)

    def test_a_tamil_thread_fills_a_smaller_window_than_the_same_length_in_english(
        self,
    ):
        tamil = [TAMIL] * 50
        english = ["x" * len(TAMIL)] * 50
        budget = 400
        kept_ta, used_ta = chat_memory.window(tamil, budget)
        kept_en, _ = chat_memory.window(english, budget)
        assert kept_ta < kept_en
        assert used_ta <= budget

    def test_an_oversized_newest_message_is_kept_whole_and_overruns_the_budget(self):
        """Pinned behaviour: the newest message is never dropped, so a long
        paste alone overruns the budget. The window says so in ``used``;
        clipping is for whoever assembles the history."""
        paste = TAMIL * 400  # roughly thirty thousand characters
        kept, used = chat_memory.window([paste, "earlier"], budget_tokens=8_000)
        assert kept == 1
        assert used > 8_000
        assert used == chat_memory.tokens_of(paste)


def _row(key, value, workflow_id=None):
    return SimpleNamespace(key=key, value=value, workflow_id=workflow_id)


class TestTheRememberedFactsCap:
    @pytest.mark.asyncio
    async def test_a_bots_own_facts_survive_an_organisation_past_the_cap(self):
        org_rows = [
            _row(f"house rule {i}", f"value {i}")
            for i in range(memory.MAX_REMEMBERED + 5)
        ]
        bot_rows = [
            _row("answer in", "Tamil", workflow_id=7),
            _row("never quote", "prices on the phone", workflow_id=7),
        ]
        with patch.object(
            memory.db_client,
            "organisation_memory",
            AsyncMock(return_value=org_rows + bot_rows),
        ):
            recalled = await memory.recall_for_bot(organization_id=42, workflow_id=7)
        block = memory.remembered_block(recalled)
        assert "- answer in: Tamil" in block
        assert "- never quote: prices on the phone" in block
        assert block.count("\n- ") == memory.MAX_REMEMBERED

    @pytest.mark.asyncio
    async def test_the_bot_still_wins_a_shared_key_and_org_facts_are_inherited(self):
        rows = [
            _row("closed on", "Sunday"),
            _row("language", "English"),
            _row("language", "Hindi", workflow_id=7),
        ]
        with patch.object(
            memory.db_client, "organisation_memory", AsyncMock(return_value=rows)
        ):
            recalled = await memory.recall_for_bot(organization_id=42, workflow_id=7)
        assert recalled == {"language": "Hindi", "closed on": "Sunday"}
        assert list(recalled) == ["language", "closed on"]

    def test_blank_values_do_not_take_a_slot(self):
        facts = {f"blank {i}": "  " for i in range(10)}
        facts.update({f"key {i}": f"value {i}" for i in range(memory.MAX_REMEMBERED)})
        block = memory.remembered_block(facts)
        assert block.count("\n- ") == memory.MAX_REMEMBERED
        assert f"- key {memory.MAX_REMEMBERED - 1}: value" in block
