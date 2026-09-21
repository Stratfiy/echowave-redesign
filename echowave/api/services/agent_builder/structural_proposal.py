"""Bounded in-memory graph splices for editor proposals, without persistence."""

from __future__ import annotations

import copy
import math
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class StructuralProposalError(ValueError):
    """A safe, actionable refusal that can be shown without leaking payloads."""


class InsertAgentOnEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    op: Literal["insert_agent_on_edge"]
    edge_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=12000)


class RemoveAgentAndReconnect(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    op: Literal["remove_agent_and_reconnect"]
    node_id: str = Field(min_length=1, max_length=200)


class RetargetEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    op: Literal["retarget_edge"]
    edge_id: str = Field(min_length=1, max_length=200)
    target_node_id: str = Field(min_length=1, max_length=200)


StructuralOperation = InsertAgentOnEdge | RemoveAgentAndReconnect | RetargetEdge
Graph = dict[str, Any]
Change = dict[str, str | None]


def _fresh_id(prefix, existing):
    for _ in range(8):
        candidate = f"{prefix}-{uuid4().hex}"
        if candidate not in existing:
            return candidate
    raise StructuralProposalError("Could not allocate a unique graph ID. Try again.")


def _ordinary_edge(edge, nodes):
    if nodes[edge["source"]]["type"] not in {"startCall", "agentNode"} or nodes[
        edge["target"]
    ]["type"] not in {"agentNode", "endCall"}:
        raise StructuralProposalError(
            "This connection involves a special node; edit it in Graph."
        )


def _point(node):
    position = node.get("position") or {}
    if not isinstance(position, dict):
        raise StructuralProposalError("Node positions must be coordinate objects.")
    x, y = position.get("x", 0), position.get("y", 0)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (x, y)):
        raise StructuralProposalError("Node positions must be finite numbers.")
    return x, y


def _description(edge, nodes):
    def name(node_id):
        return nodes[node_id]["data"].get("name") or node_id

    data = edge.get("data") or {}
    return f"{name(edge['source'])} → {name(edge['target'])} ({data.get('label', '')}: {data.get('condition', '')})"


def _reachable(graph, start):
    outgoing = {}
    for edge in graph["edges"]:
        outgoing.setdefault(edge["source"], set()).add(edge["target"])
    seen, queue = set(), [start]
    while queue:
        node = queue.pop()
        if node not in seen:
            seen.add(node)
            queue.extend(outgoing.get(node, ()))
    return seen


def _validate_changed_topology(before, after, changed_edges):
    """Reject new disconnections/cycles without blocking existing draft gaps."""
    before_ids = {n["id"] for n in before["nodes"]}
    after_ids = {n["id"] for n in after["nodes"]}
    for node in before["nodes"]:
        if node["type"] != "startCall":
            continue
        lost = (_reachable(before, node["id"]) & after_ids) - _reachable(
            after, node["id"]
        )
        if lost:
            raise StructuralProposalError(
                "The change would disconnect existing steps from the start."
            )
    before_incoming = {edge["target"] for edge in before["edges"]}
    after_incoming = {edge["target"] for edge in after["edges"]}
    for node in after["nodes"]:
        if (
            node["type"] in {"agentNode", "endCall"}
            and (node["id"] in before_incoming or node["id"] not in before_ids)
            and node["id"] not in after_incoming
        ):
            raise StructuralProposalError(
                "The change would leave a conversation step without an incoming connection."
            )
    for edge in after["edges"]:
        if edge["id"] not in changed_edges:
            continue
        if edge["source"] in _reachable(after, edge["target"]):
            raise StructuralProposalError(
                "The new connection would create a loop; edit loops explicitly in Graph."
            )


