"""What an agent carries from one call to the next, and what it must not.

An agent that forgets everything between calls asks a returning customer for
their address again, which is the most obvious way a machine announces itself
as one. An agent that remembers the wrong things is worse: it tells a caller
something about themselves that is not true, confidently, before they have
spoken.

These tests are about that second failure, because it is the one that cannot
be fixed by trying harder.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.organisation_memory import (
    MAX_FACT_CHARS,
    durable_facts,
    is_durable,
    merge_for_prompt,
    promote_from_run,
    subject_key_for_run,
)


class TestWhatIsWorthRemembering:
    @pytest.mark.parametrize(
        "key", ["customer_name", "delivery_address", "company", "gst_number", "city"]
    )
    def test_a_fact_about_the_person_is_kept(self, key):
        assert is_durable(key, "something")

    @pytest.mark.parametrize(
        "key",
        [
            "preferred_time",
            "appointment_date",
            "booking_slot",
            "callback_time",
            "call_reason",
            "customer_query",
            "order_quantity",
            "paid_amount",
        ],
    )
    def test_a_fact_about_one_call_is_not(self, key):
        """ "Preferred slot" describes a booking, not a person. Presenting it
        next time as known is worse than not knowing -- the agent tells the
        caller what they want before they have said it."""
        assert not is_durable(key, "5pm")

    @pytest.mark.parametrize(
        "key", ["otp", "login_otp", "verification_code", "password", "card_number"]
    )
    def test_a_secret_is_never_remembered(self, key):
        """Storing one is a security defect, not a product choice."""
        assert not is_durable(key, "123456")

    def test_the_internal_bookkeeping_keys_are_not_facts(self):
        for key in ("call_disposition", "nodes_visited", "extracted_variables"):
            assert not is_durable(key, "x")

    def test_a_paragraph_is_not_a_fact(self):
        """Whole answers land in extracted variables sometimes, and a paragraph
        in front of the next call as established truth is how a prompt fills
        with noise."""
        assert not is_durable("notes", "x" * (MAX_FACT_CHARS + 1))
        assert is_durable("notes", "x" * MAX_FACT_CHARS)

    @pytest.mark.parametrize("value", [None, "", "   ", True, False, {"a": 1}, [1, 2]])
    def test_a_value_that_is_not_a_stated_fact_is_skipped(self, value):
        assert not is_durable("customer_name", value)

    def test_a_realistic_call_keeps_only_the_lasting_half(self):
        facts = durable_facts(
            {
                "customer_name": "Rahul",
                "delivery_address": "12 MG Road, Hosur",
                "preferred_time": "5:30pm",
                "otp": "998211",
                "call_disposition": "booked",
                "notes": "y" * 400,
            }
        )
        assert facts == {
            "customer_name": "Rahul",
            "delivery_address": "12 MG Road, Hosur",
        }

    @pytest.mark.parametrize("extracted", [None, {}, "not a mapping", 7])
    def test_a_call_that_collected_nothing_writes_nothing(self, extracted):
        assert durable_facts(extracted) == {}


class TestOperatorDataWins:
    def test_the_accounts_own_record_beats_what_we_inferred(self):
        """They uploaded that address. We inferred this one from a phone line
        with a child shouting in the background."""
        merged = merge_for_prompt(
            operator_data={"customer_name": "Rahul Sharma"},
            remembered={"customer_name": "Rahul", "city": "Hosur"},
        )
        assert merged["customer_name"] == "Rahul Sharma"
        assert merged["city"] == "Hosur"

    def test_an_empty_operator_value_does_not_erase_what_we_learned(self):
        """A blank column in an uploaded CSV is an absence, not an assertion
        that the agent is wrong."""
        merged = merge_for_prompt(
            operator_data={"city": "", "state": None},
            remembered={"city": "Hosur", "state": "TN"},
        )
        assert merged == {"city": "Hosur", "state": "TN"}

    def test_either_side_may_be_missing(self):
        assert merge_for_prompt(operator_data=None, remembered={"a": "1"}) == {"a": "1"}
        assert merge_for_prompt(operator_data={"a": "1"}, remembered=None) == {"a": "1"}


class TestSubject:
    def test_the_caller_is_keyed_the_same_way_contacts_are(self):
        """So a fact learned on a call and an attribute uploaded in a CSV
        describe one person rather than two who share a phone."""
        assert (
            subject_key_for_run({"phone_number": "+91 98765 43210"}) == "919876543210"
        )
        assert subject_key_for_run({"caller_number": "+919876543210"}) == "919876543210"

    def test_a_browser_call_has_no_subject(self):
        """Remembering those against a shared empty key would pool every
        anonymous visitor's facts into one imaginary person."""
        for context in ({}, None, {"phone_number": ""}, {"phone_number": "hello"}):
            assert subject_key_for_run(context) is None


class TestPromotion:
    @pytest.mark.asyncio
    async def test_a_call_writes_what_it_learned(self):
        with patch(
            "api.services.workflow.organisation_memory.db_client.remember_facts",
            AsyncMock(return_value=2),
        ) as remember:
            written = await promote_from_run(
                organization_id=42,
                workflow_run_id=9,
                gathered_context={
                    "extracted_variables": {"customer_name": "Rahul", "city": "Hosur"}
                },
                subject_key="919876543210",
            )
        assert written == 2
        kwargs = remember.await_args.kwargs
        assert kwargs["organization_id"] == 42
        assert kwargs["source_run_id"] == 9
        assert kwargs["facts"] == {"customer_name": "Rahul", "city": "Hosur"}

    @pytest.mark.asyncio
    async def test_a_call_with_no_subject_remembers_nothing(self):
        with patch(
            "api.services.workflow.organisation_memory.db_client.remember_facts",
            AsyncMock(),
        ) as remember:
            assert (
                await promote_from_run(
                    organization_id=42,
                    workflow_run_id=9,
                    gathered_context={"extracted_variables": {"customer_name": "R"}},
                    subject_key=None,
                )
                == 0
            )
        remember.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_memory_failure_never_breaks_the_post_call_pipeline(self):
        """The same pipeline sends the customer their confirmation. Losing a
        fact is a bad day; losing the confirmation is a refund."""
        with patch(
            "api.services.workflow.organisation_memory.db_client.remember_facts",
            AsyncMock(side_effect=RuntimeError("database is on fire")),
        ):
            assert (
                await promote_from_run(
                    organization_id=42,
                    workflow_run_id=9,
                    gathered_context={"extracted_variables": {"customer_name": "R"}},
                    subject_key="919876543210",
                )
                == 0
            )
