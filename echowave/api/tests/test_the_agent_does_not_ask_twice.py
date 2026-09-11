"""Being asked again for something you just said is what a caller forgives least.

    Caller: next Thursday, eleven o'clock please.
    Agent:  ... and what day and time would suit you?

The cause is not bad wording. A node prompt is an instruction -- "collect the
name, a mobile number, the treatment and a preferred time" -- and an
instruction outranks a memory of the transcript. So what has already been
established is stated as fact in the system prompt rather than left for the
model to notice.
"""

from __future__ import annotations

from api.services.workflow.known_values import (
    HEADING,
    MAX_VALUE_CHARS,
    MAX_VALUES,
    known_values_block,
)


class TestWhatTheCallerAlreadySaid:
    def test_it_states_the_values(self):
        block = known_values_block(
            {
                "preferred_time": "next Thursday eleven o'clock",
                "treatment": "root canal",
            }
        )
        assert "preferred time: next Thursday eleven o'clock" in block
        assert "treatment: root canal" in block

    def test_it_tells_the_model_not_to_ask_again(self):
        block = known_values_block({"treatment": "root canal"})
        assert "never ask the caller for any of them again" in block

    def test_underscores_become_words(self):
        """The model reads this out loud if it reads it wrong."""
        assert "mobile number: 9840012345" in known_values_block(
            {"mobile_number": "9840012345"}
        )

    def test_a_boolean_is_said_as_a_person_would(self):
        assert "is new patient: yes" in known_values_block({"is_new_patient": True})
        assert "is new patient: no" in known_values_block({"is_new_patient": False})

    def test_a_number_survives(self):
        assert "party size: 4" in known_values_block({"party_size": 4})


class TestNothingEstablishedYet:
    def test_an_empty_context_produces_no_block(self):
        """A heading that says "already established" over an empty list invites
        the model to wonder what it has forgotten."""
        assert known_values_block({}) is None

    def test_none_produces_no_block(self):
        assert known_values_block(None) is None

    def test_only_bookkeeping_produces_no_block(self):
        assert known_values_block({"nodes_visited": [1, 2], "call_tags": []}) is None


class TestTheEngineSBookkeepingStaysOut:
    """None of it was said by a caller, and a model told "nodes visited: 4"
    will eventually mention it out loud."""

    def test_internal_keys_are_dropped(self):
        block = known_values_block(
            {
                "name": "Kumar",
                "nodes_visited": [1, 2, 3],
                "call_disposition": "user_hangup",
                "call_tags": ["x"],
                "extracted_variables": {"name": "Kumar"},
            }
        )
        assert "Kumar" in block
        for leak in ("nodes visited", "call disposition", "call tags", "extracted"):
            assert leak not in block

    def test_underscore_prefixed_keys_are_dropped(self):
        assert known_values_block({"_internal": "x", "name": "Kumar"}).count("\n") == 1

    def test_nested_structures_are_dropped(self):
        block = known_values_block(
            {"payload": {"a": 1}, "tags": ["x"], "name": "Kumar"}
        )
        assert "payload" not in block
        assert "tags" not in block

    def test_blank_values_are_dropped(self):
        assert known_values_block({"notes": "   ", "name": "Kumar"}).count("\n") == 1


class TestItCannotBecomeASecondPrompt:
    def test_the_number_of_values_is_capped(self):
        block = known_values_block({f"field_{i}": f"value {i}" for i in range(40)})
        assert block.count("\n- ") == MAX_VALUES

    def test_a_paragraph_is_not_restated(self):
        """The extraction pass sometimes captures a whole answer."""
        block = known_values_block(
            {"summary": "x" * (MAX_VALUE_CHARS + 1), "name": "Kumar"}
        )
        assert "summary" not in block
        assert "Kumar" in block


class TestItDoesNotChurnTheCache:
    def test_the_same_values_render_identically(self):
        """This block sits after the instruction blocks precisely so the prefix
        stays cacheable; it must not re-order between turns."""
        first = known_values_block({"b": "two", "a": "one", "c": "three"})
        second = known_values_block({"c": "three", "a": "one", "b": "two"})
        assert first == second

    def test_values_are_in_a_stable_order(self):
        block = known_values_block({"zebra": "z", "apple": "a"})
        assert block.index("apple") < block.index("zebra")


class TestItIsInTheComposedPrompt:
    def test_the_block_lands_after_the_instruction_blocks(self):
        """Everything above it is byte-identical for the life of a call."""
        from api.services.workflow.step_instructions import (
            ACTION_HONESTY,
            FACT_HONESTY,
        )

        block = known_values_block({"treatment": "root canal"})
        prompt = "\n\n".join([ACTION_HONESTY, FACT_HONESTY, block])
        assert prompt.index(ACTION_HONESTY) < prompt.index(HEADING)
        assert prompt.index(FACT_HONESTY) < prompt.index(HEADING)
