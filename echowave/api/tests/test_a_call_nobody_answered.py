"""A call nobody spoke on does not need a model to classify it.

Seen on 22 September 2026, measuring 54 real calls. Eight of them had no
caller turn at all: the recording line and then silence, or a greeting into a
dead line. The classifier was asked to read them anyway and answered three
different ways -- five ``no_answer``, one ``not_interested``, two
``unclear``. One of those is right, and the question was never hard.

So the rule runs first and the model is not called. The rule is deliberately
conservative: it fires only when the transcript *positively shows* speaker
turns and none of them are the caller's, because "this format is not one we
can read" and "nobody answered" are different facts.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow import disposition_run
from api.services.workflow.disposition import (
    DEFAULT_DISPOSITIONS,
    UNCLEAR,
    nobody_spoke,
    parse_taxonomy,
    silent_result,
)

#: The shape the transcript writer actually produces, from a real call.
ONLY_US = (
    "[2026-09-20T06:58:58.622+00:00] assistant: Just so you know, this call is "
    "recorded for quality and training purposes."
)
GREETED_NOBODY = (
    "[2026-09-15T20:07:34.923+00:00] assistant: Just so you know, this call is "
    "recorded for quality and training purposes. Namaste. How may I help you today?"
)
A_CONVERSATION = (
    "[2026-09-20T07:13:56.593+00:00] assistant: Namaste. How may I help you?\n"
    "[2026-09-20T07:13:59.274+00:00] user: I want to book a checkup.\n"
    "[2026-09-20T07:14:11.004+00:00] assistant: Monday morning is free."
)


class TestReadingTheTranscript:
    def test_only_our_own_turns_means_nobody_answered(self):
        assert nobody_spoke(ONLY_US) is True
        assert nobody_spoke(GREETED_NOBODY) is True

    def test_one_caller_turn_is_enough_to_be_a_conversation(self):
        assert nobody_spoke(A_CONVERSATION) is False

    def test_the_caller_goes_by_several_names(self):
        for word in ("user", "caller", "customer", "human"):
            assert nobody_spoke(f"agent: hello\n{word}: hi there") is False

    def test_a_transcript_we_cannot_parse_is_not_called_silent(self):
        # The important negative. A blob with no speaker labels might be a
        # long conversation in a format we have not seen; filing it as "no
        # answer" would be inventing an outcome, so it goes to the model.
        assert nobody_spoke("The caller asked about Tuesday and we booked it.") is False
        assert nobody_spoke("") is False
        assert nobody_spoke(None) is False

    def test_timestamps_are_optional(self):
        assert nobody_spoke("assistant: hello?") is True
        assert nobody_spoke("[12:00] Assistant: hello?") is True

    def test_the_word_user_inside_a_sentence_is_not_a_turn(self):
        # "the user said" in our own speech must not count as the caller
        # speaking, or a monologue mentioning them looks like a conversation.
        assert nobody_spoke("assistant: I will ask the user about Tuesday.") is True


class TestWhichCodeItFiles:
    def test_the_shipped_taxonomy_has_a_word_for_it(self):
        assert silent_result(parse_taxonomy(None)) == "no_answer"

    def test_a_taxonomy_without_it_falls_back_to_unclear(self):
        # A workflow whose operator removed `no_answer` gets `unclear`
        # rather than a code `coerce_result` would drop on the floor.
        trimmed = [
            dict(entry)
            for entry in DEFAULT_DISPOSITIONS
            if entry["code"] in ("booked", UNCLEAR)
        ]
        assert silent_result(trimmed) == UNCLEAR


@pytest.mark.asyncio
class TestTheModelIsNotAsked:
    async def _classify(self, transcript: str):
        run = SimpleNamespace(initial_context=None)
        with (
            patch.object(
                disposition_run, "resolve_user_llm_config", AsyncMock()
            ) as resolve,
            patch.object(disposition_run, "_run_llm_inference", AsyncMock()) as infer,
        ):
            result = await disposition_run.classify_call(
                workflow_run=run, transcript=transcript, disposition_codes=None
            )
        return result, resolve, infer

    async def test_a_silent_call_is_no_answer_and_costs_nothing(self):
        result, resolve, infer = await self._classify(ONLY_US)
        assert result["dispositions"] == ["no_answer"]
        assert result["reason"] == "nobody_spoke"
        resolve.assert_not_awaited()
        infer.assert_not_awaited()
        # No model ran, so there is no spend to report and nothing to bill.
        assert "token_usage" not in result

    async def test_a_conversation_still_goes_to_the_model(self):
        _, resolve, infer = await self._classify(A_CONVERSATION)
        resolve.assert_awaited()

    async def test_an_empty_transcript_keeps_its_own_reason(self):
        # Already handled, and worth keeping distinct: nothing was recorded
        # at all, which is a different fact from nobody answering.
        result, _, infer = await self._classify("   ")
        assert result["dispositions"] == [UNCLEAR]
        assert result["reason"] == "no_transcript"
        infer.assert_not_awaited()


class TestAgainstTheRealCalls:
    """The eight from the measurement, verbatim in shape.

    Each is the opening of a real transcript that had no caller turn. They
    are here because the rule was written from them and a regression would
    otherwise only show up as an outcome quietly going wrong months later.
    """

    REAL_SILENT = (
        "[2026-09-20T06:58:58.622+00:00] assistant: Just so you know, this call "
        "is recorded for quality and training purposes.",
        "[2026-09-15T20:07:34.923+00:00] assistant: Just so you know, this call "
        "is recorded for quality and training purposes. Namaste, . How may I "
        "help you today?",
        "[2026-09-11T14:52:28.862+00:00] assistant: Vanakkam, Narayani Dental "
        "Clinic, Hosur. Tamil, English, Hindi, Kannada or Telugu — which "
        "language shall we speak?",
    )

    REAL_CONVERSATIONS = (
        "[t] assistant: Namaste.\n[t] user: आपका booking पर आप दमन account.",
        "[t] assistant: Elock support.\n[t] user: हाँ जी हाँ जी।",
    )

    def test_every_silent_one_fires(self):
        assert all(nobody_spoke(text) for text in self.REAL_SILENT)

    def test_no_real_conversation_fires(self):
        assert not any(nobody_spoke(text) for text in self.REAL_CONVERSATIONS)
