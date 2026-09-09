"""One agent handing the call to another, mid-conversation.

A business does not have one job. The number on the website reaches somebody
who books appointments, chases a delivery, takes a payment and answers a
billing question, and today each of those is a branch of one enormous graph
that one person maintains and nobody else can reuse. Vapi ships this as
"squads" and Bolna as agent groups, and the reason both do is that the second
agent an account builds is usually 80% the first one.

So a ``handoff`` node names another agent, and at load time that agent's
conversation is **spliced into this one**. Everything downstream — transitions,
extraction, QA, disposition, the transcript — then works exactly as it did,
because by the time the runtime sees it there is one graph. That is the whole
design: splice rather than nest, so there is no second execution path to keep
in step with the first one.

**What is spliced, and what is dropped.**

* The member's *start* node goes. It carries a greeting, and the caller has
  already been greeted — being greeted twice is the clearest possible signal
  that they are talking to software.
* The member's *global* prompt is folded into each of its own nodes that asked
  for one, rather than becoming a second global. Two globals is not a thing a
  graph can have, and dropping it would silently change how the member behaves
  the moment it is reused.
* The member's *QA* node goes. The call is one call and the parent already
  reviews it; keeping both would score the same conversation twice and bill
  for it twice.
* The member's *end* nodes stay, and end the call.

**There is no way back.** A handoff is a transfer, not a function call: the
member's end nodes end the call, and a handoff node with an outgoing edge is
rejected rather than quietly ignored. Returning to the caller would mean
deciding what the first agent knows about what the second one did, which is a
real design question and not one to answer by accident.

**One brain per call.** The member's conversation design is reused; its model
is not. The LLM service is built once when the pipeline starts, and swapping it
mid-call is a different piece of work. Worth knowing before promising a squad
where the specialist runs on a bigger model than the receptionist.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, NamedTuple

#: The node type that names another agent.
HANDOFF = "handoff"

#: Node types in the member that are the parent's job, not the member's.
_DROPPED_MEMBER_TYPES = frozenset({"startCall", "globalNode", "qa"})

#: How deep a squad may go — an agent handing to an agent handing to an agent.
#: Past this it is not a squad, it is a call graph nobody can hold in their
#: head, and every level multiplies the prompt the model is carrying.
MAX_DEPTH = 3


#: The five ways a squad is malformed, worded once.
#:
#: Single-sourced because these are raised at load time *and* reported at
#: design time, and the two must not drift: an operator who fixes what the
#: canvas told them and then reads a different sentence in the log has been
#: given two problems where there was one.
NO_WAY_BACK = (
    "A handoff is a transfer, not a detour — the call continues with the other "
    "agent and does not come back. Remove the step after it."
)
NO_AGENT_CHOSEN = "A handoff step has no agent chosen."
CIRCULAR = (
    "These agents hand off to each other in a circle, so a call would never "
    "reach anybody."
)
MEMBER_MISSING = (
    "A handoff points at an agent that no longer exists, or belongs to another account."
)


def too_deep_message() -> str:
    return (
        f"Agents can hand off {MAX_DEPTH} deep. This chain goes further, which "
        "is a call nobody can follow and a prompt nobody can afford."
    )


class SquadProblem(NamedTuple):
    """One thing wrong with a squad, and where to point at it.

    ``node_id`` is the handoff the problem belongs to, so the canvas can mark
    the step rather than showing a banner about a graph the reader then has to
    search. ``code`` is for the UI to branch on; ``message`` is what a person
    reads, and is the same sentence the runtime would have raised.
    """

    node_id: str | None
    code: str
    message: str


class SquadError(ValueError):
    """A squad that cannot be assembled, with a reason worth showing somebody."""


def _node_id(prefix: str, original: str) -> str:
    """Namespaced so the same agent can appear twice in one squad.

    Without this, a squad using the same specialist from two places collides on
    every node id and the second copy silently overwrites the first.
    """
    return f"{prefix}__{original}"


def _handoff_nodes(workflow_json: Any) -> list[dict]:
    """Every handoff node, chosen agent or not.

    Deliberately not filtered on whether an agent was picked. A half-configured
    step is exactly the one that must not slip past — the runtime has never
    heard of a handoff node, so one that survives assembly is a dead end the
    caller walks into.
    """
    if not isinstance(workflow_json, dict):
        return []
    return [
        node
        for node in (workflow_json.get("nodes") or [])
        if isinstance(node, dict) and node.get("type") == HANDOFF
    ]


def has_handoffs(workflow_json: Any) -> bool:
    """Does this workflow need assembling at all?

    Counts unfinished steps too. Anything deciding whether to skip assembly
    has to ask this rather than whether there are agents to splice in — a
    handoff with nothing chosen answers no to the second question and is
    exactly the one that must not reach the runtime.
    """
    return bool(_handoff_nodes(workflow_json))


def handoff_targets(workflow_json: Any) -> list[str]:
    """The agents this workflow hands off to, in graph order.

    Only the ones actually chosen — this answers "what does this depend on",
    which an unfinished step does not contribute to. Assembly uses
    :func:`_handoff_nodes` instead, and refuses the unfinished one.
    """
    targets = []
    for node in _handoff_nodes(workflow_json):
        reference = (node.get("data") or {}).get("agent_uuid")
        if isinstance(reference, str) and reference.strip():
            targets.append(reference.strip())
    return targets


def _global_prompt(nodes: list[dict]) -> str:
    for node in nodes:
        if node.get("type") == "globalNode":
            return str((node.get("data") or {}).get("prompt") or "")
    return ""


def _entry_node_id(nodes: list[dict], edges: list[dict]) -> str | None:
    """What the member's start node leads to — the first thing it actually says.

    The start node itself is dropped, so this is where the caller arrives.
    """
    start_ids = {n["id"] for n in nodes if n.get("type") == "startCall"}
    if not start_ids:
        return None
    for edge in edges:
        if edge.get("source") in start_ids:
            target = edge.get("target")
            if isinstance(target, str):
                return target
    return None


def _splice_member(
    *,
    handoff_id: str,
    member_json: dict,
    parent_nodes: list[dict],
    parent_edges: list[dict],
) -> None:
    """Replace one handoff node, in place, with the member's conversation."""
    member_nodes = [n for n in (member_json.get("nodes") or []) if isinstance(n, dict)]
    member_edges = [e for e in (member_json.get("edges") or []) if isinstance(e, dict)]

    entry = _entry_node_id(member_nodes, member_edges)
    if entry is None:
        raise SquadError(
            "That agent has no starting point, so there is nothing to hand over to."
        )

    global_prompt = _global_prompt(member_nodes)
    kept: list[dict] = []
    kept_ids: set[str] = set()

    for node in member_nodes:
        if node.get("type") in _DROPPED_MEMBER_TYPES:
            continue
        copied = copy.deepcopy(node)
        copied["id"] = _node_id(handoff_id, node["id"])
        kept_ids.add(node["id"])

        # The member's own global, folded in where the member asked for it.
        # The parent's global is left applying too: it is where an account puts
        # the things true of every call — who we are, what we may not say —
        # and those do not stop being true because a second agent is speaking.
        data = copied.setdefault("data", {})
        if global_prompt and data.get("add_global_prompt", True):
            data["prompt"] = f"{global_prompt}\n\n{data.get('prompt') or ''}".strip()

        kept.append(copied)

    parent_nodes.extend(kept)

    for edge in member_edges:
        source, target = edge.get("source"), edge.get("target")
        if source not in kept_ids or target not in kept_ids:
            # An edge from the start node, or into a node that was dropped.
            continue
        copied = copy.deepcopy(edge)
        copied["source"] = _node_id(handoff_id, source)
        copied["target"] = _node_id(handoff_id, target)
        copied["id"] = _node_id(handoff_id, str(edge.get("id") or f"{source}-{target}"))
        parent_edges.append(copied)

    # Everything that pointed at the handoff now points at where the member
    # starts talking.
    entry_id = _node_id(handoff_id, entry)
    for edge in parent_edges:
        if edge.get("target") == handoff_id:
            edge["target"] = entry_id


