"""What a transition tool asks the model to hand over as it moves.

A node that collects something has always collected it afterwards: the
extraction pass is a second LLM call, fired when the transition happens, that
re-reads the conversation and writes what it finds into the gathered context.
It works, and it costs a whole model round trip to ask a question the model had
just answered for itself.

So the same variables are offered as arguments on the transition itself. The
value arrives in the same call as the decision to move, shaped by the
provider's own schema rather than by a second model reading a transcript, and
it is there the instant the node changes instead of whenever the background
task lands.

**Never required.** A model made to supply a value nobody gave it will invent
one, and an invented invoice number is worse than a missing one -- a missing
one still falls through to the extraction pass, which is exactly the behaviour
every agent has today.
"""

from typing import Any

# The three types a variable can be declared as, mapped to what JSON schema
# calls them. Anything unrecognised is a string: the model can always write a
# number into one, and a wrong `type` should not make the whole tool invalid.
_JSON_TYPES = {"string": "string", "number": "number", "boolean": "boolean"}

# A transition's schema is in front of the model on every turn it considers
# moving, and a node's variables are repeated on each of its outgoing edges.
# Nodes on this platform collect at most three things; the cap is here so one
# unusual node cannot bloat every edge leaving it. The remainder is not lost --
# it falls to the extraction pass.
MAX_ARGUMENTS = 12


def _declared_type(variable) -> str:
    raw = getattr(variable, "type", None)
    # VariableType is a str Enum, so str() on it yields "VariableType.string".
    return _JSON_TYPES.get(str(getattr(raw, "value", raw)), "string")


def argument_properties(node) -> dict[str, Any]:
    """The arguments every transition out of this node should offer.

    Empty unless the node collects something, which keeps the schema of an
    ordinary conversational node exactly as it is today.
    """
    if node is None or not getattr(node, "extraction_enabled", False):
        return {}

    properties: dict[str, Any] = {}
    for variable in (getattr(node, "extraction_variables", None) or [])[:MAX_ARGUMENTS]:
        name = getattr(variable, "name", None)
        if not name or name in properties:
            continue
        hint = getattr(variable, "prompt", None)
        properties[name] = {
            "type": _declared_type(variable),
            "description": hint or f"{name}, if the caller has given it. Omit if not.",
        }
    return properties


def supplied_values(properties: dict[str, Any], arguments: Any) -> dict[str, Any]:
    """What the model actually filled in, ignoring blanks and anything unasked.

    A model that has nothing for an optional argument may omit it, send null,
    or send an empty string. All three mean the same thing and none of them
    should overwrite a value the call already has.
    """
    if not properties or not isinstance(arguments, dict):
        return {}

    values: dict[str, Any] = {}
    for name in properties:
        if name not in arguments:
            continue
        value = arguments[name]
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        values[name] = value
    return values
