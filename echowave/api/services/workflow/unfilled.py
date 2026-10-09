"""An agent does not go live still saying ``{{clinic_name}}``.

Heard on a real call on 20 September 2026. A caller rang a clinic front desk
and was greeted with "Namaste, . How may I help you today?" — the template's
``{{clinic_name}}`` had never been answered, so it rendered as nothing. All
three Clinic front desk agents were in that state, live, with four unanswered
variables each: the clinic's name, address, doctors and opening hours.

Everything needed to prevent it already existed. ``AgentTemplate`` calls
these "values the operator must supply **before going live**". ``assemble``
knows which placeholders are the operator's and which the pipeline fills from
the contact or campaign row. ``fill_placeholders`` writes the answers in and
says in its own docstring that "anything unanswered" is left as it is. The
only missing piece was somebody checking before the agent answered a phone.

**What counts.** A bare name in braces, ``{{clinic_name}}``, minus the
runtime set the pipeline fills per call. The renderer's other syntax is not
touched: ``{{initial_context.phone_number}}`` and
``{{name | fallback:Unknown}}`` do not match the pattern at all, because both
are filled from the call and neither is anybody's to answer beforehand.

**Where it bites.** At publish, and in the editor's own validate call, both
of which run ``publish_gate.validate_definition``. Not on every keystroke: a
half-written prompt with a placeholder in it is a normal thing to be holding
at save time, and refusing it would make the editor unusable.

This does not repair agents published before it existed. They keep running
until someone republishes them, at which point this stops them.
"""

from __future__ import annotations

from typing import Any

from api.services.agent_builder.assemble import PLACEHOLDER, RUNTIME_VARIABLES
from api.services.workflow.errors import ItemKind, WorkflowError

#: How many names to list before the message stops naming them. A node with
#: eleven unanswered variables has a message nobody finishes reading, and the
#: editor highlights the node anyway.
MAX_NAMED = 4


def names_in(value: Any) -> set[str]:
    """Every operator placeholder in a string, or anywhere inside a dict or
    list. Walks the whole structure for the same reason ``fill_placeholders``
    does: prompts, greetings and transition speech all hold text, and this
    module should not have to know where in a node they live."""
    if isinstance(value, str):
        return set(PLACEHOLDER.findall(value)) - set(RUNTIME_VARIABLES)
    if isinstance(value, dict):
        return (
            set().union(*(names_in(item) for item in value.values()))
            if value
            else set()
        )
    if isinstance(value, list):
        return set().union(*(names_in(item) for item in value)) if value else set()
    return set()


def _message(missing: list[str]) -> str:
    shown = ", ".join(missing[:MAX_NAMED])
    rest = len(missing) - MAX_NAMED
    if rest > 0:
        shown = f"{shown} and {rest} more"
    return (
        f"This still has unanswered placeholders: {shown}. A caller would hear "
        "the gap where the value should be. Fill them in, or take them out of "
        "the prompt."
    )


def problems(definition: dict[str, Any] | None) -> list[WorkflowError]:
    """One error per node that still carries an unanswered placeholder.

    Empty when there is nothing to say, which is the common case and the one
    worth being fast: a workflow with no placeholders walks its own nodes once
    and finds nothing.
    """
    nodes = (definition or {}).get("nodes")
    if not isinstance(nodes, list):
        return []

    out: list[WorkflowError] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        missing = sorted(names_in(node.get("data")))
        if missing:
            out.append(
                WorkflowError(
                    kind=ItemKind.node,
                    id=str(node.get("id") or ""),
                    field="data.prompt",
                    message=_message(missing),
                )
            )
    return out
