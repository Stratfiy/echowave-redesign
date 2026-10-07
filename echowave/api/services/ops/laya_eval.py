"""Jev / Laya guardrails and shadow evaluation (handoff 14, packet G).

The handoff's order is: start in shadow, build a reviewed multilingual set
of normal, ambiguous and adversarial cases, measure false negatives, false
positives, abstention, calibration and added p95 latency; on timeout or
malformed output fall back to deterministic rules; keep a rollback. The
decision service (``services/routing/decision.py``) and Auto
(``services/routing/brain.py``) already abstain safely. This module adds the
four things that were missing:

1. **A hard deadline.** ``httpx``'s timeout applies per phase -- connect,
   write and read can each take ``LAYA_TIMEOUT_MS`` -- so a slow Laya could
   hold a reply for three times the budget. ``guarded_choose`` wraps the
   whole decision in ``asyncio.wait_for(LAYA_HARD_DEADLINE_MS)``.
2. **A circuit breaker.** After ``LAYA_BREAKER_FAILURES`` consecutive
   timeouts, errors or malformed answers, Laya is not asked for
   ``LAYA_BREAKER_COOLDOWN_SECONDS``: a down classifier should cost nothing,
   not a timeout per message. Per process; each worker learns on its own.
3. **The rollback switch.** The ``laya_rollback`` flag, flipped from the
   staff console (or as the ``laya.rollback`` ops command), makes Auto route
   by rules alone and never call Laya -- in every worker within seconds,
   through the feature-override sync, with no deploy.
4. **Measurement.** Live shadow agreement counters (no text, counts only)
   and an offline evaluation over the labelled set in
   ``evals/routing/laya_routing_labelled.jsonl``, producing a report with a
   promotion verdict.

Ordinary chat must never become unavailable because a classifier is down:
every path here ends in a Decision, never an exception.
"""

from __future__ import annotations

import asyncio
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from api import constants
from api.services import features
from api.services.routing import decision

GUARDRAILS_FLAG = "laya_guardrails"
ROLLBACK_FLAG = "laya_rollback"

#: Abstentions that count against the breaker. low_confidence does not: an
#: unsure model is working as designed.
BREAKER_FAILURES = frozenset({"timeout", "error", "malformed", "deadline"})

SHADOW_KEY_PREFIX = "decibyl:laya:shadow:"
SHADOW_TTL_SECONDS = 35 * 86400
LATENCY_BUCKETS_MS = (50, 100, 200, 350, 500, 1000)

#: Promotion gate: Laya may leave shadow only when, on the labelled set, its
#: answered accuracy beats the rules by this margin, no category regresses,
#: abstention stays under this share, and its p95 stays inside the deadline.
PROMOTION_MARGIN = 0.02
MAX_ABSTENTION = 0.25
#: The handoff's minimum for a voice evaluation set, applied here too.
MIN_SAMPLES = 300

DEFAULT_SAMPLES = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "routing"
    / "laya_routing_labelled.jsonl"
)


# --- breaker ---------------------------------------------------------------


@dataclass
class _Breaker:
    failures: int = 0
    open_until: float = 0.0
    opened_total: int = 0

    def is_open(self, now: float | None = None) -> bool:
        return (now or time.monotonic()) < self.open_until

    def record(self, abstained: str | None, now: float | None = None) -> None:
        if abstained in BREAKER_FAILURES:
            self.failures += 1
            if self.failures >= max(constants.LAYA_BREAKER_FAILURES, 1):
                self.open_until = (
                    now or time.monotonic()
                ) + constants.LAYA_BREAKER_COOLDOWN_SECONDS
                self.opened_total += 1
                self.failures = 0
                logger.warning(
                    "Laya circuit opened for {}s after repeated failures; rules decide",
                    constants.LAYA_BREAKER_COOLDOWN_SECONDS,
                )
        elif abstained is None or abstained == "low_confidence":
            self.failures = 0


