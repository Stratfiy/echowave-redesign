"""Telling the clock line apart from the rest of a system prompt.

``compose_today_line`` writes a line that changes every minute. In the middle
of a system prompt it makes the bytes different on every call, so a prompt
cache keyed on the system prompt can never be read. The Anthropic adapter
therefore lifts that one paragraph out of the cached system block and sends it
after it (see ``anthropic_llm.py``); the model still reads it, last.

This module has no imports from the rest of the app so both the composer (which
writes the line) and the adapter (which finds it) can use it.
"""

from __future__ import annotations

import re

#: One paragraph, exactly as ``compose_today_line`` writes it.
CLOCK_PARAGRAPH = re.compile(
    r"Right now it is [^\n]+? \d{4}, \d{2}:\d{2} \([^\n)]+\)\. "
    r"Work every date and time out from this, and never from anything you "
    r"remember\. When you need a calendar date, count it from today\."
)


def split_clock(system: str) -> tuple[str, str | None]:
    """``(system without the clock, the clock paragraph)``; the clock is
    ``None`` and the system untouched when it carries none."""
    paragraphs = system.split("\n\n")
    for i in range(len(paragraphs) - 1, -1, -1):
        if CLOCK_PARAGRAPH.fullmatch(paragraphs[i]):
            clock = paragraphs.pop(i)
            return "\n\n".join(paragraphs), clock
    return system, None
