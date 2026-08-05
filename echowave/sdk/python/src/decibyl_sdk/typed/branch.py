"""GENERATED — do not edit by hand.

Regenerate with `python -m decibyl_sdk.codegen` against the target
Decibyl backend. Source of truth: the backend's model-backed node-spec
catalog served from `/api/v1/node-types`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional

from decibyl_sdk.typed._base import TypedNode


@dataclass(kw_only=True)
class Branch_RulesRow:
    """
    Evaluated top to bottom. The first rule that matches decides the branch;
    drag to reorder.
    """

    label: str
    """
    Label of the outgoing edge to take when this rule matches. Must match
    one of this node's edge labels exactly.
    """
    variable: str
    """
    Name of the variable to test — from extraction, the trigger payload, a
    campaign column, or a pre-call fetch.
    """
    operator: str = 'equals'
    """
    How the variable is compared to the value.
    """
    value: Optional[str] = None
    """
    What to compare against. Leave blank for is_empty, is_not_empty, is_true
    and is_false. For 'is one of', a comma-separated list.
    """

@dataclass(kw_only=True)
class Branch(TypedNode):
    """
    Route on a variable — same input, same path, every time.  LLM hint:
    Deterministic routing. Every other transition in a workflow is decided
    by the LLM from the edge's natural-language condition; a branch node is
    decided by evaluating a rule, with no model involved.  Use it whenever
    the decision is a fact rather than a judgement: a threshold on an
    amount, a language the caller already chose, an attempt count, a flag
    from a pre-call fetch. Keep LLM-decided edges for judgements about what
    the caller meant.  Rules are evaluated top to bottom and the first match
    wins, so order bands from most specific down. Every rule's `label` must
    match an outgoing edge label, and `default_label` must match one too —
    that edge is taken when nothing matches, so a call can never be stranded
    here.  The node never speaks. It routes on entry, and the caller hears
    nothing between the previous node and the next one.
    """

    type: ClassVar[str] = 'branch'

    name: str = 'Branch'
    """
    Short identifier shown in the canvas and call logs.
    """

    rules: list[Branch_RulesRow] = field(default_factory=list)
    """
    Evaluated top to bottom. The first rule that matches decides the branch;
    drag to reorder.
    """

    default_label: str = 'default'
    """
    Edge taken when no rule matches. Required — every call that reaches this
    node must have somewhere to go.
    """

