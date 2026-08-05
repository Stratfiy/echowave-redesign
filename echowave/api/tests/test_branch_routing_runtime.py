"""What the engine does when a call reaches a branch node.

The rule engine is pinned in `test_branching.py` and the graph wiring in
`test_branch_node.py`. What is left is the part that only exists at runtime:
the branch must route **without taking a turn** — no LLM completion, no speech,
no function registered — and it must not be able to spin.

That "without taking a turn" property is what makes a chain of branches
dangerous in a way an agent node never is. Two agent nodes pointing at each
other loop at conversational speed and the caller can hang up. Two branch nodes
pointing at each other loop as fast as the event loop allows, with the caller
listening to silence.
"""

from unittest.mock import AsyncMock

import pytest

from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.pipecat_engine import BRANCH_HOP_LIMIT, PipecatEngine
from api.services.workflow.workflow_graph import WorkflowGraph


def _node(node_id, node_type, **data):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"name": node_id, **data},
    }


def _edge(source, target, label):
    return {
        "id": f"{source}->{target}:{label}",
        "source": source,
        "target": target,
        "data": {"label": label, "condition": "deterministic"},
    }


def _engine(nodes, edges, variables=None) -> PipecatEngine:
    graph = WorkflowGraph(ReactFlowDTO.model_validate({"nodes": nodes, "edges": edges}))
    engine = PipecatEngine(
        workflow=graph,
        call_context_vars=dict(variables or {}),
    )
    # The two things a branch must never do. Asserting on the mocks is how the
    # "routes without taking a turn" property is checked: a branch that reached
    # either of these would be occupying the call rather than passing through.
    engine._setup_llm_context = AsyncMock()
    engine.end_call_with_reason = AsyncMock()
    return engine


def _router_flow(rules, default_label="standard", variables=None):
    """start → router → (escalate | normal)."""
    nodes = [
        _node("start", "startCall", prompt="Greet."),
        _node("router", "branch", rules=rules, default_label=default_label),
        _node("escalate", "agentNode", prompt="Escalating."),
        _node("normal", "agentNode", prompt="Carrying on."),
    ]
    edges = [
        _edge("start", "router", "onward"),
        _edge("router", "escalate", "high value"),
        _edge("router", "normal", default_label),
    ]
    return _engine(nodes, edges, variables)


HIGH_VALUE = [
    {
        "label": "high value",
        "variable": "order_value",
        "operator": "greater_than",
        "value": "50000",
    }
]


@pytest.mark.asyncio
class TestRoutingWithoutTakingATurn:
    async def test_a_matching_rule_lands_on_its_edges_target(self):
        engine = _router_flow(HIGH_VALUE, variables={"order_value": 60000})
        await engine.set_node("router")
        assert engine._current_node.id == "escalate"

    async def test_no_match_lands_on_the_default(self):
        engine = _router_flow(HIGH_VALUE, variables={"order_value": 10000})
        await engine.set_node("router")
        assert engine._current_node.id == "normal"

    async def test_a_variable_extracted_during_the_call_beats_the_trigger_payload(self):
        """Extraction writes into gathered context, and it is the fresher fact:
        the caller just told the agent their order was larger than the CSV said.
        """
        engine = _router_flow(HIGH_VALUE, variables={"order_value": 10000})
        engine._gathered_context["order_value"] = 90000
        await engine.set_node("router")
        assert engine._current_node.id == "escalate"

    async def test_the_branch_never_opens_an_llm_context(self):
        """The property that makes routing deterministic. If a branch ever set
        up context it would be taking a turn, and the caller would hear a pause
        where there should be none."""
        engine = _router_flow(HIGH_VALUE, variables={"order_value": 60000})
        await engine.set_node("router")
        # Exactly once — for `escalate`, the node it routed to. Never for the
        # branch itself.
        assert engine._setup_llm_context.await_count == 1
        assert engine._setup_llm_context.await_args.args[0].id == "escalate"

    async def test_the_branch_appears_in_the_run_timeline(self):
        """It is a decision point, and a timeline that jumped straight from one
        conversational node to another would hide where the call turned."""
        engine = _router_flow(HIGH_VALUE, variables={"order_value": 60000})
        await engine.set_node("router")
        assert "router" in engine._gathered_context["nodes_visited"]

    async def test_the_decision_is_recorded_with_its_reason(self):
        """So "why did this call escalate" is answerable from the record rather
        than by re-running it."""
        engine = _router_flow(HIGH_VALUE, variables={"order_value": 60000})
        await engine.set_node("router")

        decisions = engine._gathered_context["branch_decisions"]
        assert len(decisions) == 1
        assert decisions[0]["label"] == "high value"
        assert decisions[0]["matched"] is True
        assert decisions[0]["rule_index"] == 0
        assert "order_value" in decisions[0]["reason"]

    async def test_an_unmatched_decision_is_recorded_too(self):
        engine = _router_flow(HIGH_VALUE, variables={"order_value": 10})
        await engine.set_node("router")
        decision = engine._gathered_context["branch_decisions"][0]
        assert decision["matched"] is False
        assert decision["rule_index"] is None


