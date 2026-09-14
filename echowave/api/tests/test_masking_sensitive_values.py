"""What a call hears and must not write down.

A verification bot hears the OTP. A payments bot hears the card. Both used to
land verbatim in the transcript, the QA pass's extracted data, the gathered
context the webhooks deliver, and the thread. These tests pin the rules that
decide what is masked, and the two places the masking is applied.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.agent_templates import list_templates
from api.services.agent_templates._base import CALLING_DIRECTIONS
from api.services.privacy.masking import (
    MASK,
    mask_completed_run,
    mask_feedback_events,
    mask_mapping,
    mask_text,
)

BOT = "rtf-bot-text"
USER = "rtf-user-transcription"


def bot(text: str) -> dict:
    return {"type": BOT, "payload": {"text": text}}


def user(text: str, final: bool = True) -> dict:
    return {"type": USER, "payload": {"text": text, "final": final}}


def fake_db(run, update: AsyncMock) -> SimpleNamespace:
    """The completion pass imports the client at call time, so the package
    attribute is what gets swapped."""
    return SimpleNamespace(
        get_workflow_run=AsyncMock(return_value=run), update_workflow_run=update
    )


class TestWhatIsACode:
    def test_a_code_given_after_the_bot_asked_is_masked_whole(self):
        assert mask_text("it is 482913", asked_for_code=True) == f"it is {MASK}"

    def test_the_same_digits_without_a_question_are_an_order_number(self):
        """Masking every six-digit number turns the transcript into noise;
        an order id is what the operator opens the call to read."""
        assert mask_text("order 482913 has shipped") == "order 482913 has shipped"

    def test_the_bot_repeating_a_code_back_is_masked_in_its_own_line(self):
        assert mask_text("your OTP is 48 29 13, is that right") == (
            f"your OTP is {MASK}, is that right"
        )

    def test_a_number_before_the_code_word_in_the_same_line_is_kept(self):
        """The code word covers what follows it, not the order number the
        bot mentioned first."""
        assert mask_text("order 482913 is ready, read me the OTP") == (
            "order 482913 is ready, read me the OTP"
        )

    def test_a_pin_is_a_code(self):
        assert mask_text("my PIN is 4321") == f"my PIN is {MASK}"

    def test_a_year_or_an_amount_after_the_question_is_not_a_code(self):
        """Four to eight digits is the code shape; a three-digit amount or a
        ten-digit phone number after the question is left alone."""
        assert mask_text("500 rupees", asked_for_code=True) == "500 rupees"
        assert mask_text("9876543210", asked_for_code=True) == "9876543210"


class TestWhatIsAlwaysMasked:
    def test_a_card_number_that_passes_luhn_keeps_its_last_four(self):
        assert mask_text("card 4111 1111 1111 1111 please") == (
            f"card {MASK}1111 please"
        )

    def test_sixteen_digits_that_fail_luhn_are_not_a_card(self):
        assert mask_text("ref 1234 5678 9012 3456") == "ref 1234 5678 9012 3456"

    def test_an_aadhaar_number_keeps_its_last_four_even_unnamed(self):
        # 223456789018 passes Verhoeff; the value alone is enough.
        assert mask_text("it is 2234 5678 9018") == f"it is {MASK}9018"

    def test_twelve_digits_that_fail_verhoeff_are_left_alone(self):
        assert mask_text("ref 2234 5678 9012") == "ref 2234 5678 9012"

    def test_a_pan_keeps_its_last_four(self):
        assert mask_text("PAN ABCDE1234F") == f"PAN {MASK}234F"

    def test_an_account_number_after_the_question_keeps_its_last_four(self):
        assert mask_text("it is 123456789012", asked_for_account=True) == (
            f"it is {MASK}9012"
        )


class TestEvents:
    def test_the_bots_question_sets_the_context_for_the_callers_reply(self):
        events = [
            bot("Your order 482913 is ready. Please read me the OTP we sent."),
            user("it is 774411"),
            bot("Thank you, that matches."),
            user("my order is 482913"),
        ]
        masked = mask_feedback_events(events)
        assert masked[0]["payload"]["text"] == (
            "Your order 482913 is ready. Please read me the OTP we sent."
        )
        assert masked[1]["payload"]["text"] == f"it is {MASK}"
        # The context is one turn deep: after the bot moved on, digits are
        # digits again.
        assert masked[3]["payload"]["text"] == "my order is 482913"

    def test_the_input_is_not_touched_and_other_events_pass_through(self):
        events = [
            bot("read me the OTP"),
            user("123456"),
            {"type": "rtf-node-transition", "payload": {"node_id": "n1"}},
        ]
        masked = mask_feedback_events(events)
        assert events[1]["payload"]["text"] == "123456"
        assert masked[2] is events[2]
        assert masked[1]["payload"]["final"] is True


class TestMappings:
    def test_a_code_under_a_code_key_is_masked_whatever_it_looks_like(self):
        out = mask_mapping({"otp": "482913", "otp_entered": 482913, "name": "Ravi"})
        assert out == {"otp": MASK, "otp_entered": MASK, "name": "Ravi"}

    def test_value_rules_apply_under_any_key(self):
        out = mask_mapping(
            {"notes": {"said": "card 4111111111111111"}, "ids": ["ABCDE1234F"]}
        )
        assert out == {"notes": {"said": f"card {MASK}1111"}, "ids": [f"{MASK}234F"]}

    def test_an_order_number_under_an_ordinary_key_survives(self):
        assert mask_mapping({"order_id": "482913"}) == {"order_id": "482913"}


class TestTheCompletionPass:
    @pytest.mark.asyncio
    async def test_masks_gathered_context_and_logs_before_anything_reads_them(self):
        run = SimpleNamespace(
            gathered_context={"otp": "482913", "customer": "Ravi"},
            logs={"realtime_feedback_events": [bot("read me the OTP"), user("482913")]},
        )
        update = AsyncMock()
        with patch("api.db.db_client", fake_db(run, update)):
            assert await mask_completed_run(7) is True
        kwargs = [call.kwargs for call in update.await_args_list]
        assert {
            "run_id": 7,
            "gathered_context": {"otp": MASK, "customer": "Ravi"},
        } in kwargs
        logs = [k for k in kwargs if "logs" in k][0]["logs"]
        assert logs["realtime_feedback_events"][1]["payload"]["text"] == MASK

    @pytest.mark.asyncio
    async def test_a_clean_run_is_not_rewritten(self):
        run = SimpleNamespace(
            gathered_context={"order_id": "482913"},
            logs={"realtime_feedback_events": [bot("hello"), user("order 482913")]},
        )
        update = AsyncMock()
        with patch("api.db.db_client", fake_db(run, update)):
            assert await mask_completed_run(7) is False
        update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failure_is_logged_not_raised(self):
        db = SimpleNamespace(get_workflow_run=AsyncMock(side_effect=RuntimeError("db")))
        with patch("api.db.db_client", db):
            assert await mask_completed_run(7) is False


class TestThePromptSide:
    def test_every_template_tells_the_bot_not_to_repeat_a_code(self):
        """Masking the record is half; the other half is the bot not saying
        the whole code back on a line somebody in the room can hear."""
        for template in list_templates():
            if template.direction not in CALLING_DIRECTIONS:
                continue
            assert any("OTP" in rule for rule in template.guardrails), template.id
