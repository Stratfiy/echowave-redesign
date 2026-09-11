"""What the caller has already told us, put in front of the model.

A node prompt is written as an instruction -- "collect the patient's name, a
mobile number, the treatment, and a preferred day and time" -- and an
instruction is unconditional. The model reads it on arrival at the node and
does what it says, even when the caller answered half of it two turns ago on
the way in.

That is not a wording problem and no rewrite of the operator's prompt fixes it
reliably. The model has the conversation in its history, but a direct
instruction outranks a memory of the transcript every time, and the result is
the thing a caller notices first and forgives least: being asked again for
something they have already said.

    Caller: next Thursday, eleven o'clock please.
    Agent:  ... and what day and time would suit you?

So the values gathered so far are stated, as facts, in the system prompt. The
model is not being asked to remember; it is being told.

**Placement is load-bearing.** This block changes the moment a value is
collected, so everything above it in the prompt would stop being cacheable if
it went early. It is appended after the instruction blocks, which are
byte-identical for the life of a call, so the expensive prefix survives and
only the tail re-tokenises.

**Only scalars.** The gathered context also carries the machinery of the run --
which nodes were visited, the disposition, the tag list, the nested copy of the
extracted variables. None of that was said by a caller, and flattening it
produces lines that read like facts without being any.
"""

from __future__ import annotations

#: Keys the engine keeps in the same dictionary for its own bookkeeping. None
#: of them is something a caller said, and a model told "nodes visited: 4" will
#: eventually mention it out loud.
INTERNAL_KEYS = frozenset(
    {
        "call_disposition",
        "call_tags",
        "extracted_variables",
        "nodes_visited",
    }
)

#: Past this many the block is no longer a reminder, it is a second prompt.
#: Nodes on this platform collect a handful of things; a call that has genuinely
#: established more than this has them in the transcript too.
MAX_VALUES = 15

#: A single value longer than this is a paragraph the extraction pass captured,
#: not an answer to a question. Stating it back in full crowds out the rest.
MAX_VALUE_CHARS = 200

HEADING = (
    "Already established in this call. These are facts, not guesses -- use "
    "them, and never ask the caller for any of them again:"
)


def known_values_block(gathered: dict | None) -> str | None:
    """The block to append, or None when nothing has been established yet.

    None rather than an empty heading: a block that says "already established"
    and then lists nothing invites the model to wonder what it has forgotten.
    """
    lines = []
    for key, value in _usable(gathered or {}):
        readable = str(key).replace("_", " ").strip()
        lines.append(f"- {readable}: {value}")
        if len(lines) >= MAX_VALUES:
            break
    if not lines:
        return None
    return "\n".join([HEADING, *lines])


def _usable(gathered: dict):
    """Scalar, non-empty, not bookkeeping, in a stable order.

    Sorted so the block is byte-identical whenever the same values are known,
    which keeps a re-entered node from re-tokenising for no reason.
    """
    for key in sorted(gathered):
        if key in INTERNAL_KEYS or str(key).startswith("_"):
            continue
        value = gathered[key]
        if isinstance(value, bool):
            yield key, "yes" if value else "no"
            continue
        if isinstance(value, (int, float)):
            yield key, value
            continue
        if not isinstance(value, str):
            # Dicts and lists are plumbing, not something a caller said.
            continue
        text = value.strip()
        if not text or len(text) > MAX_VALUE_CHARS:
            continue
        yield key, text
