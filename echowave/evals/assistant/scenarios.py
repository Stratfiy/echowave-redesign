"""The assistant seed scenarios: loading and checking their shape.

Each scenario states the final state a run must leave (cards, schedules,
facts, follow-ups, what was sent or dialled), not what a fluent answer
sounds like. This module only loads and validates them; it calls no model.
A malformed scenario is an error, never a skip: a silently shorter set is a
silently different measurement.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent / "scenarios"

CATEGORIES = ("reminders", "memory", "meetings", "tools")
LANGUAGES = ("en", "ta", "hi", "ta-en", "hi-en")
#: ``current``: the state is checkable against main today. ``stage2``: it
#: names the reminder-call contract's stores (docs/plans/reminder-calls.md)
#: or time-bound facts, which Stage 2 builds.
TARGETS = ("current", "stage2")

#: Where a run's final state is read from (see README).
STORES = frozenset(
    {
        "cards",
        "chips",
        "reply",
        "outbound",
        "tasks",
        "facts",
        "personal_facts",
        "remembered_block",
        "follow_ups",
        "done_callbacks",
        "done_calls",
        "reminder_call_schedules",
        "reminder_call_occurrences",
        "reminder_call_dispatches",
        "any",
    }
)
#: One assertion on one store.
OPERATORS = frozenset(
    {"count", "fields", "fields_contain", "none", "none_text", "none_pattern", "script"}
)
TOP_LEVEL = frozenset(
    {
        "id",
        "category",
        "language",
        "target",
        "context",
        "turns",
        "on_call",
        "finish_at",
        "as",
        "expect",
        "why",
    }
)
EXPECT_KEYS = frozenset({"state", "tools", "recall"})

_SCRIPTS = {"ta": (0x0B80, 0x0BFF), "hi": (0x0900, 0x097F)}


class ScenarioError(ValueError):
    pass


def _has_script(text: str, language: str) -> bool:
    lo, hi = _SCRIPTS[language]
    return any(lo <= ord(ch) <= hi for ch in text)


def _words(scenario: dict[str, Any]) -> str:
    parts = list(scenario.get("turns") or []) + list(scenario.get("on_call") or [])
    parts.append(str((scenario.get("context") or {}).get("transcript") or ""))
    return "\n".join(str(p) for p in parts)


def _non_string_key(value: Any) -> Any:
    """YAML 1.1 reads a bare ``on``, ``yes`` or ``no`` key as a boolean, which
    silently turns a field into something no check looks for."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if not isinstance(key, str):
                return key
            found = _non_string_key(inner)
            if found is not None:
                return found
    if isinstance(value, list):
        for inner in value:
            found = _non_string_key(inner)
            if found is not None:
                return found
    return None


def check(scenario: dict[str, Any]) -> None:
    """Raise ScenarioError naming the first thing wrong with one scenario."""
    sid = scenario.get("id") or "<no id>"

    def fail(why: str) -> None:
        raise ScenarioError(f"{sid}: {why}")

    bad = _non_string_key(scenario)
    if bad is not None:
        fail(
            f"key {bad!r} is not a string (quote it: YAML reads on/yes/no as booleans)"
        )

    unknown = set(scenario) - TOP_LEVEL
    if unknown:
        fail(f"unknown keys {sorted(unknown)}")
    if not re.fullmatch(r"[a-z]+-[a-z-]+-\d{2}", str(sid)):
        fail("id must look like rem-en-01")
    if scenario.get("category") not in CATEGORIES:
        fail(f"category must be one of {CATEGORIES}")
    language = scenario.get("language")
    if language not in LANGUAGES:
        fail(f"language must be one of {LANGUAGES}")
    if scenario.get("target") not in TARGETS:
        fail(f"target must be one of {TARGETS}")
    context = scenario.get("context") or {}
    now = context.get("now")
    try:
        parsed = datetime.fromisoformat(str(now))
    except ValueError:
        fail("context.now must be an ISO timestamp")
    if parsed.tzinfo is None:
        fail("context.now needs a UTC offset: a reminder is nothing without a zone")
    if not _words(scenario).strip():
        fail("needs turns, on_call lines or a transcript")
    # Tamil and Hindi cases are in their own script; code-mixed ones may be
    # written in Latin letters (Hinglish usually is).
    if language in _SCRIPTS and not _has_script(_words(scenario), language):
        fail(f"a {language} scenario must be written in its own script")
    expect = scenario.get("expect") or {}
    if not expect.get("state"):
        fail("expect.state is required: scenarios check final state")
    if set(expect) - EXPECT_KEYS:
        fail(f"unknown expect keys {sorted(set(expect) - EXPECT_KEYS)}")
    for item in expect["state"]:
        store = item.get("store")
        if store not in STORES:
            fail(f"unknown store {store!r}")
        ops = set(item) - {"store"}
        if not ops or ops - OPERATORS:
            fail(f"store {store}: operators must be from {sorted(OPERATORS)}")
    for item in expect.get("recall") or []:
        datetime.fromisoformat(str(item.get("as_of")))


def load(root: Path = ROOT) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        if not isinstance(data, list):
            raise ScenarioError(f"{path.name}: must be a list of scenarios")
        for scenario in data:
            check(scenario)
            if scenario["category"] != path.stem:
                raise ScenarioError(
                    f"{scenario['id']}: category {scenario['category']} in {path.name}"
                )
            scenarios.append(scenario)
    ids = [s["id"] for s in scenarios]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ScenarioError(f"duplicate ids {dupes}")
    return scenarios


def summary(scenarios: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for s in scenarios:
        row = out.setdefault(s["category"], {})
        row[s["language"]] = row.get(s["language"], 0) + 1
    return out


if __name__ == "__main__":
    loaded = load()
    print(f"{len(loaded)} scenarios")
    for category, langs in summary(loaded).items():
        print(f"  {category}: {langs}")
