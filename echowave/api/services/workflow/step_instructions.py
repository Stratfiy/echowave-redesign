"""Telling the model what this step can and cannot finish.

The model is handed the date, the operator's rules, the node's own prompt, and
a list of function schemas. Nothing in any of that says a step is a step. So
when a conversation *feels* complete, nothing contradicts the model, and it
does the human thing: it says goodbye.

Run 313 is what that costs. The caller agreed an 11:00 slot, and the agent --
sitting in "Find a slot", which holds no tools -- promised a reminder message
and wished him a good day. The booking tool lives on "Confirm and book", which
the call never reached. Nothing errored. The caller rang off believing he had
an appointment, and the clinic's diary had never heard of him.

The same shape has now appeared three times: an agent that said it would end a
call and could not, an agent that asked for a lorry registration from a step
that could not collect one, and this. In each the model asserted something the
step it was standing in had no power to do.

So the step says so itself. It is generated rather than asked of operators,
because an operator writing "do not say goodbye here" into all nine of their
prompts is doing the platform's job, and the one they forget is the one that
takes the booking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from api.services.workflow.workflow_graph import Node

#: Kept short on purpose. It is prepended to every turn of every node, so it is
#: paid for on every request, and a long block here pushes the operator's own
#: words further from the model's attention.
_HEADER = "This step is one step of a longer call, and it cannot end the call."

_MOVING_ON = (
    "When this step has what it asks for, call the matching function below. "
    "Saying what you are about to do is not doing it: the tool that does it "
    "belongs to a later step, and a promise made here is never kept."
)

_NO_FAREWELL = "Do not say goodbye or wish the caller well from this step."

_MAY_HANG_UP = (
    "The one exception is end_call, for a caller who has gone or should not be "
    "kept on the line."
)


def moving_on_instructions(
    node: "Node", *, agent_can_end_call: bool = False
) -> str | None:
    """What to tell the model about leaving this step, or None if nothing.

    Nothing for an end node, which is where a call is supposed to finish, and
    nothing for a node with no way out -- telling a model to call one of no
    functions is worse than saying nothing, and a dead end is a graph problem
    the validator should be catching rather than something a prompt can fix.
    """
    if node is None or getattr(node, "is_end", False):
        return None
    if not getattr(node, "out_edges", None):
        return None

    lines = [_HEADER, _MOVING_ON, _NO_FAREWELL]
    if agent_can_end_call:
        # Without this the two instructions contradict each other, and the
        # agent that may hang up is exactly the one told most firmly not to.
        lines.append(_MAY_HANG_UP)
    return " ".join(lines)
