"""The call-review node every new agent is created with.

QA has been built and working for a long time: ``qa/analysis.py`` scores a
finished call, splits it per node, tags what happened, and writes the result to
``workflow_runs.annotations``, which the call detail page renders. It ran on
almost nothing, because ``run_integrations`` looks for a node of type ``qa``
and **no creation path made one**. An account built an agent, took a hundred
calls, opened Analysis and found it empty — not because review was hard, but
because nothing had asked for it.

So every path that creates an agent now creates this node with it: the wizard's
generator, the launch templates, and the six agent templates. One builder
rather than three copies, because three copies of a default is how two of them
end up stale.

**No edges, and that is deliberate.** ``run_integrations`` collects qa nodes by
type after the call has ended; it never walks to one during a call. An edge
would imply the conversation passes through it, which it does not, and would
make the node a step the caller can end up stuck at.

The defaults are the model's: ``qa_enabled`` true, ``qa_use_workflow_llm``
true, so review runs on whatever brain the agent already pays for rather than
introducing a second model to configure and a second bill to explain.

**It is not free.** ``run_per_node_qa_analysis`` runs one inference per step the
conversation reached, plus a rolling summary between them, so a three-step call
is roughly five completions on top of the call itself. ``qa_min_call_duration``
(15s) keeps wrong numbers and hangups out of that, and ``qa_sample_rate`` is
there for anyone at a volume where every call is too many. This decides what a
*new* agent starts as, not what anyone is stuck with — which is what the switch
on the Analysis tab is for.
"""

from __future__ import annotations

from typing import Any

#: The node id. Fixed rather than generated: a creation path emits exactly one
#: of these, and a stable id makes a materialised agent diffable against the
#: template it came from.
QA_NODE_ID = "qa-1"

#: What the runtime looks for -- see ``run_integrations``, which filters nodes
#: on this exact string.
QA_NODE_TYPE = "qa"


def qa_node(*, x: int = -340, y: int = 520) -> dict[str, Any]:
    """A review node, positioned clear of the conversation.

    Placed under the persona rather than in the conversation column, because on
    the canvas it is not a step — it is something that happens afterwards, and
    dropping it in the flow's path is how it gets read as one.
    """
    return {
        "id": QA_NODE_ID,
        "type": QA_NODE_TYPE,
        "position": {"x": x, "y": y},
        "data": {
            # What shows on the canvas and in call logs. "Call review" rather
            # than "QA": the person reading a log is looking for what happened
            # to their call, not for a department.
            "name": "Call review",
            "qa_enabled": True,
        },
    }


def has_qa_node(nodes: list[dict[str, Any]]) -> bool:
    """Does this graph already review its calls?

    Used by the creation paths so a template that ships its own review node
    keeps it rather than getting a second one — two nodes would run the
    analysis twice and bill for both.
    """
    return any(node.get("type") == QA_NODE_TYPE for node in nodes)
