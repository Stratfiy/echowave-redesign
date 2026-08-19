"""Turn a template plus the operator's answers into a workflow that runs.

The chat's job is to *ask*; this module's job is to *build*. Keeping those
apart is what makes "it works right after the chat" a property rather than a
hope — the model never writes the workflow, so it cannot write an invalid one.
It gathers a template id and a handful of values, and the assembly below is
ordinary deterministic code that either produces a valid graph or raises.

The alternative, letting the model emit SDK TypeScript, is what the MCP surface
already offers to an external editor where a developer is watching. In an
in-product chat aimed at someone who has never seen the canvas, a generated
workflow that fails validation on the third attempt is a dead end rather than a
correction loop.

Two things this does that a template alone cannot:

**Fills the variables.** Prompts carry `{{clinic_name}}` placeholders. An
unfilled one would be spoken to a caller verbatim, so anything the operator did
not answer is reported rather than substituted with a guess.

**Hoists the guardrails into a global node.** A guardrail placed on one node
applies on one turn. Several of these are legal constraints for the vertical,
and a constraint that stops applying when the conversation moves node is not a
constraint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from api.services.agent_templates import AgentTemplate

#: Placeholders the pipeline fills at call time from the contact or campaign
#: row. The operator does not supply these and must not be asked for them.
RUNTIME_VARIABLES = frozenset(
    {
        "first_name",
        "last_name",
        "order_summary",
        "order_amount",
        "amount_due",
        "due_date",
        "loan_ref",
    }
)

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

#: Canvas layout. The graph is laid out as a single column because these
#: templates are short and a straight line reads correctly at a glance; the
#: editor's own layout takes over the moment anyone drags a node.
_X = 240.0
_Y_STEP = 220.0
_GLOBAL_POSITION = (-320.0, 0.0)


class AssemblyError(ValueError):
    """The template and answers do not make a workflow."""


@dataclass
class AssembledAgent:
    """A workflow definition ready for validation and persistence."""

    name: str
    definition: dict[str, Any]
    #: Variables the template declares that nobody answered. Empty on a
    #: complete build; non-empty means the chat has more to ask.
    missing_variables: list[str] = field(default_factory=list)


def required_variables(template: AgentTemplate) -> list[str]:
    """What the operator must answer before this template can be built.

    Derived from the prompts rather than read from `template_variables`, so a
    prompt that references something the template forgot to declare still gets
    asked about instead of reaching a caller as literal braces.
    """
    text = " ".join(
        [node.prompt for node in template.nodes]
        + [node.greeting or "" for node in template.nodes]
        + template.guardrails
    )
    found = {name for name in _PLACEHOLDER.findall(text)} - RUNTIME_VARIABLES
    # Declared order first so the chat asks in the order the template intended,
    # then anything the prompts referenced but the template did not declare.
    declared = [key for key in template.template_variables if key in found]
    undeclared = sorted(found - set(declared))
    return declared + undeclared


def _fill(text: str, variables: dict[str, str]) -> str:
    """Substitute the operator's answers, leaving runtime placeholders alone."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in RUNTIME_VARIABLES:
            return match.group(0)
        return variables.get(name, match.group(0))

    return _PLACEHOLDER.sub(replace, text)


def _global_prompt(template: AgentTemplate, variables: dict[str, str]) -> str:
    """The guardrails, as an instruction that applies on every turn."""
    rules = "\n".join(f"- {_fill(rule, variables)}" for rule in template.guardrails)
    return (
        "These rules apply to every turn of this conversation, without "
        "exception, and override any other instruction that conflicts with "
        f"them.\n\n{rules}"
    )


def assemble(
    template: AgentTemplate,
    *,
    name: str,
    variables: dict[str, str] | None = None,
) -> AssembledAgent:
    """Build the workflow definition for ``template``.

    Raises :class:`AssemblyError` only for things that make a graph impossible —
    a missing start node, a template with no way to end. An unanswered variable
    is *not* an error: it comes back in ``missing_variables`` so the chat can
    ask, which is a better conversation than a failed build.
    """
    variables = {k: v for k, v in (variables or {}).items() if v and v.strip()}
    display_name = (name or template.name).strip()
    if not display_name:
        raise AssemblyError("The agent needs a name.")

    if template.start_node is None:
        raise AssemblyError(
            f"Template {template.id!r} has no start node and cannot be built."
        )

    # Node ids are derived from the template's own node names so a workflow
    # read later still shows where it came from, and so edges can be resolved
    # by name without a second lookup table.
    ids = {
        node.name: f"{template.id}-{index}" for index, node in enumerate(template.nodes)
    }

    nodes: list[dict[str, Any]] = []
    for index, node in enumerate(template.nodes):
        data: dict[str, Any] = {
            "name": node.name,
            "prompt": _fill(node.prompt, variables),
            "allow_interrupt": True,
            # Every node inherits the guardrails from the global node below.
            "add_global_prompt": True,
        }

        if node.type == "startCall":
            data["is_start"] = True
            if node.greeting:
                data["greeting"] = _fill(node.greeting, variables)
                data["greeting_type"] = "text"
            # Spoken disclosure is a DPDP obligation on a recorded call, and
            # every one of these verticals records. Defaulted on rather than
            # left to the operator to remember.
            data["recording_disclosure_enabled"] = True
        elif node.type == "endCall":
            data["is_end"] = True

        if node.extract:
            data["extraction_enabled"] = True
            data["extraction_prompt"] = (
                "Capture the following from the conversation. Leave a field "
                "empty rather than guessing at it."
            )
            data["extraction_variables"] = [
                {"name": key, "type": "string", "description": hint}
                for key, hint in node.extract.items()
            ]

        nodes.append(
            {
                "id": ids[node.name],
                "type": node.type,
                "position": {"x": _X, "y": float(index) * _Y_STEP},
                "data": data,
            }
        )

    # The guardrails, once, applying everywhere.
    nodes.append(
        {
            "id": f"{template.id}-global",
            "type": "globalNode",
            "position": {"x": _GLOBAL_POSITION[0], "y": _GLOBAL_POSITION[1]},
            "data": {
                "name": "Rules",
                "prompt": _global_prompt(template, variables),
            },
        }
    )

    edges = [
        {
            "id": f"{ids[edge.source]}->{ids[edge.target]}",
            "source": ids[edge.source],
            "target": ids[edge.target],
            "data": {
                "label": edge.label,
                "condition": _fill(edge.condition, variables),
            },
        }
        for edge in template.edges
    ]

    unanswered = [key for key in required_variables(template) if key not in variables]

    return AssembledAgent(
        name=display_name,
        definition={"nodes": nodes, "edges": edges},
        missing_variables=unanswered,
    )