@pytest.mark.asyncio
class TestChainsAndCycles:
    async def test_branches_can_be_chained(self):
        """Two decisions between one pair of spoken turns is ordinary: route by
        language, then by value."""
        nodes = [
            _node("start", "startCall", prompt="Greet."),
            _node(
                "by_language",
                "branch",
                rules=[
                    {
                        "label": "telugu",
                        "variable": "language",
                        "operator": "equals",
                        "value": "te",
                    }
                ],
                default_label="other",
            ),
            _node(
                "by_value",
                "branch",
                rules=[
                    {
                        "label": "high",
                        "variable": "amount",
                        "operator": "greater_than",
                        "value": "1000",
                    }
                ],
                default_label="low",
            ),
            _node("te_high", "agentNode", prompt="Telugu, high value."),
            _node("te_low", "agentNode", prompt="Telugu, low value."),
            _node("other", "agentNode", prompt="Other language."),
        ]
        edges = [
            _edge("start", "by_language", "onward"),
            _edge("by_language", "by_value", "telugu"),
            _edge("by_language", "other", "other"),
            _edge("by_value", "te_high", "high"),
            _edge("by_value", "te_low", "low"),
        ]
        engine = _engine(nodes, edges, {"language": "te", "amount": 5000})

        await engine.set_node("by_language")

        assert engine._current_node.id == "te_high"
        assert len(engine._gathered_context["branch_decisions"]) == 2

    async def test_a_cycle_of_branches_ends_the_call_rather_than_spinning(self):
        """Cycles are legal in this graph — conversations legitimately loop back
        — so nothing upstream forbids two branches routing into each other. With
        no turn taken and no LLM call to slow it, that is a busy loop against a
        live caller."""
        nodes = [
            _node("start", "startCall", prompt="Greet."),
            _node("a", "branch", rules=[], default_label="to_b"),
            _node("b", "branch", rules=[], default_label="to_a"),
            _node("escape_a", "agentNode", prompt="Unreachable."),
            _node("escape_b", "agentNode", prompt="Unreachable."),
        ]
        edges = [
            _edge("start", "a", "onward"),
            _edge("a", "b", "to_b"),
            _edge("a", "escape_a", "other"),
            _edge("b", "a", "to_a"),
            _edge("b", "escape_b", "other"),
        ]
        engine = _engine(nodes, edges)

        await engine.set_node("a")

        engine.end_call_with_reason.assert_awaited_once()
        assert len(engine._gathered_context["branch_decisions"]) <= BRANCH_HOP_LIMIT

    async def test_the_budget_is_per_chain_not_per_call(self):
        """A flow with a branch every few turns is normal. Counting them across
        a whole call would end a legitimate twenty-minute conversation."""
        engine = _router_flow(HIGH_VALUE, variables={"order_value": 60000})

        for _ in range(BRANCH_HOP_LIMIT + 5):
            await engine.set_node("router")

        engine.end_call_with_reason.assert_not_awaited()
        assert engine._current_node.id == "escalate"


@pytest.mark.asyncio
class TestWorkflowsSavedBeforeValidationExisted:
    """Graph validation rejects a label with no edge, so these states are only
    reachable for a workflow persisted earlier. Keeping the call alive matters
    more than being right: an unintended branch is recoverable, a call that
    stops mid-sentence because of a typo is not."""

    async def test_a_label_matching_no_edge_falls_back_to_the_first(self):
        engine = _router_flow(HIGH_VALUE, variables={"order_value": 10})
        # Reach past validation the way a legacy row would.
        engine.workflow.nodes["router"].data.default_label = "typo"

        await engine.set_node("router")

        assert engine._current_node.id == "escalate"  # first edge out
        engine.end_call_with_reason.assert_not_awaited()
        assert (
            "matched no edge"
            in engine._gathered_context["branch_decisions"][0]["reason"]
        )

    async def test_a_branch_with_no_edges_at_all_ends_the_call(self):
        """There is genuinely nowhere to go, and silently stopping would leave
        the caller listening to nothing until they hang up."""
        nodes = [
            _node("start", "startCall", prompt="Greet."),
            _node("dead_end", "branch", rules=[], default_label="nowhere"),
            _node("unused_a", "agentNode", prompt="A."),
            _node("unused_b", "agentNode", prompt="B."),
        ]
        edges = [
            _edge("start", "dead_end", "onward"),
            _edge("dead_end", "unused_a", "nowhere"),
            _edge("dead_end", "unused_b", "other"),
        ]
        engine = _engine(nodes, edges)
        engine.workflow.nodes["dead_end"].out_edges.clear()

        await engine.set_node("dead_end")

        engine.end_call_with_reason.assert_awaited_once()