def assemble(
    workflow_json: Any,
    *,
    load_member: Callable[[str], dict | None],
    _depth: int = 0,
    _seen: tuple[str, ...] = (),
) -> dict:
    """This workflow with every handoff replaced by the agent it names.

    ``load_member`` resolves an agent reference to its workflow JSON, and is
    the caller's job because it is the only part of this that touches a
    database — and because it is where the organization check belongs. A
    handoff naming an agent in another account must not resolve, and this
    module has no way to know that.

    Returns the workflow unchanged when there are no handoffs, so the common
    case costs one scan and no copying.
    """
    if not isinstance(workflow_json, dict):
        return workflow_json

    if not _handoff_nodes(workflow_json):
        return workflow_json

    if _depth >= MAX_DEPTH:
        raise SquadError(too_deep_message())

    spliced = copy.deepcopy(workflow_json)
    nodes: list[dict] = [n for n in spliced.get("nodes") or [] if isinstance(n, dict)]
    edges: list[dict] = [e for e in spliced.get("edges") or [] if isinstance(e, dict)]

    handoffs = [n for n in nodes if n.get("type") == HANDOFF]
    remaining = [n for n in nodes if n.get("type") != HANDOFF]

    for node in handoffs:
        handoff_id = node["id"]
        reference = str((node.get("data") or {}).get("agent_uuid") or "").strip()

        if any(edge.get("source") == handoff_id for edge in edges):
            raise SquadError(NO_WAY_BACK)

        if not reference:
            raise SquadError(NO_AGENT_CHOSEN)

        if reference in _seen:
            raise SquadError(CIRCULAR)

        member = load_member(reference)
        if member is None:
            raise SquadError(MEMBER_MISSING)

        # Resolved before splicing, so a member's own handoffs are already
        # flattened by the time its nodes are renamed.
        member = assemble(
            member,
            load_member=load_member,
            _depth=_depth + 1,
            _seen=(*_seen, reference),
        )
        _splice_member(
            handoff_id=handoff_id,
            member_json=member,
            parent_nodes=remaining,
            parent_edges=edges,
        )

    spliced["nodes"] = remaining
    spliced["edges"] = [
        edge
        for edge in edges
        if edge.get("source") not in {n["id"] for n in handoffs}
        and edge.get("target") not in {n["id"] for n in handoffs}
    ]
    return spliced


