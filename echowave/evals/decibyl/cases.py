"""The labelled case set: loading, checking and slicing it.

A malformed line is an error, not a skip (as in ``laya_eval.load_samples``):
a silently shorter set is a silently different measurement. So is an
``expect`` key no check implements, or a category with no rubric.

Case shape (one JSON object per line in ``cases.jsonl``; ``//`` lines are
comments)::

    {
      "id": "act-email-01",            unique, stable across runs
      "category": "actions",           one of judge.RUBRICS
      "as": "any",                     which test account speaks: a, b, or
                                       any (the one with more turns left)
      "turns": ["...", "..."],         lines said in one fresh thread, in order
      "setup": [{"as": "a", "turns": ["..."]}],
                                       earlier threads: the state the case needs
      "preferences": {"simple_mode": true},
                                       the speaker's own settings for the case,
                                       put back afterwards
      "requires": {"features": [...], "connected": [...],
                   "not_connected": [...], "helpers": [...],
                   "two_accounts": true, "apps": true},
                                       skipped (never passed or failed) without
      "expect": {"card": {...}, "no_send": true, ...},
                                       deterministic checks (checks.CHECKS)
      "good": "what a good answer does",
                                       the judge's case-specific rubric line
      "judge": true,                   false: the checks alone decide
      "helper": "follow_up"            a helper from the picker; omitted is
                                       Automatic
    }

``{marker}`` anywhere in a case is replaced by a token unique to the run, so
a secret planted by one account can be looked for in the other's replies
without colliding with anything already on the account.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.decibyl import checks
from evals.decibyl.judge import RUBRICS

DEFAULT_PATH = Path(__file__).resolve().parent / "cases.jsonl"
#: "any" lets the runner pick whichever account has more turns left today.
ACCOUNTS = ("a", "b", "any")
REQUIREMENTS = frozenset(
    {"features", "connected", "not_connected", "helpers", "two_accounts", "apps"}
)


@dataclass(frozen=True)
class Setup:
    speaker: str
    turns: tuple[str, ...]


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    turns: tuple[str, ...]
    good: str
    speaker: str = "any"
    setup: tuple[Setup, ...] = ()
    preferences: dict[str, Any] = field(default_factory=dict)
    requires: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    judge: bool = True
    context: str = ""
    helper: str | None = None

    def with_marker(self, marker: str) -> Case:
        """The case with ``{marker}`` filled in everywhere."""
        raw = json.dumps(self.as_dict(), ensure_ascii=False).replace("{marker}", marker)
        return parse(json.loads(raw), where=self.id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "as": self.speaker,
            "turns": list(self.turns),
            "setup": [{"as": s.speaker, "turns": list(s.turns)} for s in self.setup],
            "preferences": self.preferences,
            "requires": self.requires,
            "expect": self.expect,
            "good": self.good,
            "judge": self.judge,
            "context": self.context,
            "helper": self.helper,
        }


class CaseError(ValueError):
    pass


def parse(data: dict[str, Any], *, where: str) -> Case:
    def need(key: str) -> Any:
        if key not in data:
            raise CaseError(f"{where}: missing {key!r}")
        return data[key]

    category = str(need("category"))
    if category not in RUBRICS:
        raise CaseError(f"{where}: unknown category {category!r}")
    turns = need("turns")
    if (
        not isinstance(turns, list)
        or not turns
        or not all(isinstance(t, str) and t.strip() for t in turns)
    ):
        raise CaseError(f"{where}: 'turns' must be a non-empty list of lines")
    speaker = str(data.get("as", "any"))
    if speaker not in ACCOUNTS:
        raise CaseError(f"{where}: 'as' must be one of {ACCOUNTS}")
    setup = []
    for s in data.get("setup") or []:
        who = str(s.get("as", "a"))
        if who not in ("a", "b") or not s.get("turns"):
            raise CaseError(f"{where}: a setup step needs 'as' and 'turns'")
        setup.append(Setup(who, tuple(s["turns"])))
    expect = dict(data.get("expect") or {})
    unknown = sorted(set(expect) - set(checks.CHECKS))
    if unknown:
        raise CaseError(f"{where}: no check called {unknown}")
    requires = dict(data.get("requires") or {})
    if set(requires) - REQUIREMENTS:
        raise CaseError(
            f"{where}: unknown requirement {sorted(set(requires) - REQUIREMENTS)}"
        )
    judged = bool(data.get("judge", True))
    good = str(data.get("good") or "").strip()
    if judged and not good:
        raise CaseError(f"{where}: a judged case needs 'good'")
    if not judged and not expect:
        raise CaseError(f"{where}: a case the judge skips needs checks")
    two = speaker == "b" or any(s.speaker == "b" for s in setup)
    if two:
        requires.setdefault("two_accounts", True)
    return Case(
        id=str(need("id")),
        category=category,
        turns=tuple(turns),
        good=good,
        speaker=speaker,
        setup=tuple(setup),
        preferences=dict(data.get("preferences") or {}),
        requires=requires,
        expect=expect,
        judge=judged,
        context=str(data.get("context") or ""),
        helper=(str(data["helper"]) if data.get("helper") else None),
    )


def load(path: Path | str | None = None) -> list[Case]:
    path = Path(path or DEFAULT_PATH)
    out: list[Case] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        where = f"{path.name}:{number}"
        try:
            data = json.loads(line)
        except ValueError as exc:
            raise CaseError(f"{where}: {exc}") from exc
        case = parse(data, where=where)
        if case.id in seen:
            raise CaseError(f"{where}: duplicate id {case.id!r}")
        seen.add(case.id)
        out.append(case)
    return out


def select(
    cases: list[Case],
    *,
    categories: list[str] | None = None,
    ids: list[str] | None = None,
    limit: int | None = None,
) -> list[Case]:
    """A slice of the set: by category, by id, then the first ``limit``.

    ``limit`` takes the first N of *each* category asked for when categories
    are given, so ``--category actions --category safety --limit 3`` runs
    three of each rather than three of whichever comes first."""
    if categories:
        unknown = sorted(set(categories) - set(RUBRICS))
        if unknown:
            raise CaseError(
                f"No category called {unknown}; there are {sorted(RUBRICS)}"
            )
        cases = [c for c in cases if c.category in categories]
    if ids:
        missing = sorted(set(ids) - {c.id for c in cases})
        if missing:
            raise CaseError(f"No case called {missing}")
        cases = [c for c in cases if c.id in ids]
    if limit is not None and limit >= 0:
        if categories and len(categories) > 1:
            kept: list[Case] = []
            for name in categories:
                kept += [c for c in cases if c.category == name][:limit]
            cases = kept
        else:
            cases = cases[:limit]
    return cases


def new_marker() -> str:
    return f"ZQX{secrets.token_hex(4).upper()}"
