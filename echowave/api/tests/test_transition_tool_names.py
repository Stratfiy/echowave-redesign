"""The name the model sees for a transition, and why it cannot be the label.

Every node transition in a workflow is a tool call, so the set of tool names
offered at a node *is* the set of moves the agent can make. A name the provider
rejects fails the whole request; two names that collide are worse, because the
request succeeds and one of the moves is simply gone. The agent keeps talking
and the branch behind the shadowed edge is never reached.

The old rule -- every character outside [a-z0-9] becomes an underscore -- is
fine for an English label and destroys an Indian one: every character of
"நேரம் பதிவு" is outside that set, so the name was a row of underscores, and
any second Tamil label was the same row.
"""

import re

import pytest

from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import (
    TOOL_NAME_MAX_CHARS,
    WorkflowGraph,
    slugify_tool_name,
)

# What OpenAI accepts. A name outside this is a rejected request, not a bad call.
VALID_TOOL_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _graph(*edge_labels, condition="the caller is ready"):
    """A start node with one outgoing edge per label, all landing on the end."""
    nodes = [
        {
            "id": "start",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {"name": "Start", "prompt": "Greet the caller."},
        },
        {
            "id": "end",
            "type": "endCall",
            "position": {"x": 400, "y": 0},
            "data": {"name": "End", "prompt": "Say goodbye."},
        },
    ]
    edges = [
        {
            "id": f"e-{index}",
            "source": "start",
            "target": "end",
            "data": {"label": label, "condition": condition},
        }
        for index, label in enumerate(edge_labels)
    ]
    return WorkflowGraph(ReactFlowDTO.model_validate({"nodes": nodes, "edges": edges}))


def _names(*edge_labels):
    graph = _graph(*edge_labels)
    return [edge.get_function_name() for edge in graph.nodes["start"].out_edges]


class TestAnEnglishLabelIsUnchanged:
    """The fix must not rename the tools of agents that are working today."""

    def test_a_plain_label_reads_as_it_always_did(self):
        assert _names("Book appointment") == ["book_appointment"]

    def test_punctuation_and_spacing_collapse_to_one_underscore(self):
        # The old rule emitted one underscore per character, so an em dash
        # surrounded by spaces became three.
        assert _names("Quote — over 10T") == ["quote_over_10t"]

    def test_digits_survive(self):
        assert _names("3 or more") == ["3_or_more"]


class TestALabelThatCannotBeAName:
    def test_two_tamil_labels_do_not_collide(self):
        """The bug. Both used to be '___________'."""
        names = _names("நேரம் பதிவு", "வேறு ஏதாவது")
        assert len(set(names)) == 2, names
        assert all(VALID_TOOL_NAME.match(name) for name in names), names

    def test_a_hindi_label_gets_a_usable_name(self):
        assert _names("होल्ड करें") == ["option_1"]

    def test_a_label_of_only_punctuation_gets_a_usable_name(self):
        assert _names("...") == ["option_1"]

    def test_mixed_script_keeps_the_ascii_it_has(self):
        assert _names("Book நேரம்") == ["book"]


class TestNamesAreAlwaysDistinct:
    def test_two_identical_labels_get_two_names(self):
        """Nothing forbids an operator labelling two edges the same way."""
        names = _names("yes", "yes")
        assert names == ["yes", "yes_2"]

    def test_labels_differing_only_in_punctuation_still_separate(self):
        names = _names("Yes!", "Yes?")
        assert len(set(names)) == 2, names

    def test_three_unnameable_labels_give_three_names(self):
        names = _names("ஒன்று", "இரண்டு", "மூன்று")
        assert len(set(names)) == 3, names


class TestTheProviderWouldAcceptEveryName:
    @pytest.mark.parametrize(
        "label",
        [
            "Book appointment",
            "நேரம் பதிவு",
            "होल्ड करें",
            "...",
            "3 or more",
            "a" * 200,
            "Caller " * 40,
            "இது " * 40,
        ],
    )
    def test_a_single_edge(self, label):
        (name,) = _names(label)
        assert VALID_TOOL_NAME.match(name), name

    def test_a_long_label_is_cut_to_the_limit(self):
        (name,) = _names("a" * 200)
        assert len(name) == TOOL_NAME_MAX_CHARS

    def test_a_deduplicated_long_name_stays_within_the_limit(self):
        """Appending a suffix must shorten the stem, not overflow past 64."""
        names = _names("a" * 200, "a" * 200)
        assert len(set(names)) == 2, names
        assert all(len(name) <= TOOL_NAME_MAX_CHARS for name in names), names
        assert all(VALID_TOOL_NAME.match(name) for name in names), names


class TestTheLabelStillReachesTheModel:
    def test_a_tamil_label_is_carried_in_the_description(self):
        """It cannot be in the name, so it goes where script does not matter.
        Otherwise the operator's own word for this move appears nowhere in the
        request and the model is choosing between 'option_1' and 'option_2'."""
        from api.services.workflow.pipecat_engine_context_composer import (
            _transition_description,
        )

        (edge,) = _graph("நேரம் பதிவு").nodes["start"].out_edges
        description = _transition_description(edge)
        assert "நேரம் பதிவு" in description
        assert "the caller is ready" in description

    def test_an_english_label_leaves_the_description_alone(self):
        from api.services.workflow.pipecat_engine_context_composer import (
            _transition_description,
        )

        (edge,) = _graph("Book appointment").nodes["start"].out_edges
        assert _transition_description(edge) == "the caller is ready"


class TestTheSlugHelper:
    def test_it_reports_no_ascii_rather_than_guessing(self):
        assert slugify_tool_name("நேரம்") == ""

    def test_it_tolerates_none(self):
        assert slugify_tool_name(None) == ""
