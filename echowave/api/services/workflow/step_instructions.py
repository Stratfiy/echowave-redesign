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


#: Applies to every node, including the last one and including nodes that do
#: hold tools. Run 313's false promise came from a node with none, but the
#: same sentence is hand-written into "Confirm and book" -- "Never say an
#: appointment is booked unless the tool confirmed it" -- which is the proof
#: that operators need this and the proof they should not have to write it.
#:
#: Not a setting. An operator may reasonably choose whether their agent can
#: hang up or follow a caller's language; "may it tell the caller something
#: happened that did not happen" is not a choice a product should offer.
ACTION_HONESTY = (
    "Never tell the caller something has been done unless a tool has just "
    "done it and reported success. A booking, a cancellation, a payment, a "
    "message, a transfer: none of them are real until the tool that performs "
    "them says so. If you have not called it, speak about what you will do, "
    "not what you have done. If a tool failed or returned nothing, say so "
    "plainly and offer the next step -- never cover it with a reassurance."
)


def action_honesty_instructions() -> str:
    """The one rule every node gets, whatever it is and whatever it holds."""
    return ACTION_HONESTY


#: The other half of the same rule, and the one a live demo found. The
#: honesty rule above stops an agent claiming an *action* it did not take.
#: It says nothing about a *fact* it was never given, and a model that has
#: been asked to collect a name and a number will cheerfully supply both.
#:
#: Run 316, in front of a customer: the caller typed "RCT" and "Monday 11
#: AM". The agent replied "So the name Nithish Kalyan and mobile 1234567890
#: -- correct?". Neither was ever said, and 1234567890 is a placeholder the
#: model pattern-completed. A confirmation of invented details is worse than
#: a question, because it invites a distracted "yes".
#:
#: Not a setting, for the same reason the action rule is not one.
FACT_HONESTY = (
    "Never say a name, phone number, date, time, amount or reference the "
    "caller has not given you in this conversation, or that a tool has not "
    "returned. If you need one and do not have it, ask for it plainly. Never "
    "offer an example, a placeholder or a likely-looking value for the "
    "caller to confirm: a made-up detail read back as a question is how a "
    "wrong number reaches a real booking."
)


def fact_honesty_instructions() -> str:
    """The second rule every node gets: do not invent the details either."""
    return FACT_HONESTY
