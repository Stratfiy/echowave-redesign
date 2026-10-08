"""Trading summaries are information only, never advice (founder request).

The Research helper's instructions say so; this is what holds when a model
does not listen. ``scrub`` removes any sentence that tells the reader what
to do with a security -- buy, sell, hold, accumulate, a target price, a
stop-loss to set -- and the summary always carries ``NOTICE``. A sentence
that only reports a fact ("the stock fell 3%", "analysts at X raised
their target to 900") is kept: reporting what someone said is information;
telling the reader to act on it is advice.

Deliberately a blocklist of advice shapes, not an allowlist of facts: a
missed phrasing leaves advice the reviewer can see and add here, where an
allowlist would silently drop facts nobody would miss.
"""

from __future__ import annotations

import re

#: Shown with every trading summary. A safety line, not positioning.
NOTICE = "Information only, not investment advice. Decide with a registered adviser."

REMOVED = "(A recommendation was removed: Decibyl gives information, not advice.)"

_ADVICE = re.compile(
    r"""
    \b(you|we|i)\s+(should|must|could|might\s+want\s+to|may\s+want\s+to)\s+
        (buy|sell|hold|short|accumulate|exit|book\s+profits?|average|add|invest|trim)\b
    | \b(strong\s+)?(buy|sell|hold|accumulate|outperform|underperform)\s+(rating|call|signal|recommendation)\b
    | \b(i|we)\s+recommend\b
    | \brecommend(ed)?\s+(buying|selling|holding|shorting)\b
    | \b(good|great|right|best)\s+time\s+to\s+(buy|sell|enter|exit|invest)\b
    | \b(set|place|keep)\s+(a\s+)?stop[\s-]?loss\b
    | \bmy\s+target\b
    | \b(go|going)\s+long\b|\b(go|going)\s+short\b
    | \b(consider|start)\s+(buying|selling|shorting|accumulating)\b
    | ^\s*(buy|sell|hold|accumulate|avoid)\b
    """,
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
)

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def is_advice(sentence: str) -> bool:
    return bool(_ADVICE.search(sentence or ""))


def scrub(text: str | None) -> str:
    """``text`` with every advice sentence removed, and one line saying a
    recommendation was removed when any was. Never raises."""
    if not text:
        return ""
    parts = [p for p in _SENTENCE.split(text) if p and p.strip()]
    kept = [p for p in parts if not is_advice(p)]
    out = " ".join(p.strip() for p in kept).strip()
    if len(kept) != len(parts):
        out = f"{out} {REMOVED}".strip()
    return out


def finish(reply: str) -> str:
    """A trading summary reply as the person reads it: scrubbed, with the
    notice once."""
    body = scrub(reply)
    if NOTICE not in body:
        body = f"{body}\n\n{NOTICE}".strip()
    return body
