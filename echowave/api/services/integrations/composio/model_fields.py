"""Which of a tool's arguments the model is asked to fill.

Composio publishes every argument an action takes. ``GMAIL_SEND_EMAIL``
declares around twenty: recipient, subject, body, and then cc, bcc, thread
id, label ids, attachment paths, whether the body is HTML, and so on. Until
now the model was handed all of them (up to ``MAX_PARAMETERS``, twenty-four),
which costs two things at once -- a large schema in every prompt that carries
the tool, and a model deciding among twenty fields when a person said "email
Ravi the quote".

n8n solves this in ``create-node-as-tool.ts`` and it is worth stating plainly,
because it is the opposite of what we were doing. There, a node becomes a tool
whose schema is **not the vendor's argument list**. It is only the fields the
operator explicitly left for the model to fill, marked ``$fromAI(...)``:

    traverseNodeParameters(node.parameters, collectedArguments);
    return z.object(schemaObj).required();

Everything else is pinned by the person who set the node up. So "send an
email" reaches the model as three fields, not twenty, and the model cannot
get the other seventeen wrong because it was never shown them.

We have no canvas and nobody to do that pinning, so the choice is made here,
once, when the tool row is created -- and written onto the row as its declared
parameters, which is a field our tool schema already has and already prefers
over the vendor's. What the model is not offered, Composio fills with its own
defaults, exactly as it did when the model omitted the field.

**Required fields are never dropped.** The cap is a preference about how much
to offer; a call missing an argument the vendor requires fails every time. So
if an action requires more fields than the cap, it gets all of them and the
cap does not apply. A schema that is one field too long is a worse outcome
than a tool that cannot work at all.

**The rest are chosen by name, not by position.** The vendor's order is
alphabetical -- the same fact that gave a real account seven ways to delete
mail -- so taking "the first few optional ones" would be taking whichever sort
first, which is how ``bcc`` beats ``body``. Instead a small list of the words
that actually appear in what people ask a receptionist to do is matched
against the field names.

**A schema with fields never yields a tool with none.** The two rules above
choose; they may both decline. Calendly's ``GET_EVENT_TYPE`` publishes one
property, ``uuid``, required nowhere in the schema and required by the API on
every call -- nothing to keep, and no word in it anybody says out loud. The
tool was created declaring no arguments, which reads as "takes none" rather
than "we did not choose any", and the model called it bare. So when the
choosing picks nobody, the schema's own fields are offered in its own order.

Never raises: a schema that cannot be read yields no fields, and the tool is
created exactly as it was before this module existed.
"""

from __future__ import annotations

from typing import Any, Optional

#: How many arguments the model is offered for one tool.
#:
#: Six because the jobs this product is for -- send a message, book a slot,
#: look someone up, raise a ticket -- are all four or five fields, and the
#: seventh field of any action is reliably an option nobody asked for.
#: Required fields are exempt (see the module docstring).
MODEL_FILLS = 6

#: How much of a field's description the model is given.
MAX_DESCRIPTION = 200

#: Optional fields worth offering, in the order they are offered.
#:
#: Matched against the field name, so ``to``, ``recipient_email`` and
#: ``to_address`` all answer to ``to``. Deliberately short: this is a list of
#: what a person dictates out loud, not a list of what an API supports.
_WANTED = (
    "to",
    "recipient",
    "subject",
    "body",
    "message",
    "text",
    "content",
    "query",
    "search",
    "name",
    "title",
    "email",
    "phone",
    "number",
    "start",
    "end",
    "date",
    "time",
    "duration",
    "description",
    "note",
    "amount",
    "status",
    "assignee",
    "priority",
    "label",
)

#: JSON Schema types to the five a Decibyl tool parameter may declare.
_TYPES = {
    "string": "string",
    "number": "number",
    "integer": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}


