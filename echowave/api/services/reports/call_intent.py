"""What callers actually wanted, taken from the path their call took.

The account already has a disposition system for what a call *achieved*, and
it is worth saying plainly why this does not use it: across 198 calls in the
last seven days on the live account, every commercial disposition was empty.
What comes back is ``unknown`` and ``user_hangup`` -- how the call ended,
mechanically. A manager asking "how many bookings today" gets nothing.

``nodes_visited`` is recorded on every run and always has been, and on a
workflow it is the answer:

    Greet and understand -> Take the booking details -> Find a slot
    Greet and understand -> Reschedule or cancel
    Greet and understand -> Clinical question

The step the caller was taken to *is* what they rang about. It needs no
classifier, costs nothing per call, cannot hallucinate, and it is already
there for every call ever made -- so the view has history on the day it ships
rather than starting from zero.

Its honest limit: this is what the agent *understood*, not what the caller
meant. An agent that routes a booking to the wrong branch is counted wrong
here too. That is a real failure mode and the number to watch is the
unrouted one -- a call that never left the greeting is reported as such
rather than being quietly dropped, because a rising "never got there" is the
signal that the agent, not the report, is broken.
"""

from __future__ import annotations

from typing import Any, Iterable

#: Node types a call passes *through* on its way somewhere. A branch reads its
#: rules and hands straight on; a wait holds the line. Neither is a thing the
#: caller asked for, so neither can be the answer to "what did they want" --
#: and both are recorded in ``nodes_visited`` deliberately, so the routing is
#: visible in a run timeline.
PASSTHROUGH_NODE_TYPES = frozenset({"branch", "wait"})

#: What a call that never got past the greeting is reported as. Named rather
#: than dropped: a rising count here is the agent failing to understand
#: people, which is exactly what a silent filter would hide.
NO_INTENT = "Didn't get that far"


def passthrough_names(workflow_json: dict[str, Any] | None) -> set[str]:
    """Names of the nodes in this workflow that only route.

    ``nodes_visited`` stores names, not types, so the types have to be looked
    up against the definition the run was pinned to.
    """
    names: set[str] = set()
    for node in (workflow_json or {}).get("nodes") or []:
        if node.get("type") in PASSTHROUGH_NODE_TYPES:
            name = (node.get("data") or {}).get("name")
            if name:
                names.add(name)
    return names


def intent_of(
    nodes_visited: Iterable[str] | None, passthrough: set[str] | None = None
) -> str:
    """The step that says what this caller rang about.

    The first node after the greeting, skipping anything that only routes.
    """
    visited = [n for n in (nodes_visited or []) if isinstance(n, str)]
    if len(visited) < 2:
        # Index 0 is where every call starts. On its own it means the call
        # ended, or stalled, before the caller's reason was established.
        return NO_INTENT
    skip = passthrough or set()
    for name in visited[1:]:
        if name not in skip:
            return name
    return NO_INTENT


def summarise(
    runs: Iterable[tuple[Any, list[str] | None]],
    passthrough_by_workflow: dict[Any, set[str]] | None = None,
) -> list[dict[str, Any]]:
    """Count calls by intent, commonest first.

    ``runs`` is (workflow_id, nodes_visited) pairs. Counting happens here
    rather than in SQL because the intent is the *first qualifying element of
    a JSON array*, which no database expresses without being told each
    workflow's routing nodes -- and a day of calls is small.
    """
    lookup = passthrough_by_workflow or {}
    counts: dict[str, int] = {}
    for workflow_id, nodes in runs:
        intent = intent_of(nodes, lookup.get(workflow_id))
        counts[intent] = counts.get(intent, 0) + 1

    total = sum(counts.values())
    rows = [
        {
            "intent": intent,
            "calls": calls,
            # A share of nothing is not zero, it is unanswerable.
            "share": round(calls / total, 4) if total else None,
        }
        for intent, calls in counts.items()
    ]
    # Commonest first; ties by name so the order does not shuffle between
    # requests and make a dashboard look alive when nothing has changed.
    rows.sort(key=lambda r: (-r["calls"], r["intent"]))
    return rows
