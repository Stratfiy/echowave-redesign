"""A rejected workflow graph must name what is wrong, once.

``WorkflowGraph`` rejects two ways -- with a list of errors, and with a plain
sentence for a cycle -- and the route extended its error list with whichever
came out. Extending a list with a string appends its characters, so "workflow
contains a cycle" reached the editor as twenty-four one-letter errors attached
to no node.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from api.routes.workflow import graph_failure_errors
from api.services.workflow.errors import ItemKind


class _Model(BaseModel):
    count: int


class TestASentence:
    def test_a_cycle_is_one_error_not_one_per_letter(self):
        errors = graph_failure_errors(ValueError("workflow contains a cycle"))

        assert len(errors) == 1

    def test_it_keeps_the_sentence_intact(self):
        [error] = graph_failure_errors(ValueError("workflow contains a cycle"))

        assert error["message"] == "workflow contains a cycle"

    def test_it_is_attributed_to_the_workflow_rather_than_a_node(self):
        """A cycle is a property of the graph -- there is no one node to
        blame, and blaming an arbitrary one would send the reader to the wrong
        place in the editor."""
        [error] = graph_failure_errors(ValueError("workflow contains a cycle"))

        assert error["kind"] == ItemKind.workflow
        assert error["id"] is None


class TestAList:
    def test_collected_errors_pass_through_untouched(self):
        collected = [
            {"kind": ItemKind.node, "id": "n1", "field": None, "message": "no prompt"},
            {"kind": ItemKind.edge, "id": "e1", "field": None, "message": "dangling"},
        ]

        assert graph_failure_errors(ValueError(collected)) == collected


class TestAValueErrorCarryingNoArgs:
    def test_a_pydantic_error_does_not_take_the_response_down(self):
        """Pydantic's ValidationError is a ValueError with empty ``args``.
        Indexing it here raised IndexError, discarding every error already
        found before this point."""
        with pytest.raises(ValidationError) as caught:
            _Model(count="not a number")

        errors = graph_failure_errors(caught.value)

        assert len(errors) == 1
        assert errors[0]["message"]
