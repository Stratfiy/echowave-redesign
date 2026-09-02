"""What a person has to fill in before an agent can take a call.

The whole product claim is that a non-technical seller sets an agent up in ten
minutes without opening a canvas. That claim has to hold for templates nobody
has written yet, so the fields are *derived* from the template rather than
listed alongside it — a hand-kept list is one someone forgets to update, and
the symptom is an agent that greets callers with the literal text
"{{business_name}}".

Two kinds of placeholder look identical in a prompt and must not be asked for
the same way:

- **Setup** — one answer per business, given once. The clinic's name, its
  hours, what it charges.
- **Per call** — a different answer on every call, and it arrives from the
  contact row or the trigger payload. The patient's name is not something the
  clinic types in during setup; asking for it would be nonsense.

There is no way to tell them apart by looking at the prompt, so the per-call
set is named here explicitly and everything else is treated as setup. That
direction is deliberate: a new placeholder that nobody classifies shows up as
one extra question during setup, which is visible and harmless. The other
default would silently leave it unfilled at call time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")

#: Filled at call time from the contact row or the trigger payload, never by a
#: person during setup.
PER_CALL_VARIABLES = frozenset(
    {
        "patient_name",
        "customer_name",
        "contact_name",
        "first_name",
        "appointment_time",
        "amount",
        "order_id",
    }
)


@dataclass(frozen=True)
class SetupField:
    name: str
    label: str
    #: Shown under the input. Says what a good answer looks like, because the
    #: person filling this in has never seen the prompt that uses it.
    hint: str
    required: bool = True


#: Wording for the fields we know about. A placeholder with no entry still
#: becomes a field — labelled from its own name — so an unknown template is
#: set up awkwardly rather than not at all.
KNOWN_FIELDS: dict[str, SetupField] = {
    "business_name": SetupField(
        "business_name",
        "Business name",
        "Said out loud in the greeting, so write it the way you say it — "
        "'Sharma Dental', not 'Sharma Dental Care Pvt Ltd'.",
    ),
    "opening_hours": SetupField(
        "opening_hours",
        "Opening hours",
        "Plain words, e.g. 'Monday to Saturday, 10am to 7pm. Closed Sunday.'",
    ),
    "services": SetupField(
        "services",
        "What you offer",
        "A short list the agent can read back, e.g. 'cleaning, fillings, "
        "root canal, braces'.",
    ),
    "fees": SetupField(
        "fees",
        "Fees",
        "Only what you are happy to say on a call, e.g. 'consultation ₹500'. "
        "Leave blank if you would rather the agent did not quote prices.",
        required=False,
    ),
    "address": SetupField(
        "address",
        "Address",
        "How you would give it to someone on the phone, plus a landmark.",
        required=False,
    ),
    "transfer_number": SetupField(
        "transfer_number",
        "Transfer to",
        "The number the agent rings when a caller needs a person. Leave blank "
        "and it will take a message instead.",
        required=False,
    ),
}


def placeholders_in(definition: dict[str, Any]) -> set[str]:
    """Every ``{{variable}}`` in a workflow definition's prompts and greetings."""
    found: set[str] = set()
    for node in definition.get("nodes", []):
        data = node.get("data") or {}
        for key in ("prompt", "greeting"):
            value = data.get(key)
            if isinstance(value, str):
                found.update(_PLACEHOLDER.findall(value))
    return found


def setup_fields_for(definition: dict[str, Any]) -> list[SetupField]:
    """The questions to ask before this agent can take a call.

    Required fields first, then the optional ones, each group alphabetical.
    Ordering is not cosmetic here: the person filling this in stops when the
    form stops looking mandatory, so anything that must be answered has to come
    before anything that need not be.
    """
    names = placeholders_in(definition) - PER_CALL_VARIABLES
    fields = [
        KNOWN_FIELDS.get(name)
        or SetupField(name, name.replace("_", " ").capitalize(), "")
        for name in names
    ]
    return sorted(fields, key=lambda f: (not f.required, f.name))


def missing_required(
    definition: dict[str, Any], values: dict[str, Any] | None
) -> list[str]:
    """Required fields with no answer yet.

    Blank counts as missing. A field left as an empty string reaches the prompt
    as nothing at all, and the agent then greets callers with a sentence that
    has a hole in it — which is worse than refusing to go live.
    """
    provided = values or {}
    return [
        field.name
        for field in setup_fields_for(definition)
        if field.required and not str(provided.get(field.name, "")).strip()
    ]
