"""What happened on this call, as a label an operations team can sort by.

We already score calls (QA) and pull fields out of them (extraction). Neither
answers the question a manager actually opens the list with: *did it work?* A
transcript does not answer it at a hundred calls, and a numeric QA score
answers a different question — a polite, well-run call to somebody who was
never going to buy scores well and books nothing.

**Not the disposition codes we already have.** `enums`/`dispositionCodes.ts`
carry `user_hangup`, `voicemail_detected`, `call_duration_exceeded` — how the
call *ended*, mechanically. These are what it *achieved*, commercially. A call
is legitimately both `user_hangup` and `booked`: the customer got what they
wanted and put the phone down. Keeping them in one field would force a choice
between two facts that are both true, so they stay separate.

**Per workflow, because the outcomes are.** A clinic books appointments; a
lending agent gets a payment promise; an NDR agent confirms an address. The
default list below is a starting point that a business edits, not a taxonomy we
impose — which is also where Vapi and Bolna land, both of which let you define
the shape and neither of which ships a fixed set.

**Several labels per call.** "Booked, and asked to be called back about the
second treatment" is one call and two outcomes, and forcing one would lose the
half somebody follows up on.

``unclear`` is deliberate rather than blank. A blank cannot be told apart from
"not scored yet", and the difference matters when somebody is working a list.
"""

from __future__ import annotations

import re
from typing import Any

#: The fallback. Always available, never removable, and applied whenever the
#: model returns nothing usable — so "we could not tell" is a visible state
#: rather than an absence somebody has to interpret.
UNCLEAR = "unclear"

#: Shipped with a new agent, and editable per workflow thereafter.
DEFAULT_DISPOSITIONS: tuple[dict[str, str], ...] = (
    {
        "code": "booked",
        "label": "Booked",
        "when": "An appointment, demo or visit is confirmed",
    },
    {
        "code": "interested",
        "label": "Interested",
        "when": "Wants it, but committed to nothing yet",
    },
    {
        "code": "callback",
        "label": "Call back",
        "when": "Asked to be contacted at another time",
    },
    {"code": "not_interested", "label": "Not interested", "when": "A clear no"},
    {
        "code": "wrong_number",
        "label": "Wrong number",
        "when": "Not the person we were trying to reach",
    },
    {
        "code": "no_answer",
        "label": "No answer",
        "when": "Nobody picked up, or picked up and said nothing",
    },
    {
        "code": "unreachable",
        "label": "Unreachable",
        "when": "Invalid, switched off, or could not connect",
    },
    {
        "code": UNCLEAR,
        "label": "Unclear",
        "when": "The conversation did not settle either way",
    },
)

#: A code has to survive being a CRM field name, a URL parameter and a CSV
#: column heading, so it is deliberately narrower than a label.
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

#: More than this is not a taxonomy anybody sorts by, and each one costs prompt
#: tokens on every call.
MAX_DISPOSITIONS = 30


def normalise_code(raw: Any) -> str | None:
    """Coerce something typed by a person into a storable code, or reject it."""
    if not isinstance(raw, str):
        return None
    candidate = raw.strip().lower().replace(" ", "_").replace("-", "_")
    candidate = re.sub(r"[^a-z0-9_]", "", candidate)[:40]
    return candidate if candidate and _CODE.match(candidate) else None


