"""The branch node as a graph citizen: saving it, validating it, routing it.

`test_branching.py` pins the rule engine in isolation. This file pins the two
seams around it, and both exist because of the same failure shape: **a branch
node that is wrong does not fail loudly.** It routes everything to the default,
the calls complete, and nobody notices the routing was never happening.

So the rules are checked when the node is saved, and the labels are checked
against the actual edges when the graph is built. Neither can wait for runtime.
"""

import pytest
from pydantic import ValidationError

from api.services.workflow.dto import BranchNodeData, ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph


def _branch(rules, default_label="default", name="Branch"):
    return BranchNodeData(name=name, rules=rules, default_label=default_label)


def _rule(label, variable="v", operator="equals", value="x"):
    return {"label": label, "variable": variable, "operator": operator, "value": value}


class TestSavingABranchNode:
    def test_a_well_formed_node_validates(self):
        node = _branch([_rule("high", "amount", "greater_than", "50000")], "normal")
        assert node.default_label == "normal"
        assert node.rules[0].variable == "amount"

    def test_an_unconfigured_node_is_allowed(self):
        """Someone dropped a branch on the canvas and has not filled it in.
        Refusing to save that would make the node unusable — you cannot
        configure what you cannot place."""
        assert _branch([]).rules == []

    def test_a_rule_that_could_never_match_is_refused(self):
        with pytest.raises(ValidationError) as exc:
            _branch([_rule("high", "amount", "greater_than", "fifty thousand")])
        assert "not a number" in str(exc.value)

    def test_an_unknown_operator_is_refused(self):
        with pytest.raises(ValidationError) as exc:
            _branch([_rule("high", "amount", "is_roughly", "50000")])
        assert "is_roughly" in str(exc.value)

    def test_two_rules_sharing_a_label_are_refused(self):
        """The second could never be reached: the first match wins and both
        point at the same edge, so the second is dead configuration that reads
        as though it does something."""
        with pytest.raises(ValidationError) as exc:
            _branch(
                [
                    _rule("high", "amount", "greater_than", "50000"),
                    _rule("high", "tier", "equals", "gold"),
                ]
            )
        assert "already used by rule 1" in str(exc.value)

    def test_labels_collide_case_insensitively(self):
        """'High' and 'high' are one edge on the canvas."""
        with pytest.raises(ValidationError):
            _branch(
                [
                    _rule("High", "amount", "greater_than", "50000"),
                    _rule("high", "tier", "equals", "gold"),
                ]
            )

    def test_the_default_cannot_reuse_a_rule_label(self):
        """If matched and unmatched calls leave by the same edge, the run log
        cannot tell them apart — which defeats the reason for having a
        deterministic branch at all."""
        with pytest.raises(ValidationError) as exc:
            _branch([_rule("standard", "amount", "greater_than", "50000")], "standard")
        assert "own edge" in str(exc.value)

    def test_every_problem_is_reported_at_once(self):
        """A form that surfaces one error per save is a form people give up on."""
        with pytest.raises(ValidationError) as exc:
            _branch(
                [
                    _rule("a", "", "equals", "x"),
                    _rule("b", "v", "greater_than", "not a number"),
                ]
            )
        message = str(exc.value)
        assert "needs a variable" in message
        assert "not a number" in message


def _flow(*, rules, default_label, edge_labels):
    """A minimal start → branch → end flow with the given edges out of the
    branch. Every edge lands on the same end node; only the labels matter."""
    nodes = [
        {
            "id": "start",
            "type": "startCall",
            "position": {"x": 0, "y": 0},
            "data": {"name": "Start", "prompt": "Greet the caller."},
        },
        {
            "id": "br",
            "type": "branch",
            "position": {"x": 200, "y": 0},
            "data": {"name": "Router", "rules": rules, "default_label": default_label},
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
            "id": "e-start",
            "source": "start",
            "target": "br",
            "data": {"label": "onward", "condition": "always"},
        }
    ]
    for index, label in enumerate(edge_labels):
        edges.append(
            {
                "id": f"e-{index}",
                "source": "br",
                "target": "end",
                "data": {"label": label, "condition": "deterministic"},
            }
        )
    return ReactFlowDTO.model_validate({"nodes": nodes, "edges": edges})


def _errors(exc) -> str:
    return " ".join(str(e) for e in exc.value.args[0])


class TestTheLabelsMustMatchTheCanvas:
    """The failure only visible by comparing the two halves of the node.

    A rule whose label matches no edge never routes anywhere, and nothing at
    runtime can tell you that — the call just takes the default. A *default*
    with no edge is worse: the call arrives with nowhere to go.
    """

    def test_a_flow_whose_labels_all_resolve_is_accepted(self):
        graph = WorkflowGraph(
            _flow(
                rules=[_rule("high", "amount", "greater_than", "50000")],
                default_label="standard",
                edge_labels=["high", "standard"],
            )
        )
        assert graph.nodes["br"].node_type == "branch"

    def test_a_rule_pointing_at_a_missing_edge_is_refused(self):
        with pytest.raises(ValueError) as exc:
            WorkflowGraph(
                _flow(
                    rules=[_rule("hgih", "amount", "greater_than", "50000")],
                    default_label="standard",
                    edge_labels=["high", "standard"],
                )
            )
        message = _errors(exc)
        assert "hgih" in message
        assert "high, standard" in message

    def test_a_default_pointing_at_a_missing_edge_is_refused(self):
        with pytest.raises(ValueError) as exc:
            WorkflowGraph(
                _flow(
                    rules=[_rule("high", "amount", "greater_than", "50000")],
                    default_label="fallback",
                    edge_labels=["high", "standard"],
                )
            )
        message = _errors(exc)
        assert "fallback" in message
        assert "somewhere to go" in message

    def test_labels_resolve_case_insensitively(self):
        """The canvas says 'High value' and the rule says 'high value'. Failing
        that would be pedantry, not safety."""
        WorkflowGraph(
            _flow(
                rules=[_rule("High Value", "amount", "greater_than", "50000")],
                default_label="Standard",
                edge_labels=["high value", "standard"],
            )
        )

    def test_a_branch_needs_two_differently_labelled_ways_out(self):
        """A branch with one label is not a branch — it is a pass-through with
        extra steps and a misleading name."""
        with pytest.raises(ValueError) as exc:
            WorkflowGraph(_flow(rules=[], default_label="only", edge_labels=["only"]))
        assert "at least two differently-labelled" in _errors(exc)

    def test_two_labels_into_one_node_is_an_ordinary_branch(self):
        """The reason this is not the generic `min_outgoing` constraint. Out-
        degree in this graph counts distinct target *nodes* — `Node.out` is a
        dict keyed by target id — so 'high value' and 'repeat caller' both
        routing to the same escalation node reads as one outgoing edge. It is
        two ways out, and it must validate."""
        WorkflowGraph(
            _flow(
                rules=[
                    _rule("high value", "amount", "greater_than", "50000"),
                    _rule("repeat caller", "calls", "greater_or_equal", "3"),
                ],
                default_label="standard",
                edge_labels=["high value", "repeat caller", "standard"],
            )
        )
