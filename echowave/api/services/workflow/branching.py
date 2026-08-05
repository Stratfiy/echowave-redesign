"""Deterministic routing: pick an outgoing edge by evaluating a rule, not by asking a model.

Every other transition in a Decibyl workflow is decided by the language model.
Each outgoing edge becomes a function the LLM may call, and its ``condition`` is
natural-language prose in the function description — "the caller has confirmed
their appointment", "the customer sounds unhappy". For a judgement about what a
person meant, that is exactly right, and it is why the builder works the way it
does.

It is the wrong instrument for a fact. ``order_value > 50000`` is not a
judgement. Neither is "this is the third attempt" or "the caller chose Telugu".
Routing those through a model buys nothing and costs three things:

* **It is not reproducible.** The same call can take a different branch twice,
  and no log explains why, because the reason was a token sample.
* **It cannot be demonstrated.** "Show us that a complaint above ₹50,000 always
  reaches a human" has no answer if the escalation is a prompt.
* **It fails open.** A model that misreads a number routes the call somewhere
  plausible rather than stopping.

So this module evaluates rules the way every workflow tool outside voice does —
n8n's IF and Switch, Zapier's paths, Step Functions' Choice. Same inputs, same
branch, every time, with the winning rule written into the run record.

**Everything here is pure.** No I/O, no engine, no pipeline: a rule, a bag of
variables, and an answer. That is what makes the behaviour testable without a
call, and the tests are the specification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from loguru import logger

#: The operators a rule can use.
#:
#: Deliberately small and deliberately not an expression language. A branch node
#: exists to make routing legible to somebody who did not write it — an operator
#: reading a flow during an incident, an auditor reading it during a tender
#: review. Every entry here fits in a sentence. The moment this needs `and`,
#: `or` and parentheses, the honest answer is two branch nodes in a row, which
#: reads better on a canvas than a nested expression ever does.
OPERATORS: tuple[str, ...] = (
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "matches_regex",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
    "is_empty",
    "is_not_empty",
    "is_true",
    "is_false",
    "in_list",
    "not_in_list",
)

#: Operators that take no comparison value. Anything else with a blank value is
#: a half-finished rule, and validation says so rather than quietly matching.
UNARY_OPERATORS: frozenset[str] = frozenset(
    {"is_empty", "is_not_empty", "is_true", "is_false"}
)

#: Operators that need both sides to be numbers.
NUMERIC_OPERATORS: frozenset[str] = frozenset(
    {"greater_than", "greater_or_equal", "less_than", "less_or_equal"}
)

#: How long a regex is allowed to be. Not a security boundary — the author is
#: an authenticated operator editing their own workflow — but a catastrophic
#: backtracking pattern would stall a live call, and a cap plus the compile
#: check in :func:`validate_rule` catches the accidents.
MAX_REGEX_LENGTH = 200

#: Strings that count as true. Variables arrive from HTTP fetches, campaign CSV
#: columns and LLM extraction, so a boolean reaches here as ``true``, ``"true"``,
#: ``"True"``, ``"yes"`` or ``1`` depending on which. Treating only a real
#: ``bool`` as truthy would make ``is_true`` fail on most real data; treating
#: every non-empty string as truthy would make it match ``"false"``.
TRUTHY = frozenset({"true", "yes", "y", "1", "on"})
FALSEY = frozenset({"false", "no", "n", "0", "off", ""})


class BranchRuleError(ValueError):
    """A rule that cannot be evaluated as written."""


@dataclass(frozen=True)
class Rule:
    """One `if` on a branch node.

    ``label`` is the join to the canvas: it must match the label of one
    outgoing edge, and that is the edge taken when the rule matches. Matching on
    the label rather than a node id means the rule survives the node being moved,
    re-parented or duplicated, and it means the canvas reads as its own
    documentation — the edge says "high value" and so does the rule.
    """

    label: str
    variable: str
    operator: str
    value: str = ""

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Rule":
        return cls(
            label=str(raw.get("label") or "").strip(),
            variable=str(raw.get("variable") or "").strip(),
            operator=str(raw.get("operator") or "").strip(),
            value="" if raw.get("value") is None else str(raw.get("value")),
        )


@dataclass(frozen=True)
class Decision:
    """Which way the branch went, and why.

    The ``reason`` is written to the run record. A branch whose choice cannot be
    explained afterwards is only marginally better than a model's, so the
    explanation is part of the return type rather than a log line that may or
    may not have been enabled.
    """

    label: str
    matched: bool
    reason: str
    rule_index: int | None = None


def _as_number(value: Any) -> float | None:
    """Best-effort number, or None.

    Handles the shapes real data arrives in: ``50000``, ``"50000"``,
    ``"50,000"``, ``"₹50000"``, ``" 50000 "``. Deliberately does **not** handle
    ``"fifty thousand"`` — a rule that silently guesses at prose is worse than
    one that declines to match and says so.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    if not cleaned:
        return None
    # Strip currency symbols, thousands separators and spaces — but only those.
    cleaned = re.sub(r"[,\s₹$€£]", "", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in TRUTHY:
            return True
        if lowered in FALSEY:
            return False
    return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _split_list(value: str) -> list[str]:
    """Comma-separated list, trimmed, blanks dropped.

    ``te, en , hi`` and ``te,en,hi`` mean the same thing. Someone typing a list
    into a text box will use spaces, and a rule that fails because of one is a
    rule that wasted an afternoon.
    """
    return [part.strip() for part in value.split(",") if part.strip()]


def validate_rule(rule: Rule, *, index: int = 0) -> list[str]:
    """Everything wrong with one rule, in the operator's words.

    Returned rather than raised so the whole node can be reported at once — a
    form that surfaces one error per save is a form people give up on.
    """
    problems: list[str] = []
    where = f"Rule {index + 1}"

    if not rule.label:
        problems.append(f"{where}: needs a label matching one outgoing edge.")
    if not rule.variable:
        problems.append(f"{where}: needs a variable to test.")
    if rule.operator not in OPERATORS:
        problems.append(
            f"{where}: '{rule.operator or '(blank)'}' is not an operator. "
            f"Use one of: {', '.join(OPERATORS)}."
        )
        return problems

    if rule.operator in UNARY_OPERATORS:
        if rule.value:
            problems.append(
                f"{where}: '{rule.operator}' takes no value — remove '{rule.value}'."
            )
    elif not rule.value:
        problems.append(f"{where}: '{rule.operator}' needs a value to compare against.")

    if rule.operator in NUMERIC_OPERATORS and rule.value:
        if _as_number(rule.value) is None:
            problems.append(
                f"{where}: '{rule.value}' is not a number, so '{rule.operator}' "
                "can never match."
            )

    if rule.operator == "matches_regex" and rule.value:
        if len(rule.value) > MAX_REGEX_LENGTH:
            problems.append(
                f"{where}: pattern is longer than {MAX_REGEX_LENGTH} characters."
            )
        else:
            try:
                re.compile(rule.value)
            except re.error as exc:
                problems.append(f"{where}: pattern is not valid regex ({exc}).")

    return problems


def evaluate_rule(rule: Rule, variables: Mapping[str, Any]) -> tuple[bool, str]:
    """Does this rule match? And in one phrase, why?

    **An absent variable never matches** — except for ``is_empty``, where absence
    is the thing being asked about. This is the single most consequential
    decision in the module. A pre-call fetch that failed, a CSV column that was
    blank, an extraction that found nothing: all three arrive here as a missing
    key, and all three mean "we do not know". Treating unknown as a match sends
    a caller down a path chosen by an outage.
    """
    raw = variables.get(rule.variable)
    present = rule.variable in variables and raw is not None and _as_text(raw) != ""

    if rule.operator == "is_empty":
        return (not present, "value is empty" if not present else "value is present")
    if rule.operator == "is_not_empty":
        return (present, "value is present" if present else "value is empty")

    if not present:
        return False, f"{rule.variable} is not set"

    text = _as_text(raw)

    if rule.operator == "is_true":
        parsed = _as_bool(raw)
        return (parsed is True, f"{rule.variable} is {text}")
    if rule.operator == "is_false":
        parsed = _as_bool(raw)
        return (parsed is False, f"{rule.variable} is {text}")

    if rule.operator in NUMERIC_OPERATORS:
        left = _as_number(raw)
        right = _as_number(rule.value)
        if left is None:
            return False, f"{rule.variable} ('{text}') is not a number"
        if right is None:
            return False, f"'{rule.value}' is not a number"
        result = {
            "greater_than": left > right,
            "greater_or_equal": left >= right,
            "less_than": left < right,
            "less_or_equal": left <= right,
        }[rule.operator]
        return result, f"{rule.variable}={left:g} {rule.operator} {right:g}"

    # Text comparisons are case-insensitive. "Telugu" and "telugu" are the same
    # answer, and every source of these values — a caller's speech through
    # extraction, a CSV a human typed, a partner's JSON — disagrees about case.
    haystack = text.casefold()
    needle = rule.value.casefold()

    if rule.operator == "equals":
        return haystack == needle, f"{rule.variable}='{text}'"
    if rule.operator == "not_equals":
        return haystack != needle, f"{rule.variable}='{text}'"
    if rule.operator == "contains":
        return needle in haystack, f"{rule.variable}='{text}'"
    if rule.operator == "not_contains":
        return needle not in haystack, f"{rule.variable}='{text}'"
    if rule.operator == "starts_with":
        return haystack.startswith(needle), f"{rule.variable}='{text}'"
    if rule.operator == "ends_with":
        return haystack.endswith(needle), f"{rule.variable}='{text}'"
    if rule.operator == "in_list":
        options = [o.casefold() for o in _split_list(rule.value)]
        return haystack in options, f"{rule.variable}='{text}'"
    if rule.operator == "not_in_list":
        options = [o.casefold() for o in _split_list(rule.value)]
        return haystack not in options, f"{rule.variable}='{text}'"
    if rule.operator == "matches_regex":
        try:
            matched = re.search(rule.value, text) is not None
        except re.error as exc:
            # Validation catches this at save time; a workflow saved before this
            # module existed could still carry one. Not matching is the safe
            # outcome — it falls through to the default branch.
            logger.warning("Branch regex {!r} failed at runtime: {}", rule.value, exc)
            return False, f"pattern error: {exc}"
        return matched, f"{rule.variable}='{text}'"

    raise BranchRuleError(f"Unhandled operator {rule.operator!r}")


def decide(
    rules: Iterable[Rule],
    variables: Mapping[str, Any],
    *,
    default_label: str,
) -> Decision:
    """First matching rule wins; the default catches everything else.

    **Order is the tie-break, and it is the author's to control.** Overlapping
    rules are not a mistake to be detected — ``order_value > 100000`` above
    ``order_value > 50000`` is how you write bands, and it only reads correctly
    top-down. So the first match wins and the UI keeps the rows in the order
    they were written, exactly like every other rules engine an operator has
    used.

    A default is always required, so there is no such thing as a call that
    reaches a branch node and has nowhere to go.
    """
    for index, rule in enumerate(rules):
        matched, reason = evaluate_rule(rule, variables)
        if matched:
            return Decision(
                label=rule.label,
                matched=True,
                reason=f"{rule.label}: {reason}",
                rule_index=index,
            )

    return Decision(
        label=default_label,
        matched=False,
        reason=f"{default_label}: no rule matched",
        rule_index=None,
    )
