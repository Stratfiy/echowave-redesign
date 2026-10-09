"""Was each handover right, and on time? Labelled after the call, by a person.

Raw containment ("how many calls did the agent keep") rewards an agent that
never lets anybody go. The research brief's measure is containment split by
outcome, with precision and recall for escalation judged against a
**tolerance window** -- the GT-T idea in Liu et al., "Time to Transfer:
Predicting and Evaluating Machine-Human Chatting Handoff" (AAAI 2021): a
transfer one turn later than it should have been is fine, five turns late is
a failure.

So each call's outcome row (``call_escalation_outcomes``) carries the caller
turn the escalation was decided on (``caller_turn``), and a reviewer can
later set a label and, where it should have gone to a person, the turn it
should have gone on:

* ``resolved_by_ai`` -- the agent kept it, rightly.
* ``escalated_correctly`` -- it went to a person, rightly.
* ``escalated_unnecessarily`` -- it went to a person, and need not have.
* ``should_have_escalated`` -- the agent kept it, and should not have.

``score`` turns labelled rows into the numbers; ``report`` reads them for one
workspace. Pure arithmetic on what is stored -- nothing here is guessed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from api.db import db_client

RESOLVED_BY_AI = "resolved_by_ai"
ESCALATED_CORRECTLY = "escalated_correctly"
ESCALATED_UNNECESSARILY = "escalated_unnecessarily"
SHOULD_HAVE_ESCALATED = "should_have_escalated"
LABELS = (
    RESOLVED_BY_AI,
    ESCALATED_CORRECTLY,
    ESCALATED_UNNECESSARILY,
    SHOULD_HAVE_ESCALATED,
)
#: Which recorded outcome each label can be given to.
_LABEL_FITS = {
    RESOLVED_BY_AI: "resolved_by_ai",
    ESCALATED_CORRECTLY: "escalated",
    ESCALATED_UNNECESSARILY: "escalated",
    SHOULD_HAVE_ESCALATED: "resolved_by_ai",
}
#: The labels that say the call should have reached a person, and so may
#: carry the turn it should have reached one on.
_TIMED = frozenset({ESCALATED_CORRECTLY, SHOULD_HAVE_ESCALATED})

DEFAULT_WINDOW = 1
MAX_WINDOW = 20


class LabelInvalid(ValueError):
    pass


class OutcomeNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Row:
    """What the score needs from one call."""

    outcome: str
    label: str | None
    caller_turn: int | None = None
    expected_turn: int | None = None


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score(rows: Iterable[Row], *, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """Precision, recall and F1 of escalation within ``window`` turns.

    An escalation labelled correct is **on time** when it was decided no
    earlier than the turn the reviewer named and at most ``window`` turns
    after it. Earlier is ``early``, later is ``late``; neither counts as a hit.
    One labelled correct with no turn named is a hit (``untimed``), because
    the reviewer gave no point to measure from.

    * precision = on-time / (escalated_correctly + escalated_unnecessarily)
    * recall    = on-time / (escalated_correctly + should_have_escalated)
    """
    if window < 0:
        raise ValueError("The window is a number of turns, 0 or more.")
    rows = list(rows)
    labels = Counter(r.label for r in rows if r.label)
    outcomes = Counter(r.outcome for r in rows)
    on_time = early = late = untimed = 0
    for r in rows:
        if r.label != ESCALATED_CORRECTLY:
            continue
        if r.expected_turn is None or r.caller_turn is None:
            untimed += 1
            on_time += 1
            continue
        delta = r.caller_turn - r.expected_turn
        if delta < 0:
            early += 1
        elif delta <= window:
            on_time += 1
        else:
            late += 1
    escalated = labels[ESCALATED_CORRECTLY] + labels[ESCALATED_UNNECESSARILY]
    should = labels[ESCALATED_CORRECTLY] + labels[SHOULD_HAVE_ESCALATED]
    precision = _ratio(on_time, escalated)
    recall = _ratio(on_time, should)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall == 0:
        f1 = 0.0
    else:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    labelled = sum(labels.values())
    return {
        "window": window,
        "calls": len(rows),
        "labelled": labelled,
        "unlabelled": len(rows) - labelled,
        "outcomes": dict(outcomes),
        "labels": {label: labels[label] for label in LABELS},
        "on_time": on_time,
        "early": early,
        "late": late,
        "untimed": untimed,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        #: Of the labelled calls, the share the agent kept and rightly.
        "resolved_by_ai_rate": _ratio(labels[RESOLVED_BY_AI], labelled),
    }


def _row(model: Any) -> Row:
    return Row(
        outcome=model.outcome,
        label=model.qa_label,
        caller_turn=model.caller_turn,
        expected_turn=model.qa_expected_turn,
    )


async def report(
    organization_id: int,
    *,
    window: int = DEFAULT_WINDOW,
    since: datetime | None = None,
    workflow_id: int | None = None,
) -> dict[str, Any]:
    """The score for one workspace, plus what the shadow rules would have done."""
    if not 0 <= window <= MAX_WINDOW:
        raise LabelInvalid(f"The window is 0 to {MAX_WINDOW} turns.")
    models = await db_client.list_call_escalation_outcomes(
        organization_id=organization_id, since=since, workflow_id=workflow_id
    )
    out = score([_row(m) for m in models], window=window)
    shadow: Counter = Counter()
    shadow_calls = 0
    for model in models:
        hits = model.shadow_escalations or []
        if hits:
            shadow_calls += 1
        for hit in hits:
            shadow[str((hit or {}).get("rule") or "unknown")] += 1
    out["shadow"] = {"calls": shadow_calls, "by_rule": dict(shadow)}
    return out


def check_label(
    outcome: str, label: str | None, expected_turn: int | None, caller_turns: int | None
) -> None:
    """Refuse a label that cannot be true of this call, with the reason."""
    if label is None:
        if expected_turn is not None:
            raise LabelInvalid("A turn needs a label to go with it.")
        return
    if label not in LABELS:
        raise LabelInvalid(f"{label!r} is not a label; one of {', '.join(LABELS)}.")
    if _LABEL_FITS[label] != outcome:
        raise LabelInvalid(f"{label} does not fit a call whose outcome was {outcome}.")
    if expected_turn is not None:
        if label not in _TIMED:
            raise LabelInvalid(
                "Only escalated_correctly or should_have_escalated take the "
                "turn it should have gone to a person on."
            )
        if expected_turn < 1 or (caller_turns and expected_turn > caller_turns):
            raise LabelInvalid(
                f"The call had {caller_turns or 'no'} caller turns; "
                f"turn {expected_turn} is not one of them."
            )


async def set_label(
    organization_id: int,
    workflow_run_id: int,
    *,
    label: str | None,
    expected_turn: int | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    current = await db_client.get_call_escalation_outcome(
        workflow_run_id, organization_id=organization_id
    )
    if current is None:
        raise OutcomeNotFound("No escalation outcome for that call here.")
    check_label(current.outcome, label, expected_turn, current.caller_turns)
    model = await db_client.label_call_escalation_outcome(
        workflow_run_id,
        organization_id=organization_id,
        label=label,
        expected_turn=expected_turn,
        user_id=user_id,
    )
    if model is None:
        raise OutcomeNotFound("No escalation outcome for that call here.")
    return as_dict(model)


def as_dict(model: Any) -> dict[str, Any]:
    return {
        "workflow_run_id": model.workflow_run_id,
        "workflow_id": model.workflow_id,
        "outcome": model.outcome,
        "reason_code": model.reason_code,
        "transfer_result": model.transfer_result,
        "caller_turn": model.caller_turn,
        "caller_turns": model.caller_turns,
        "shadow_escalations": list(model.shadow_escalations or []),
        "qa_label": model.qa_label,
        "qa_expected_turn": model.qa_expected_turn,
        "qa_labelled_at": model.qa_labelled_at.isoformat()
        if model.qa_labelled_at
        else None,
    }


__all__ = [
    "DEFAULT_WINDOW",
    "ESCALATED_CORRECTLY",
    "ESCALATED_UNNECESSARILY",
    "LABELS",
    "LabelInvalid",
    "OutcomeNotFound",
    "RESOLVED_BY_AI",
    "Row",
    "SHOULD_HAVE_ESCALATED",
    "as_dict",
    "check_label",
    "report",
    "score",
    "set_label",
]