def parse_taxonomy(raw: Any) -> list[dict[str, str]]:
    """The dispositions configured for a workflow, always including ``unclear``.

    Falls back to the default list when nothing is configured, so an agent that
    has never touched this still classifies rather than silently doing nothing.
    Tolerant of shape: this comes from a JSON column written by several
    versions of the client, and a malformed entry should cost that entry, not
    the classification.
    """
    entries: list[dict[str, str]] = []
    seen: set[str] = set()

    if isinstance(raw, dict):
        # A caller that handed over the whole configuration block rather than
        # the list inside it. Cheaper to accept than to make every call site
        # remember which.
        raw = raw.get("call_outcomes") or raw.get("dispositions")

    if isinstance(raw, list):
        for item in raw[:MAX_DISPOSITIONS]:
            if isinstance(item, str):
                item = {"code": item}
            if not isinstance(item, dict):
                continue
            code = normalise_code(item.get("code") or item.get("value"))
            if not code or code in seen:
                continue
            seen.add(code)
            entries.append(
                {
                    "code": code,
                    "label": str(item.get("label") or code.replace("_", " ").title())[
                        :60
                    ],
                    "when": str(item.get("when") or item.get("description") or "")[
                        :200
                    ],
                }
            )

    if not entries:
        return [dict(entry) for entry in DEFAULT_DISPOSITIONS]

    # `unclear` is not optional. Without it the model has nowhere to put a call
    # it genuinely cannot read, and will pick the nearest real outcome instead
    # — which is how a "not interested" that was actually a bad line ends up in
    # somebody's report.
    if UNCLEAR not in seen:
        entries.append(dict(DEFAULT_DISPOSITIONS[-1]))
    return entries


def allowed_codes(taxonomy: list[dict[str, str]]) -> set[str]:
    return {entry["code"] for entry in taxonomy}


def coerce_result(raw: Any, taxonomy: list[dict[str, str]]) -> list[str]:
    """Turn whatever the model answered into labels this workflow allows.

    Anything the taxonomy does not contain is dropped rather than stored: a
    hallucinated code is worse than no code, because it silently makes a filter
    under-count without ever appearing in the options list.

    Returns ``[unclear]`` when nothing survives, never an empty list.
    """
    allowed = allowed_codes(taxonomy)

    if isinstance(raw, dict):
        raw = (
            raw.get("dispositions")
            or raw.get("disposition")
            or raw.get("codes")
            or raw.get("outcome")
        )
    if isinstance(raw, str):
        raw = re.split(r"[,\n]", raw)
    if not isinstance(raw, list):
        return [UNCLEAR]

    out: list[str] = []
    for item in raw:
        code = normalise_code(
            item if isinstance(item, str) else (item or {}).get("code")
        )
        if code and code in allowed and code not in out:
            out.append(code)

    # `unclear` alongside a real outcome is a contradiction — the model both
    # could and could not tell. The real answer wins.
    if len(out) > 1 and UNCLEAR in out:
        out = [code for code in out if code != UNCLEAR]

    return out or [UNCLEAR]


def build_prompt(taxonomy: list[dict[str, str]]) -> str:
    """The instruction sent with the transcript.

    States the allowed codes and their meanings, because a model asked to
    classify without being shown the options invents plausible ones.
    """
    lines = [
        f"- {entry['code']}: {entry['when'] or entry['label']}" for entry in taxonomy
    ]
    return (
        "Read the call transcript and decide what the call achieved.\n\n"
        "Choose every code below that applies. A call can have more than one — "
        "somebody who books an appointment and also asks to be called about "
        f"something else is both. Use {UNCLEAR} only when the conversation "
        "genuinely did not settle either way, and never alongside another "
        "code.\n\n"
        "Codes:\n" + "\n".join(lines) + "\n\n"
        'Answer with JSON only: {"dispositions": ["code", ...]}'
    )


def merge_taxonomies(configured: list[Any]) -> list[dict[str, str]]:
    """One menu covering several agents' outcome lists.

    The calls list is organization-wide while a taxonomy is per agent, so a
    filter offering only one agent's codes would silently hide the rest. The
    defaults are always in, so an agent nobody configured still contributes
    the labels its calls are actually being given.

    First definition wins on a repeated code. Two agents can label the same
    code differently and there is no right answer between them; deciding
    deterministically at least stops the menu reshuffling between page loads.
    """
    merged: dict[str, dict[str, str]] = {
        entry["code"]: entry for entry in parse_taxonomy(None)
    }
    for raw in configured:
        if not raw:
            continue
        for entry in parse_taxonomy(raw):
            merged.setdefault(entry["code"], entry)
    return list(merged.values())
