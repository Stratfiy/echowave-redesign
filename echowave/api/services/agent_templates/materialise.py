"""Turning a catalogue template into a workflow somebody can open and run.

The six templates in ``catalogue.py`` are complete — nodes, edges, prompts,
guardrails, a recommended stack — and until now nothing could instantiate one.
A customer's only route to an agent was the wizard, which asks eleven questions
and then runs a language model to write the flow. That is the right door for
somebody describing a business we have no template for. It is the wrong door
for a dental clinic, when a dental clinic template already exists and is
better than anything generated on the spot.

This is the other door: pick a template, get the agent, edit it.

The shapes were designed to meet. ``TemplateNode`` names its fields after
``services/workflow/dto.py``, and the node builders in
``services/workflow/launch_templates.py`` already emit the ReactFlow structure
the editor and the engine read. So this converts rather than invents, and
reuses those builders instead of writing a second copy of the node shape that
would drift from the first.
"""

from __future__ import annotations

from typing import Any

from api.services.agent_templates._base import AgentTemplate
from api.services.workflow.launch_templates import (
    _agent,
    _edge,
    _end,
    _global_node,
    _start,
)

#: Vertical gap between stacked nodes, matching the launch templates so a
#: materialised agent opens looking like a hand-authored one rather than a
#: pile at the origin.
_ROW = 220


class TemplateShapeError(ValueError):
    """A template that would not open as a workflow."""


def _slug(index: int, node_type: str) -> str:
    """Stable ids, in the form the launch templates already use."""
    return {
        "startCall": "start-1",
        "globalNode": "global-1",
    }.get(node_type) or f"{'end' if node_type == 'endCall' else 'agent'}-{index}"


def to_workflow_definition(template: AgentTemplate) -> dict[str, Any]:
    """The template as a ``{"nodes": [...], "edges": [...]}`` definition.

    Edges in a template point at node *names*, because a template is written by
    a person and names are what a person can keep straight. Ids are assigned
    here and the edges are rewritten to match — a template that references a
    name with no node is a bug in the catalogue and is raised as one rather
    than producing a workflow with an edge into nothing.
    """
    ids: dict[str, str] = {}
    # The persona, which templates do not carry and every agent needs. Each
    # launch template opens with one; a materialised agent without it would run
    # with no shared voice or guardrail at all, and the difference would only
    # show up on a call.
    nodes: list[dict[str, Any]] = [_global_node()]
    agent_seen = 0
    end_seen = 0
    row = 0

    for node in template.nodes:
        if node.type == "globalNode":
            # Allowed but not expected: the persona above is already in place,
            # so a template that ships its own replaces it rather than adding a
            # second the engine would have to choose between.
            node_id = "global-1"
            nodes[0] = {**nodes[0], "data": {"name": node.name, "prompt": node.prompt}}
        elif node.type == "startCall":
            node_id = "start-1"
            nodes.append(_start(node.greeting or "", node.prompt))
            row += 1
        elif node.type in ("agentNode", "agent"):
            agent_seen += 1
            node_id = _slug(agent_seen, "agent")
            extraction = (
                [{"name": k, "description": v} for k, v in node.extract.items()]
                if node.extract
                else None
            )
            nodes.append(
                _agent(
                    node_id, node.name, node.prompt, row * _ROW, extraction=extraction
                )
            )
            row += 1
        elif node.type == "endCall":
            end_seen += 1
            node_id = _slug(end_seen, "endCall")
            nodes.append(_end(node_id, node.name, node.prompt, row * _ROW))
            row += 1
        else:
            raise TemplateShapeError(
                f"{template.id!r} has a node of type {node.type!r}, which is not "
                "one this editor can draw."
            )

        if node.name in ids:
            raise TemplateShapeError(
                f"{template.id!r} has two nodes called {node.name!r}; edges "
                "reference nodes by name, so the second would be unreachable."
            )
        ids[node.name] = node_id

    edges: list[dict[str, Any]] = []
    for edge in template.edges:
        for end, ref in (("source", edge.source), ("target", edge.target)):
            if ref not in ids:
                raise TemplateShapeError(
                    f"{template.id!r} has an edge whose {end} is {ref!r}, and no "
                    "node has that name."
                )
        edges.append(
            _edge(ids[edge.source], ids[edge.target], edge.label, edge.condition)
        )

    return {"nodes": nodes, "edges": edges}
