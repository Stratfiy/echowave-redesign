"""One line per call, read off what post-call QA already wrote.

QA runs on every call and stores a summary, a score out of ten, a
sentiment and tags on the run's annotations, in one of two shapes (per
node, or whole call). The review inbox needs one verdict per call and does
not care which shape produced it; this is the one place that knows both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: A score at or below this, or a negative sentiment, is a call somebody
#: should listen to.
ATTENTION_SCORE = 5
NEGATIVE = {"negative", "frustrated", "angry", "unhappy", "poor"}


@dataclass(frozen=True)
class Verdict:
    summary: str | None
    score: int | None
    sentiment: str | None
    tags: tuple[str, ...]

    @property
    def needs_attention(self) -> bool:
        if self.score is not None and self.score <= ATTENTION_SCORE:
            return True
        return (self.sentiment or "").strip().lower() in NEGATIVE

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "score": self.score,
            "sentiment": self.sentiment,
            "tags": list(self.tags),
            "needs_attention": self.needs_attention,
        }


def _tags(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict) and isinstance(item.get("tag"), str):
            out.append(item["tag"])
    return tuple(out)


def from_annotations(annotations: dict[str, Any] | None) -> Verdict | None:
    """The call's verdict, or None when QA left nothing to say."""
    if not isinstance(annotations, dict):
        return None
    candidates: list[dict[str, Any]] = []
    for value in annotations.values():
        if not isinstance(value, dict):
            continue
        results = value.get("node_results")
        if isinstance(results, dict):
            candidates.extend(n for n in results.values() if isinstance(n, dict))
        elif "summary" in value or "score" in value:
            candidates.append(value)
    if not candidates:
        return None
    pick = next(
        (
            c
            for c in candidates
            if isinstance(c.get("summary"), str) and c["summary"].strip()
        ),
        candidates[0],
    )
    summary = pick.get("summary")
    score = pick.get("score")
    sentiment = pick.get("overall_sentiment")
    return Verdict(
        summary=summary.strip()
        if isinstance(summary, str) and summary.strip()
        else None,
        score=int(score)
        if isinstance(score, (int, float)) and not isinstance(score, bool)
        else None,
        sentiment=sentiment.strip()
        if isinstance(sentiment, str) and sentiment.strip()
        else None,
        tags=_tags(pick.get("tags")),
    )