def validate(
    workflow_json: Any,
    *,
    load_member: Callable[[str], dict | None],
    _depth: int = 0,
    _seen: tuple[str, ...] = (),
) -> list[SquadProblem]:
    """Every way this squad is malformed, without assembling it.

    ``assemble`` raises on the first problem, which is right for a call — there
    is nothing useful to do with the second one when the first has already
    stopped the conversation. It is wrong for an editor. Every one of these
    five conditions is decidable while somebody is building the agent, and
    until this existed the only way to discover any of them was to place a call
    and have it fail.

    So: the same checks, in the same order, against the same sentences — but
    collecting instead of raising, and carrying the node each belongs to so the
    canvas can mark the step rather than describing the problem and leaving the
    reader to find it.

    Returns an empty list for a workflow with no handoffs, which is almost all
    of them.
    """
    if not isinstance(workflow_json, dict):
        return []

    handoffs = _handoff_nodes(workflow_json)
    if not handoffs:
        return []

    if _depth >= MAX_DEPTH:
        # Attributed to the first handoff at this level: the chain is too long
        # as a whole, and the step that would have extended it is the one a
        # person can act on.
        return [SquadProblem(handoffs[0].get("id"), "too_deep", too_deep_message())]

    edges = [e for e in workflow_json.get("edges") or [] if isinstance(e, dict)]
    problems: list[SquadProblem] = []

    for node in handoffs:
        handoff_id = node.get("id")
        reference = str((node.get("data") or {}).get("agent_uuid") or "").strip()

        if any(edge.get("source") == handoff_id for edge in edges):
            problems.append(SquadProblem(handoff_id, "no_way_back", NO_WAY_BACK))

        if not reference:
            # Nothing further to check on this node: every remaining question
            # is about the agent it names, and it names none.
            problems.append(
                SquadProblem(handoff_id, "no_agent_chosen", NO_AGENT_CHOSEN)
            )
            continue

        if reference in _seen:
            # Reported and not followed. Descending would loop, and the cycle
            # is the finding.
            problems.append(SquadProblem(handoff_id, "circular", CIRCULAR))
            continue

        member = load_member(reference)
        if member is None:
            problems.append(SquadProblem(handoff_id, "member_missing", MEMBER_MISSING))
            continue

        problems.extend(
            validate(
                member,
                load_member=load_member,
                _depth=_depth + 1,
                _seen=(*_seen, reference),
            )
        )

    return problems
