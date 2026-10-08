"""Follow-up chips for a helper's reply: the next step under the answer.

AGENTS.md: "The chat carries its own next steps ... the thread is where they
matter most." The thread's chips were the home screen's cards whatever had
just been said, so a report never offered "Go deeper on point 2". These are
built from the reply itself and carry the helper that wrote it, so a tapped
chip is answered by the same helper, not by Automatic.

Plain text a person could also type: a chip is a message, never a command.
"""

from __future__ import annotations

import re
from typing import Any

from api.services.helpers import catalogue as C

#: At most this many "go deeper" chips: the first points of the answer.
MAX_POINTS = 3

#: A numbered point at the start of a line: "1.", "2)", "### 3." and so on.
_POINT = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?(\d{1,2})[.)]\s+\S", re.MULTILINE)

_FIXED: dict[str, tuple[str, ...]] = {
    C.RESEARCH: ("Turn this into a one-page brief",),
    C.LEARNING_GUIDE: ("Quiz me on this", "Explain it more simply"),
}


def for_reply(helper: str | None, body: str) -> list[dict[str, Any]]:
    """The chips that continue ``body``, written by ``helper``; ``[]`` for
    Automatic or a helper with none."""
    if not helper or helper not in _FIXED:
        return []
    texts: list[str] = []
    if helper == C.RESEARCH:
        seen: list[int] = []
        for match in _POINT.finditer(body or ""):
            number = int(match.group(1))
            if number not in seen:
                seen.append(number)
        texts += [f"Go deeper on point {n}" for n in seen[:MAX_POINTS]]
    texts += list(_FIXED[helper])
    return [{"kind": "follow_up", "text": t, "helper": helper} for t in texts]


_LINK = re.compile(r"https?://\S+")


def for_agent_reply(body: str) -> list[dict[str, Any]]:
    """An agent's reply in its own chat: a reply that cites sources gets the
    research follow-ups, sent back to the same agent (``helper`` None)."""
    if not _LINK.search(body or ""):
        return []
    return [{**chip, "helper": None} for chip in for_reply(C.RESEARCH, body)]
