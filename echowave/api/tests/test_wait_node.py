"""The wait node: holding the line without taking a turn.

A wait node is the branch node's slower sibling — same pass-through shape, same
"no LLM completion, no question asked", but it makes a noise and it takes time.
Both of those are where it can go wrong.

**Silence is the failure mode.** A caller who hears nothing for three seconds
does not think "the system is working"; they think the call dropped, and they
either talk over the agent when it returns or hang up before it does. The filler
is the feature.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from api.services.workflow.dto import ReactFlowDTO, WaitNodeData
from api.services.workflow.pipecat_engine import (
    MAX_TOTAL_WAIT_SECONDS,
    PipecatEngine,
)
from api.services.workflow.workflow_graph import WorkflowGraph


class TestSavingAWaitNode:
    def test_a_plain_pause_validates(self):
        assert WaitNodeData(name="Beat", duration_seconds=1.5).filler_type == "none"

    def test_saying_something_needs_something_to_say(self):
        """Choosing 'say something' and leaving it blank produces silence the
        author did not intend — the one outcome this node must not create by
        accident."""
        with pytest.raises(ValidationError) as exc:
            WaitNodeData(name="Hold", filler_type="text")
        assert "no text" in str(exc.value)

    def test_playing_a_recording_needs_a_recording(self):
        with pytest.raises(ValidationError) as exc:
            WaitNodeData(name="Hold", filler_type="audio")
        assert "none is chosen" in str(exc.value)

    def test_the_pause_is_bounded(self):
        """Thirty seconds of hold music is already a caller reaching for the
        red button."""
        with pytest.raises(ValidationError):
            WaitNodeData(name="Forever", duration_seconds=120)
        with pytest.raises(ValidationError):
            WaitNodeData(name="Nothing", duration_seconds=0)


def _node(node_id, node_type, **data):
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"name": node_id, **data},
    }


def _edge(source, target, label):
    return {
        "id": f"{source}->{target}",
        "source": source,
        "target": target,
        "data": {"label": label, "condition": "onward"},
    }


@pytest.fixture(autouse=True)
def no_real_sleeping():
    """The pauses are real seconds by design; a test suite should not spend
    them. Patching the sleep keeps every assertion about *what* happens rather
    than how long it took."""
    with patch("api.services.workflow.pipecat_engine.asyncio.sleep", new=AsyncMock()):
        yield


def _engine(nodes, edges, variables=None):
    graph = WorkflowGraph(ReactFlowDTO.model_validate({"nodes": nodes, "edges": edges}))
    engine = PipecatEngine(workflow=graph, call_context_vars=dict(variables or {}))
    engine._setup_llm_context = AsyncMock()
    engine.end_call_with_reason = AsyncMock()
    engine.task = MagicMock()
    engine.task.queue_frame = AsyncMock()
    return engine


def _pause_flow(**wait_data):
    nodes = [
        _node("start", "startCall", prompt="Greet."),
        _node("hold", "wait", **wait_data),
        _node("after", "agentNode", prompt="Carry on."),
    ]
    edges = [_edge("start", "hold", "onward"), _edge("hold", "after", "onward")]
    return _engine(nodes, edges)


@pytest.mark.asyncio
class TestHoldingTheLine:
    async def test_it_continues_to_the_next_node(self):
        engine = _pause_flow(duration_seconds=0.1)
        await engine.set_node("hold")
        assert engine._current_node.id == "after"

    async def test_it_never_opens_an_llm_context_for_itself(self):
        engine = _pause_flow(duration_seconds=0.1)
        await engine.set_node("hold")
        assert engine._setup_llm_context.await_count == 1
        assert engine._setup_llm_context.await_args.args[0].id == "after"

    async def test_the_filler_is_spoken(self):
        engine = _pause_flow(
            duration_seconds=0.1,
            filler_type="text",
            filler_text="One moment.",
        )
        await engine.set_node("hold")
        spoken = engine.task.queue_frame.await_args.args[0]
        assert spoken.text == "One moment."

    async def test_the_filler_resolves_template_variables(self):
        """It is spoken to a person who has a name."""
        nodes = [
            _node("start", "startCall", prompt="Greet."),
            _node(
                "hold",
                "wait",
                duration_seconds=0.1,
                filler_type="text",
                filler_text="One moment {{first_name}}.",
            ),
            _node("after", "agentNode", prompt="Carry on."),
        ]
        edges = [_edge("start", "hold", "onward"), _edge("hold", "after", "onward")]
        engine = _engine(nodes, edges, {"first_name": "Lakshmi"})

        await engine.set_node("hold")

        assert engine.task.queue_frame.await_args.args[0].text == "One moment Lakshmi."

    async def test_a_silent_wait_says_nothing(self):
        engine = _pause_flow(duration_seconds=0.1, filler_type="none")
        await engine.set_node("hold")
        engine.task.queue_frame.assert_not_awaited()


@pytest.mark.asyncio
class TestTheThingsThatWouldStrandACaller:
    async def test_a_wait_loop_ends_the_call_rather_than_holding_forever(self):
        """A wait cycle cannot spin the event loop the way a branch cycle can —
        it sleeps. That makes it worse, not better: the caller sits on a silent
        line for as long as the loop runs, slowly enough to look deliberate."""
        nodes = [
            _node("start", "startCall", prompt="Greet."),
            _node("a", "wait", duration_seconds=30),
            _node("b", "wait", duration_seconds=30),
        ]
        edges = [
            _edge("start", "a", "onward"),
            {
                "id": "a->b",
                "source": "a",
                "target": "b",
                "data": {"label": "onward", "condition": "onward"},
            },
            {
                "id": "b->a",
                "source": "b",
                "target": "a",
                "data": {"label": "back", "condition": "back"},
            },
        ]
        engine = _engine(nodes, edges)
        # Start just under the budget so the test does not actually sleep it.
        engine._waited_seconds = MAX_TOTAL_WAIT_SECONDS - 1

        await engine.set_node("a")

        engine.end_call_with_reason.assert_awaited_once()

    async def test_a_wait_with_no_way_out_ends_the_call(self):
        nodes = [
            _node("start", "startCall", prompt="Greet."),
            _node("hold", "wait", duration_seconds=0.1),
            _node("after", "agentNode", prompt="Carry on."),
        ]
        edges = [_edge("start", "hold", "onward"), _edge("hold", "after", "onward")]
        engine = _engine(nodes, edges)
        engine.workflow.nodes["hold"].out_edges.clear()

        await engine.set_node("hold")

        engine.end_call_with_reason.assert_awaited_once()

    async def test_an_over_long_duration_from_an_old_workflow_is_clamped(self):
        """The field caps at 30, but a row saved before that bound existed would
        otherwise hold the line for whatever it says."""
        engine = _pause_flow(duration_seconds=0.1)
        engine.workflow.nodes["hold"].data.duration_seconds = 9999

        await engine.set_node("hold")

        # Clamped to MAX_WAIT_SECONDS rather than obeyed — and the budget it
        # consumed proves it was not 9999.
        assert engine._waited_seconds <= MAX_TOTAL_WAIT_SECONDS


class TestGraphShape:
    def test_a_wait_needs_exactly_one_way_out(self):
        """Two edges would be a branch without any rules to choose between
        them, which is a coin toss dressed as a flow."""
        nodes = [
            _node("start", "startCall", prompt="Greet."),
            _node("hold", "wait", duration_seconds=1),
            _node("a", "agentNode", prompt="A."),
            _node("b", "agentNode", prompt="B."),
        ]
        edges = [
            _edge("start", "hold", "onward"),
            _edge("hold", "a", "one"),
            _edge("hold", "b", "two"),
        ]
        with pytest.raises(ValueError) as exc:
            WorkflowGraph(ReactFlowDTO.model_validate({"nodes": nodes, "edges": edges}))
        assert "at most 1 outgoing" in " ".join(str(e) for e in exc.value.args[0])