_BREAKER = _Breaker()


def reset_breaker() -> None:
    """For tests."""
    global _BREAKER
    _BREAKER = _Breaker()


def rolled_back() -> bool:
    return features.is_on(ROLLBACK_FLAG)


def breaker_state() -> dict[str, Any]:
    remaining = max(_BREAKER.open_until - time.monotonic(), 0.0)
    return {
        "guardrails": features.is_on(GUARDRAILS_FLAG),
        "rolled_back": rolled_back(),
        "open": _BREAKER.is_open(),
        "open_for_seconds": round(remaining, 1),
        "consecutive_failures": _BREAKER.failures,
        "times_opened": _BREAKER.opened_total,
        "routing": constants.LAYA_ROUTING,
        "hard_deadline_ms": constants.LAYA_HARD_DEADLINE_MS,
    }


async def guarded_choose(
    question: str,
    labels: dict[str, str],
    text: str,
    *,
    chooser: Callable[..., Awaitable[decision.Decision]] | None = None,
) -> decision.Decision:
    """``decision.choose`` behind the rollback switch, and -- while
    ``laya_guardrails`` is on -- the breaker and the hard deadline."""
    chooser = chooser or decision.choose
    if rolled_back():
        return decision.Decision(
            label=None, confidence=None, elapsed_ms=0, abstained="rolled_back"
        )
    if not features.is_on(GUARDRAILS_FLAG):
        return await chooser(question, labels, text)
    if _BREAKER.is_open():
        return decision.Decision(
            label=None, confidence=None, elapsed_ms=0, abstained="circuit_open"
        )
    started = time.monotonic()
    try:
        made = await asyncio.wait_for(
            chooser(question, labels, text),
            timeout=max(constants.LAYA_HARD_DEADLINE_MS, 1) / 1000,
        )
    except TimeoutError:
        made = decision.Decision(
            label=None,
            confidence=None,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            abstained="deadline",
        )
    except Exception as exc:  # noqa: BLE001 - routing must never cost a reply
        logger.warning("Decision call raised: {}", type(exc).__name__)
        made = decision.Decision(
            label=None,
            confidence=None,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            abstained="error",
        )
    _BREAKER.record(made.abstained)
    return made


# --- live shadow statistics -------------------------------------------------


