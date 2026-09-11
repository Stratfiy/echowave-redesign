"""Handing the collected value over with the decision to move.

A node that collects something used to collect it afterwards: the extraction
pass is a second LLM call, fired at the transition, that re-reads the
conversation for values the model had just worked out for itself. These tests
pin the other route -- the variables offered as arguments on the transition --
and, at least as importantly, everything that must not change because of it.

The two properties that matter most are at the bottom: a node that collects
nothing has exactly the schema it has today, and a variable the model did not
supply still reaches the extraction pass.
"""

import pytest

from api.services.workflow.dto import ExtractionVariableDTO
from api.services.workflow.transition_arguments import (
    MAX_ARGUMENTS,
    argument_properties,
    supplied_values,
)


class _Node:
    """Only the three attributes the helper reads."""

    def __init__(self, enabled=True, variables=None):
        self.extraction_enabled = enabled
        self.extraction_variables = variables


def _var(name, type_="string", prompt=None):
    return ExtractionVariableDTO(name=name, type=type_, prompt=prompt)


class TestWhatTheToolOffers:
    def test_a_collecting_node_offers_its_variables(self):
        props = argument_properties(
            _Node(variables=[_var("tt_number"), _var("invoice_number")])
        )
        assert sorted(props) == ["invoice_number", "tt_number"]

    def test_the_declared_type_reaches_the_schema(self):
        props = argument_properties(
            _Node(variables=[_var("otp_received", "boolean"), _var("amount", "number")])
        )
        assert props["otp_received"]["type"] == "boolean"
        assert props["amount"]["type"] == "number"

    def test_the_extraction_hint_becomes_the_description(self):
        props = argument_properties(
            _Node(
                variables=[
                    _var(
                        "tt_number", prompt="The truck registration, e.g. TN 70 AV 8040"
                    )
                ]
            )
        )
        assert "TN 70 AV 8040" in props["tt_number"]["description"]

    def test_a_variable_with_no_hint_still_describes_itself(self):
        props = argument_properties(_Node(variables=[_var("intent")]))
        assert "intent" in props["intent"]["description"]

    def test_many_variables_are_capped(self):
        """One unusual node must not bloat every edge leaving it."""
        props = argument_properties(_Node(variables=[_var(f"v{i}") for i in range(30)]))
        assert len(props) == MAX_ARGUMENTS


class TestWhatTheToolDoesNotOffer:
    """A node that collects nothing keeps the schema it has today."""

    def test_a_node_without_extraction_offers_nothing(self):
        assert argument_properties(_Node(enabled=False, variables=[_var("x")])) == {}

    def test_a_node_with_extraction_on_and_no_variables_offers_nothing(self):
        assert argument_properties(_Node(variables=[])) == {}
        assert argument_properties(_Node(variables=None)) == {}

    def test_no_node_offers_nothing(self):
        assert argument_properties(None) == {}


class TestReadingBackWhatTheModelSent:
    def test_a_supplied_value_is_taken(self):
        props = argument_properties(_Node(variables=[_var("tt_number")]))
        assert supplied_values(props, {"tt_number": "TN70AV8040"}) == {
            "tt_number": "TN70AV8040"
        }

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_a_blank_is_not_a_value(self, blank):
        """Omitted, null and empty all mean 'I do not have this'. None of them
        may overwrite something the call already knows."""
        props = argument_properties(_Node(variables=[_var("tt_number")]))
        assert supplied_values(props, {"tt_number": blank}) == {}

    def test_an_omitted_argument_is_not_a_value(self):
        props = argument_properties(_Node(variables=[_var("tt_number")]))
        assert supplied_values(props, {}) == {}

    def test_false_and_zero_are_values(self):
        """A boolean that is false is an answer, not a blank -- the bug waiting
        to happen in any 'if not value' shortcut."""
        props = argument_properties(
            _Node(variables=[_var("gps_blinking", "boolean"), _var("count", "number")])
        )
        got = supplied_values(props, {"gps_blinking": False, "count": 0})
        assert got == {"gps_blinking": False, "count": 0}

    def test_anything_not_asked_for_is_ignored(self):
        """The model inventing an extra key must not write into the context."""
        props = argument_properties(_Node(variables=[_var("tt_number")]))
        got = supplied_values(props, {"tt_number": "X", "balance_due": "99999"})
        assert got == {"tt_number": "X"}

    def test_a_non_dict_argument_payload_is_survivable(self):
        props = argument_properties(_Node(variables=[_var("tt_number")]))
        assert supplied_values(props, None) == {}
        assert supplied_values(props, "tt_number=X") == {}

    def test_nothing_offered_means_nothing_taken(self):
        assert supplied_values({}, {"anything": "at all"}) == {}
