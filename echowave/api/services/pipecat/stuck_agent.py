"""Notice when a call went round in circles instead of going anywhere.

A workflow moves between nodes by calling a tool, and a model that will not
call one does not fail -- it keeps talking. On the Kriti Labs agent that
looked like this: the caller gave a truck registration in pieces, and the
agent asked for it three times in a row from inside the greeting step, which
has no idea how to collect one. Nothing errored. The call recorded cleanly.
It was found by a person reading a transcript, which is not a monitoring
strategy.

The signal is cheap and already recorded: ``nodes_visited``. A call that ends
having visited exactly one node, on a workflow that has more than one, either
ended before it got going or never got going at all.

**Telling those two apart is the whole problem.** A caller who says "wrong
number" and hangs up visits one node and is fine. So the turn count decides,
and the threshold is measured rather than picked: across twenty-one runs on
five agents, every call whose agent was working had moved on by the caller's
third turn, and the two known-broken calls ran to eight and thirteen turns
without moving. Four is that boundary with a turn of headroom.

**What this deliberately does not do** is guess at a cause. A stuck call may
be a model that will not emit tool calls, a prompt that never gives it reason
to, or a graph whose only edge has an unsatisfiable condition. The tag says
where to look, not what is wrong.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

#: What a stuck call is tagged with.
TAG = "stuck_in_first_node"

#: Caller turns before a call that has not moved counts as stuck.
#:
#: Measured, not chosen: of twenty-one runs across five agents, every working
#: call had left its first node by the caller's third turn, and the two broken
#: ones reached eight and thirteen. Four flags both of those and none of the
#: short calls -- a caller who says one thing and hangs up, which is most of
#: the single-node runs on this account and is not a bug.
MIN_USER_TURNS = 4


def looks_stuck(
    *,
    nodes_visited: Sequence[Any] | None,
    workflow_node_count: int,
    user_turns: int,
) -> bool:
    """Did this call talk for a while without ever leaving its first step?

    Conservative on every axis. A one-node workflow cannot be stuck, a call
    that moved is not stuck, and a call too short to have needed a transition
    is not stuck either. The cost of a wrong "yes" is an operator chasing a
    call that was fine, which is how a signal gets ignored.
    """
    if workflow_node_count <= 1:
        return False
    if nodes_visited is None or len(nodes_visited) != 1:
        return False
    return user_turns >= MIN_USER_TURNS
