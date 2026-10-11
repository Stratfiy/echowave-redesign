"""Send the recent thread whole and the rest as a digest.

Behind ``history_cap``. A Decibyl turn used to carry as much of the thread as
the plan's chat memory allows (``chat_memory``): from 8,000 tokens on Free up,
filled from the newest message back, and resent on every tool round. A long
thread therefore paid, every turn, for messages from last week that the
question in hand does not touch.

What changes: the newest messages go whole, and everything older in the same
window becomes one short message of one line each. The plan's memory is still
what bounds how far back the digest reaches -- nothing a plan promises is
taken away; it is carried more briefly.

**Two cuts that would be wrong, and are not made.**

* *Ids and cards survive.* A line that holds an id the model may need later
  (an image id for "make option 2 bigger", a card and where it stands) is kept
  whole, not clipped to a line: a digest that drops the id makes the follow-up
  impossible, and nothing would say why.
* *The cut moves in steps.* If the digest were rebuilt from "everything but
  the last six", it would change on every message, and the cached prefix
  (``cache_v2``) would break on every message with it. The boundary moves
  :data:`STEP` messages at a time, so between moves the digest is
  byte-identical and the new messages only append.

The digest is built without a model call: a summary that cost a call to make
would spend the tokens it saves. It is the opening of each older message, and
says so.
"""

from __future__ import annotations

import re
from typing import Any

#: Messages kept whole at the end. The first tool round of a turn needs the
#: last exchange or two to resolve "her", "that one" and "send it".
KEEP = 6

#: The older boundary moves this many messages at a time (see above), so the
#: recent window is between KEEP and KEEP + STEP - 1 messages.
STEP = 4

#: One digest line.
LINE_CHARS = 140

#: A line that holds an id or a card, kept to this.
ID_LINE_CHARS = 400

#: The ordinary lines of the digest. The newest win when they do not fit.
DIGEST_CHARS = 1_600

#: Pinned lines kept whatever else is dropped; each at most ID_LINE_CHARS.
MAX_PINNED = 8

_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_CARD = re.compile(r"^\[(Proposed|Images made):")

HEADER = (
    "[Earlier in this thread, shortened to the opening of each message "
    "(newest last). Ask if you need the detail of one:]"
)


def _pinned(message: dict[str, Any]) -> bool:
    body = str(message.get("content") or "")
    return bool(_CARD.search(body) or _ID.search(body))


def _line(message: dict[str, Any]) -> str:
    who = "Person" if message.get("role") == "user" else "Decibyl"
    body = " ".join(str(message.get("content") or "").split())
    keep = ID_LINE_CHARS if _pinned(message) else LINE_CHARS
    if len(body) > keep:
        body = body[: keep - 1].rstrip() + "\u2026"
    return f"- {who}: {body}"


def digest(older: list[dict[str, Any]]) -> str:
    """One message's worth of the older turns, or empty when there are none.

    Two kinds of line. A *pinned* line holds an id or a card; the newest
    :data:`MAX_PINNED` of them always stay, whatever else is dropped, because a
    digest that loses the id loses the follow-up with no sign of why. The rest
    fill what is left of :data:`DIGEST_CHARS`, newest first.
    """
    if not older:
        return ""
    lines = [(_line(m), _pinned(m)) for m in older]
    pinned = [i for i, (_, p) in enumerate(lines) if p][-MAX_PINNED:]
    chosen = set(pinned)
    room = DIGEST_CHARS - len(HEADER)
    for i in range(len(lines) - 1, -1, -1):
        if i in chosen or lines[i][1]:
            continue
        cost = len(lines[i][0]) + 1
        if cost > room:
            break
        room -= cost
        chosen.add(i)
    kept = [lines[i][0] for i in sorted(chosen)]
    return HEADER + "\n" + "\n".join(kept)


def recent_count(total: int) -> int:
    """How many of ``total`` messages go whole: at least :data:`KEEP`, and
    enough more that the older part is a whole number of steps."""
    if total <= KEEP:
        return total
    return KEEP + ((total - KEEP) % STEP)


def cap(history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """``history`` with its older messages as one digest.

    Returns the new list and how many of its leading messages are stable from
    one request to the next (the digest and the recent messages that were
    already there), for ``Conversation.stable_prefix``: all of them, since the
    next turn's history starts with these same messages and appends.
    """
    keep = recent_count(len(history))
    older, recent = history[: len(history) - keep], history[len(history) - keep :]
    summary = digest(older)
    if not summary:
        return list(history), len(history)
    capped = [{"role": "user", "content": summary}, *recent]
    return capped, len(capped)


#: Attachments. What one file and one turn's files may show, when the cap is on
#: (``ATTACHMENT_CHARS`` / ``ATTACHMENTS_CHARS`` in decibyl.py are 24,000 and
#: 48,000). Chosen so the largest brief the product has been shown -- a vendor
#: spec of 12,134 characters -- is sent whole: clipping the tail of a spec
#: drops the closing steps, which is where escalation and disposition live, and
#: that was a bug once. A file past this is clipped *with the size said*.
ATTACHMENT_CHARS = 16_000
ATTACHMENTS_CHARS = 32_000


def clipped_note(shown: int, whole: int) -> str:
    return (
        f" … [showing the first {shown:,} of {whole:,} characters; the whole "
        "file is in the workspace Files -- search_files finds a passage in it]"
    )


__all__ = [
    "ATTACHMENTS_CHARS",
    "ATTACHMENT_CHARS",
    "DIGEST_CHARS",
    "KEEP",
    "MAX_PINNED",
    "STEP",
    "cap",
    "clipped_note",
    "digest",
    "recent_count",
]