def apply_structural_operations(
    graph: Graph, operations: list[StructuralOperation]
) -> tuple[Graph, list[Change]]:
    """Return a new graph and human-readable changes, or reject the whole batch."""
    from api.services.workflow.dto import RFEdgeDTO, RFNodeDTO
    from api.services.workflow.node_specs import get_spec

    result = copy.deepcopy(graph)
    changes, changed_edges = [], set()
    for operation in operations:
        nodes = {node["id"]: node for node in result["nodes"]}
        edges = {edge["id"]: edge for edge in result["edges"]}
        if isinstance(operation, InsertAgentOnEdge):
            if not operation.name.strip() or not operation.prompt.strip():
                raise StructuralProposalError(
                    "A new step needs a name and instructions."
                )
            edge = edges.get(operation.edge_id)
            if edge is None:
                raise StructuralProposalError(
                    "The connection to insert on no longer exists."
                )
            _ordinary_edge(edge, nodes)
            RFEdgeDTO.model_validate(edge)
            original_description = _description(edge, nodes)
            spec = get_spec("agentNode")
            if spec is None:
                raise StructuralProposalError("The agent step type is unavailable.")
            data = {
                prop.name: copy.deepcopy(prop.default)
                for prop in spec.properties
                if prop.default is not None
            }
            data.update(name=operation.name, prompt=operation.prompt)
            node_id = _fresh_id("chat-node", set(nodes))
            source_x, source_y = _point(nodes[edge["source"]])
            target_x, target_y = _point(nodes[edge["target"]])
            node = {
                "id": node_id,
                "type": "agentNode",
                "data": data,
                "position": {
                    "x": (source_x + target_x) / 2
                    if source_x != target_x
                    else source_x + 240,
                    "y": (source_y + target_y) / 2,
                },
            }
            RFNodeDTO.model_validate(node)
            completion = {
                "id": _fresh_id("chat-edge", set(edges)),
                "source": node_id,
                "target": edge["target"],
                "data": {
                    "label": "Continue",
                    "condition": "When this step's instructions are complete",
                },
            }
            RFEdgeDTO.model_validate(completion)
            result["nodes"].append(node)
            nodes[node_id] = node
            edge["target"] = node_id
            # A target handle belongs to the old target; the newly created
            # standard node uses its standard input. Preserve source handle.
            old_target_handle = edge.pop("targetHandle", None)
            if old_target_handle is not None:
                completion["targetHandle"] = old_target_handle
            result["edges"].append(completion)
            changed_edges.update((edge["id"], completion["id"]))
            changes.extend(
                [
                    {
                        "node_id": node_id,
                        "field": "node_added",
                        "before": None,
                        "after": f"{operation.name}: {operation.prompt}",
                    },
                    {
                        "node_id": node_id,
                        "field": "edge_retargeted",
                        "before": original_description,
                        "after": _description(edge, nodes),
                    },
                    {
                        "node_id": node_id,
                        "field": "edge_added",
                        "before": None,
                        "after": _description(completion, nodes),
                    },
                ]
            )
        elif isinstance(operation, RemoveAgentAndReconnect):
            node = nodes.get(operation.node_id)
            if node is None or node["type"] != "agentNode":
                raise StructuralProposalError(
                    "Only ordinary conversation steps can be removed here."
                )
            capability_fields = (
                "tool_uuids",
                "document_uuids",
                "mcp_tool_filters",
                "extraction_enabled",
                "extraction_variables",
                "extraction_prompt",
            )
            if any(node["data"].get(field) for field in capability_fields):
                raise StructuralProposalError(
                    "This step has tools, knowledge or extraction configured; remove it explicitly in Graph."
                )
            incoming = [e for e in result["edges"] if e["target"] == operation.node_id]
            outgoing = [e for e in result["edges"] if e["source"] == operation.node_id]
            if len(incoming) != 1 or len(outgoing) != 1:
                raise StructuralProposalError(
                    "Removing a branching step requires explicit Graph editing."
                )
            first, last = incoming[0], outgoing[0]
            _ordinary_edge(first, nodes)
            _ordinary_edge(last, nodes)
            if first is last or first["source"] == last["target"]:
                raise StructuralProposalError("Removing this step would create a loop.")
            RFEdgeDTO.model_validate(first)
            RFEdgeDTO.model_validate(last)
            visual_edge_fields = {
                "id",
                "source",
                "target",
                "data",
                "sourceHandle",
                "targetHandle",
                "type",
                "style",
                "animated",
                "selected",
                "selectable",
                "deletable",
                "hidden",
                "focusable",
                "ariaLabel",
                "zIndex",
                "interactionWidth",
                "markerStart",
                "markerEnd",
                "label",
                "labelStyle",
                "labelShowBg",
                "labelBgStyle",
                "labelBgPadding",
                "labelBgBorderRadius",
                "reconnectable",
            }
            if set(last) - visual_edge_fields:
                raise StructuralProposalError(
                    "The outgoing connection has custom metadata; remove this step in Graph."
                )
            # Only the routing label/condition may be consumed with the removed
            # step. Unknown data and transition speech must never vanish silently.
            if any(
                v is not None and v != ""
                for k, v in last["data"].items()
                if k not in {"label", "condition"}
            ):
                raise StructuralProposalError(
                    "The outgoing connection has transition metadata; remove this step in Graph."
                )
            before_description = _description(first, nodes)
            first["target"] = last["target"]
            first.pop("targetHandle", None)
            if "targetHandle" in last:
                first["targetHandle"] = last["targetHandle"]
            result["edges"] = [e for e in result["edges"] if e["id"] != last["id"]]
            result["nodes"] = [
                n for n in result["nodes"] if n["id"] != operation.node_id
            ]
            changed_edges.add(first["id"])
            changes.extend(
                [
                    {
                        "node_id": node["id"],
                        "field": "node_removed",
                        "before": f"{node['data'].get('name') or node['id']}: {node['data'].get('prompt', '')}",
                        "after": None,
                    },
                    {
                        "node_id": first["source"],
                        "field": "edge_retargeted",
                        "before": before_description,
                        "after": _description(first, nodes),
                    },
                    {
                        "node_id": node["id"],
                        "field": "edge_removed",
                        "before": _description(last, nodes),
                        "after": None,
                    },
                ]
            )
        else:
            edge = edges.get(operation.edge_id)
            target = nodes.get(operation.target_node_id)
            if edge is None or target is None:
                raise StructuralProposalError(
                    "The connection or target no longer exists."
                )
            _ordinary_edge(edge, nodes)
            if (
                target["type"] not in {"agentNode", "endCall"}
                or edge["source"] == target["id"]
            ):
                raise StructuralProposalError(
                    "Choose an ordinary conversation step or end as the target."
                )
            if edge["target"] == target["id"]:
                raise StructuralProposalError(
                    "This connection already has that target."
                )
            RFEdgeDTO.model_validate(edge)
            previous = _description(edge, nodes)
            if edge.get("targetHandle"):
                raise StructuralProposalError(
                    "This connection uses a custom target handle; reconnect it in Graph."
                )
            edge["target"] = target["id"]
            changed_edges.add(edge["id"])
            changes.append(
                {
                    "node_id": edge["source"],
                    "field": "edge_retargeted",
                    "before": previous,
                    "after": _description(edge, nodes),
                }
            )
    _validate_changed_topology(graph, result, changed_edges)
    return result, changes
