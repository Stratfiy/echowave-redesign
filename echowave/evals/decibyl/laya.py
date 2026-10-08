"""The Laya routing set as a second eval, in the same report shape.

The measurement is ``services/ops/laya_eval.evaluate`` unchanged -- the
rules against the decision model over ``evals/routing/laya_routing_labelled.jsonl``
-- so this suite and the ops console's Laya page can never disagree about
a number. What this adds is the shape: a pass or fail per sample (does Auto,
Laya with the rules as fallback, pick the labelled kind?), a score per
category, every miss with what each side said, and the baseline comparison.

Needs the API's Python dependencies (it imports the routing code) and
``LAYA_URL`` for the decision model. Without ``LAYA_URL`` every Laya answer
is an abstention, so the score is the rules' alone and the report says so.
"""

from __future__ import annotations

from typing import Any

from evals.decibyl.report import FAILED, PASSED, Result


async def evaluate(
    samples_path: str | None = None, *, chooser=None, limit=None, categories=None
):
    from api.services.ops import laya_eval

    samples = laya_eval.load_samples(samples_path)
    if categories:
        samples = [s for s in samples if s.category in categories]
    if limit is not None:
        samples = samples[:limit]
    measured = await laya_eval.evaluate(
        samples, **({"chooser": chooser} if chooser else {})
    )
    return samples, measured


def results(
    samples: list[Any], measured: dict[str, Any], decisions: list[Any] | None = None
) -> list[Result]:
    """One row per sample. ``decisions`` are the model's answers in sample
    order when the caller kept them; the aggregate report does not, so by
    default the row says what the rules and the combined route chose."""
    from api.services.routing import brain

    out: list[Result] = []
    for i, s in enumerate(samples):
        ruled = brain.by_rules(s.text, attachments=s.attachments)
        made = decisions[i] if decisions else None
        chosen = made.label if made is not None and made.label is not None else ruled
        ok = chosen == s.expected
        transcript = (
            f"text: {s.text}\nlanguage: {s.language}\nexpected: {s.expected}\n"
            f"rules: {ruled}\n"
            + (
                f"laya: {made.label or 'abstained'}"
                + (f" ({made.abstained})" if made.abstained else "")
                + (
                    f", confidence {made.confidence:.2f}"
                    if made.confidence is not None
                    else ""
                )
                + "\n"
                if made is not None
                else ""
            )
            + f"auto chose: {chosen}"
        )
        out.append(
            Result(
                id=s.id,
                category=s.category,
                status=PASSED if ok else FAILED,
                checks=[] if ok else [f"routed to {chosen!r}, labelled {s.expected!r}"],
                transcript=transcript,
            )
        )
    return out


async def run(
    samples_path: str | None = None, *, limit=None, categories=None, chooser=None
):
    """The suite: (results, extra report fields, decision-model calls made)."""
    from api.services.routing import decision

    kept: list[Any] = []
    inner = chooser or decision.choose

    async def keeping(question, labels, text):
        made = await inner(question, labels, text)
        kept.append((text, made))
        return made

    samples, measured = await evaluate(
        samples_path, chooser=keeping, limit=limit, categories=categories
    )
    by_text = {}
    for text, made in kept:
        by_text.setdefault(text, made)
    decisions = [by_text.get(s.text) for s in samples]
    rows = results(samples, measured, decisions)
    calls = sum(1 for _, m in kept if m.abstained != "off")
    extra = {
        "laya": {
            "configured": measured["laya_configured"],
            "rules_accuracy": measured["rules"]["accuracy"],
            "combined_accuracy": measured["combined"]["accuracy"],
            "laya_answered_accuracy": measured["laya"]["answered_accuracy"],
            "laya_abstention": measured["laya"]["abstention"],
            "latency_ms": measured["laya"]["latency_ms"],
            "by_language": measured["by_language"],
            "verdict": measured["verdict"],
        }
    }
    return rows, extra, calls
