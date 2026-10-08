"""Does a learning detail look sensitive? (handoff 6: "Ask before saving
sensitive learning details".)

A goal, a course or pasted notes can say more about a person than they
meant to store: a diagnosis behind "learn to manage my diabetes", a faith,
a debt. Nothing is refused -- people learn about these things -- but it is
not saved until the person says yes, and the question names what it saw.

A list of words, so it errs towards asking: the worst case of a false match
is one extra question; the worst case of a miss is a health detail kept
without being asked. English plus a few common Hindi words; the categories
are what the screen names.
"""

from __future__ import annotations

import re

CATEGORIES: dict[str, tuple[str, ...]] = {
    "health": (
        "diagnos",
        "disease",
        "diabetes",
        "cancer",
        "hiv",
        "pregnan",
        "medication",
        "medicine i take",
        "my illness",
        "my condition",
        "therapy",
        "bimari",
        "बीमारी",
    ),
    "mental health": (
        "depression",
        "anxiety",
        "adhd",
        "dyslexi",
        "autis",
        "bipolar",
        "panic attack",
        "trauma",
    ),
    "disability": ("disabilit", "hearing loss", "blind", "wheelchair"),
    "religion or caste": ("religio", "caste", "jaati", "जाति", "dharm", "धर्म"),
    "sexuality": ("sexual orientation", "gay", "lesbian", "transgender"),
    "money trouble": ("my debt", "loan default", "bankrupt", "karz", "कर्ज"),
    "legal trouble": ("my court case", "arrested", "criminal record", "fir against"),
}

_PATTERNS = {
    name: re.compile("|".join(re.escape(word) for word in words), re.IGNORECASE)
    for name, words in CATEGORIES.items()
}


def detect(*texts: str | None) -> list[str]:
    """The categories any of ``texts`` touch, in a fixed order."""
    joined = "\n".join(t for t in texts if t)
    if not joined:
        return []
    return [name for name, pattern in _PATTERNS.items() if pattern.search(joined)]