def _day(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%d")


def _bucket(ms: int | None) -> str:
    if ms is None:
        return "none"
    for edge in LATENCY_BUCKETS_MS:
        if ms <= edge:
            return f"le_{edge}"
    return f"gt_{LATENCY_BUCKETS_MS[-1]}"


def shadow_fields(ruled: str, made: decision.Decision) -> list[str]:
    """The counters one routed message increments. Pure. No text, ever."""
    fields = ["total", f"latency:{_bucket(made.elapsed_ms)}"]
    if made.truncated:
        fields.append("truncated")
    if made.label is None:
        fields.append(f"abstained:{made.abstained or 'unknown'}")
    elif made.label == ruled:
        fields.append("agree")
        fields.append(f"agree:{ruled}")
    else:
        fields.append("disagree")
        fields.append(f"disagree:{ruled}->{made.label}")
    return fields


async def record_shadow(ruled: str, made: decision.Decision, *, client=None) -> None:
    """Count one shadow comparison. Never raises, never blocks a reply for
    longer than one Redis pipeline."""
    if not features.is_on(GUARDRAILS_FLAG):
        return
    own = client is None
    try:
        if own:
            import redis.asyncio as aioredis

            client = aioredis.from_url(constants.REDIS_URL)
        key = f"{SHADOW_KEY_PREFIX}{_day()}"
        pipe = client.pipeline()
        for name in shadow_fields(ruled, made):
            pipe.hincrby(key, name, 1)
        pipe.expire(key, SHADOW_TTL_SECONDS)
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Laya shadow counters not written: {}", type(exc).__name__)
    finally:
        if own and client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass


async def shadow_report(
    *, days: int = 7, client=None, now: datetime | None = None
) -> dict[str, Any]:
    """Sum the live counters over the last ``days`` days."""
    own = client is None
    totals: Counter[str] = Counter()
    try:
        if own:
            import redis.asyncio as aioredis

            client = aioredis.from_url(constants.REDIS_URL)
        start = now or datetime.now(UTC)
        for offset in range(max(days, 1)):
            raw = await client.hgetall(
                f"{SHADOW_KEY_PREFIX}{_day(start - timedelta(days=offset))}"
            )
            for name, value in (raw or {}).items():
                name = name.decode() if isinstance(name, bytes) else str(name)
                totals[name] += int(value)
        readable = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Laya shadow counters unreadable: {}", type(exc).__name__)
        readable = False
    finally:
        if own and client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass
    total = totals.get("total", 0)
    answered = totals.get("agree", 0) + totals.get("disagree", 0)
    return {
        "days": days,
        "readable": readable,
        "total": total,
        "agreement": round(totals["agree"] / answered, 4) if answered else None,
        "abstention": round((total - answered) / total, 4) if total else None,
        "counters": dict(sorted(totals.items())),
        "breaker": breaker_state(),
    }


# --- offline evaluation over the labelled set ------------------------------


@dataclass(frozen=True)
class Sample:
    id: str
    text: str
    expected: str
    category: str  # normal, ambiguous, adversarial
    language: str
    attachments: int = 0


def load_samples(path: Path | str | None = None) -> list[Sample]:
    """Read the labelled JSONL. A malformed line is an error, not a skip:
    a silently shorter set is a silently different measurement."""
    path = Path(path or DEFAULT_SAMPLES)
    samples: list[Sample] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        try:
            data = json.loads(line)
            sample = Sample(
                id=str(data["id"]),
                text=str(data["text"]),
                expected=str(data["expected"]),
                category=str(data.get("category", "normal")),
                language=str(data.get("language", "en")),
                attachments=int(data.get("attachments", 0)),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        samples.append(sample)
    return samples


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(math.ceil(pct / 100 * len(ordered)) - 1, 0)
    return round(ordered[index], 1)


def _per_kind(
    pairs: Iterable[tuple[str, str | None]], kinds: Iterable[str]
) -> dict[str, Any]:
    """Precision and recall per kind; None predictions are abstentions and
    count as misses for recall (a false negative) but not as false positives."""
    pairs = list(pairs)
    out: dict[str, Any] = {}
    for kind in kinds:
        tp = sum(1 for exp, got in pairs if exp == kind and got == kind)
        fp = sum(1 for exp, got in pairs if exp != kind and got == kind)
        fn = sum(1 for exp, got in pairs if exp == kind and got != kind)
        out[kind] = {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        }
    return out


def _calibration(points: list[tuple[float, bool]], bins: int = 5) -> dict[str, Any]:
    """Confidence against correctness, and the expected calibration error."""
    rows = []
    ece = 0.0
    for b in range(bins):
        low, high = b / bins, (b + 1) / bins
        inside = [
            (c, ok)
            for c, ok in points
            if (low <= c < high) or (b == bins - 1 and c == 1.0)
        ]
        if not inside:
            continue
        confidence = statistics.fmean(c for c, _ in inside)
        accuracy = sum(1 for _, ok in inside if ok) / len(inside)
        ece += len(inside) / len(points) * abs(confidence - accuracy)
        rows.append(
            {
                "bin": f"{low:.1f}-{high:.1f}",
                "count": len(inside),
                "mean_confidence": round(confidence, 4),
                "accuracy": round(accuracy, 4),
            }
        )
    return {"bins": rows, "ece": round(ece, 4) if points else None}


def _accuracy(pairs: list[tuple[str, str | None]]) -> float | None:
    return round(sum(1 for e, g in pairs if e == g) / len(pairs), 4) if pairs else None


async def evaluate(
    samples: list[Sample],
    *,
    chooser: Callable[..., Awaitable[decision.Decision]] | None = None,
    concurrency: int = 4,
) -> dict[str, Any]:
    """Run the rules and the decision model over the labelled set.

    ``chooser`` defaults to the live decision service. With no ``LAYA_URL``
    every Laya answer is an abstention, and the report says so rather than
    inventing a comparison.
    """
    from api.services.routing import brain

    chooser = chooser or decision.choose
    gate = asyncio.Semaphore(max(concurrency, 1))

    async def ask(sample: Sample) -> decision.Decision:
        async with gate:
            try:
                return await asyncio.wait_for(
                    chooser(brain.QUESTION, brain.KINDS, sample.text),
                    timeout=max(constants.LAYA_HARD_DEADLINE_MS, 1) / 1000 * 4,
                )
            except TimeoutError:
                return decision.Decision(
                    None, None, constants.LAYA_HARD_DEADLINE_MS * 4, "deadline"
                )
            except Exception:  # noqa: BLE001
                return decision.Decision(None, None, 0, "error")

    rules_started = time.perf_counter()
    rules = [brain.by_rules(s.text, attachments=s.attachments) for s in samples]
    rules_ms = (time.perf_counter() - rules_started) * 1000 / max(len(samples), 1)
    made = await asyncio.gather(*(ask(s) for s in samples))

    kinds = list(brain.KINDS)
    rule_pairs = [(s.expected, r) for s, r in zip(samples, rules)]
    laya_pairs = [(s.expected, m.label) for s, m in zip(samples, made)]
    answered = [
        (s.expected, m.label) for s, m in zip(samples, made) if m.label is not None
    ]
    # What Auto would do with Laya on: Laya when it answers, rules otherwise.
    combined = [
        (s.expected, m.label if m.label is not None else r)
        for s, m, r in zip(samples, made, rules)
    ]
    latencies = [float(m.elapsed_ms) for m in made if m.abstained != "off"]

    def breakdown(attribute: str) -> dict[str, Any]:
        groups: dict[str, list[int]] = defaultdict(list)
        for i, s in enumerate(samples):
            groups[getattr(s, attribute)].append(i)
        return {
            name: {
                "count": len(idx),
                "rules_accuracy": _accuracy([rule_pairs[i] for i in idx]),
                "combined_accuracy": _accuracy([combined[i] for i in idx]),
                "laya_abstention": round(
                    sum(1 for i in idx if made[i].label is None) / len(idx), 4
                ),
            }
            for name, idx in sorted(groups.items())
        }

    abstentions = Counter(m.abstained for m in made if m.label is None)
    laya_available = any(m.abstained != "off" for m in made)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "samples": len(samples),
        "laya_configured": laya_available,
        "laya_model": constants.LAYA_MODEL if laya_available else None,
        "rules": {
            "accuracy": _accuracy(rule_pairs),
            "per_kind": _per_kind(rule_pairs, kinds),
            "mean_ms": round(rules_ms, 3),
        },
        "laya": {
            "answered_accuracy": _accuracy(answered),
            "abstention": round(sum(abstentions.values()) / len(samples), 4)
            if samples
            else None,
            "abstained_by_reason": dict(abstentions),
            "truncated": sum(1 for m in made if m.truncated),
            "per_kind": _per_kind(laya_pairs, kinds),
            "calibration": _calibration(
                [
                    (float(m.confidence), m.label == s.expected)
                    for s, m in zip(samples, made)
                    if m.confidence is not None and m.label is not None
                ]
            ),
            "latency_ms": {
                "p50": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
                "n": len(latencies),
            },
        },
        "combined": {"accuracy": _accuracy(combined)},
        "by_category": breakdown("category"),
        "by_language": breakdown("language"),
    }
    report["verdict"] = verdict(report)
    return report


def verdict(report: dict[str, Any]) -> dict[str, Any]:
    """Whether Laya may leave shadow, and every reason it may not."""
    reasons: list[str] = []
    if not report.get("laya_configured"):
        reasons.append("No decision model was reachable; nothing was measured.")
        return {"promote": False, "reasons": reasons}
    rules_acc = report["rules"]["accuracy"] or 0.0
    combined_acc = report["combined"]["accuracy"] or 0.0
    if combined_acc < rules_acc + PROMOTION_MARGIN:
        reasons.append(
            f"Laya with rules as fallback scores {combined_acc:.2%} against the "
            f"rules' {rules_acc:.2%}; it must beat them by {PROMOTION_MARGIN:.0%}."
        )
    abstention = report["laya"]["abstention"] or 0.0
    if abstention > MAX_ABSTENTION:
        reasons.append(
            f"Abstains on {abstention:.0%} of samples (limit {MAX_ABSTENTION:.0%})."
        )
    p95 = report["laya"]["latency_ms"]["p95"]
    if p95 is None or p95 > constants.LAYA_HARD_DEADLINE_MS:
        reasons.append(
            f"p95 latency {p95} ms is over the {constants.LAYA_HARD_DEADLINE_MS} ms deadline."
        )
    for group in ("by_category", "by_language"):
        for name, row in report[group].items():
            if (row["combined_accuracy"] or 0) < (row["rules_accuracy"] or 0):
                reasons.append(f"Regresses on {group[3:]} '{name}'.")
    if report["samples"] < MIN_SAMPLES:
        reasons.append(
            f"Only {report['samples']} labelled samples; the handoff asks for at least "
            "300 reviewed ones before claiming support. Treat the numbers as indicative."
        )
    return {"promote": not reasons, "reasons": reasons}


def render_markdown(report: dict[str, Any]) -> str:
    """The report as a page an operator can read and attach as evidence."""
    lines = [
        "# Laya routing evaluation",
        "",
        f"Generated {report['generated_at']} over {report['samples']} labelled samples.",
        "",
        f"**Verdict: {'promote' if report['verdict']['promote'] else 'stay in shadow'}.**",
    ]
    for reason in report["verdict"]["reasons"]:
        lines.append(f"- {reason}")
    laya = report["laya"]
    lines += [
        "",
        "| | Rules | Laya (answered) | Laya + rules fallback |",
        "|---|---|---|---|",
        f"| Accuracy | {report['rules']['accuracy']} | {laya['answered_accuracy']} | "
        f"{report['combined']['accuracy']} |",
        "",
        f"Abstention {laya['abstention']} ({laya['abstained_by_reason']}); "
        f"truncated {laya['truncated']}; latency p50 {laya['latency_ms']['p50']} ms, "
        f"p95 {laya['latency_ms']['p95']} ms (n={laya['latency_ms']['n']}); "
        f"calibration error {laya['calibration']['ece']}.",
        "",
        "## Per kind",
        "",
        "| Kind | Rules P | Rules R | Laya P | Laya R | Laya FP | Laya FN |",
        "|---|---|---|---|---|---|---|",
    ]
    for kind, rules_row in report["rules"]["per_kind"].items():
        laya_row = laya["per_kind"][kind]
        lines.append(
            f"| {kind} | {rules_row['precision']} | {rules_row['recall']} | "
            f"{laya_row['precision']} | {laya_row['recall']} | "
            f"{laya_row['false_positive']} | {laya_row['false_negative']} |"
        )
    for title, key in (("Category", "by_category"), ("Language", "by_language")):
        lines += [
            "",
            f"## By {title.lower()}",
            "",
            f"| {title} | n | Rules | Combined | Laya abstains |",
            "|---|---|---|---|---|",
        ]
        for name, row in report[key].items():
            lines.append(
                f"| {name} | {row['count']} | {row['rules_accuracy']} | "
                f"{row['combined_accuracy']} | {row['laya_abstention']} |"
            )
    return "\n".join(lines) + "\n"