def _type_of(spec: Any) -> str:
    """One field's type, as a tool parameter may declare it.

    Unknown or absent becomes ``string``: a field the model fills as text is
    a field it can still fill, where a field with no type at all is a row
    that will not validate.
    """
    if not isinstance(spec, dict):
        return "string"
    raw = spec.get("type")
    if isinstance(raw, list):  # {"type": ["string", "null"]}
        raw = next((t for t in raw if t != "null"), None)
    if not isinstance(raw, str):
        return "string"
    return _TYPES.get(raw.strip().lower(), "string")


def _words(name: str) -> str:
    """A field name as an instruction, for a vendor that gave no description."""
    return name.replace("_", " ").strip() or name


def _describe(name: str, spec: Any) -> str:
    text = ""
    if isinstance(spec, dict):
        raw = spec.get("description")
        if isinstance(raw, str):
            text = raw.strip()
    return (text or _words(name))[:MAX_DESCRIPTION]


def _rank(name: str) -> Optional[tuple[int, int]]:
    """How well this optional field answers to a word people say.

    ``(how well, which word)``, lower being better, or ``None`` for a field
    that matches nothing. Sorting on the pair puts a field that *is* the word
    ahead of one that merely contains it.

    That distinction is not decorative. Matching on substring alone, a Gmail
    schema offers ``extra_recipients`` ahead of ``subject``, because
    "recipient" is early in the word list and happens to appear inside it --
    so the model is handed a field almost nobody uses and loses one it always
    needs. Measured on the real GMAIL_SEND_EMAIL schema before this was
    written, which is how it was found.

    The name is read as words, not as a string: ``recipient_email`` is
    ``recipient`` + ``email``, and a token that *is* a wanted word beats one
    that only begins or ends with it, which beats a bare substring.
    """
    lowered = name.strip().lower()
    tokens = [t for t in lowered.split("_") if t]
    best: Optional[tuple[int, int]] = None
    for index, word in enumerate(_WANTED):
        if word in tokens:
            quality = 0
        elif any(t.startswith(word) or t.endswith(word) for t in tokens):
            quality = 1
        elif word in lowered:
            quality = 2
        else:
            continue
        if best is None or (quality, index) < best:
            best = (quality, index)
    return best


def chosen(schema: Any, *, limit: int = MODEL_FILLS) -> list[dict[str, Any]]:
    """The arguments the model fills, from the vendor's whole list.

    Returns entries shaped for ``ToolParameter``. An empty list means the
    schema said nothing usable -- the caller creates the tool without declared
    parameters, which is what every connected tool did before this.
    """
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return []
    required_names = [
        name
        for name in (schema.get("required") or [])
        if isinstance(name, str) and name in properties
    ]

    picked: list[str] = list(required_names)

    room = max(0, limit - len(picked))
    if room:
        optional = [
            (rank, name)
            for name in properties
            if name not in required_names and (rank := _rank(name)) is not None
        ]
        # `rank` is already (how well, which word); the name breaks a tie so
        # the choice does not depend on the order a dict happened to arrive in.
        optional.sort(key=lambda pair: (pair[0], pair[1]))
        picked.extend(name for _, name in optional[:room])

    if not picked:
        # Nothing was required and nothing answered to a word we know. That
        # is not the same as a tool that takes no arguments, and it was being
        # published as one: CALENDLY_GET_EVENT_TYPE declares a single `uuid`,
        # marks it required nowhere, and fails every call without it. The
        # tool went out declaring nothing, and a live account got "Missing
        # required param: uuid" from a model that had never been shown the
        # field we were holding.
        #
        # Adding "uuid" to the word list fixes that tool and not the next
        # one. So the rule carries a floor instead: a schema with fields
        # never yields a tool with none. Schema order, because with no word
        # to rank by there is nothing better, and a vendor's own order beats
        # a sort we invented.
        picked = list(properties)[:limit]

    return [
        {
            "name": name,
            "type": _type_of(properties.get(name)),
            "description": _describe(name, properties.get(name)),
            "required": name in required_names,
        }
        for name in picked
    ]
