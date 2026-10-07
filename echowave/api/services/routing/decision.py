"""Narrow judgments from a decision model, behind one small interface.

The product handoff asks for a provider-neutral DecisionService: a typed
question with a fixed set of answers, a deadline, and an answer that may be
"I don't know". Laya (convaiinnovations/laya, self-hosted, ``laya-serve``)
is the first backend; Jev speaks the same ``/v1/systemone`` shape, so
pointing ``LAYA_URL`` at it is the whole switch.

What this never is: permission. A decision picks among options the caller
already allowed. Anything that comes back malformed, late, or unsure is an
abstention, and the caller's own rule decides.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from loguru import logger

from api import constants

#: What one forward pass is sent; longer text is cut and the Decision says so.
MAX_TEXT_CHARS = 4000


@dataclass(frozen=True)
class Decision:
    #: One of the labels asked about, or None when the model abstained.
    label: str | None
    confidence: float | None
    elapsed_ms: int
    #: Why there is no label, when there is none: off, timeout, error,
    #: malformed, low_confidence (and, from services/ops/laya_eval:
    #: rolled_back, circuit_open, deadline).
    abstained: str | None = None
    #: Whether ``text`` was cut to fit what one forward pass reads. A label
    #: about a truncated message is a label about part of it.
    truncated: bool = False


async def choose(
    question: str,
    labels: dict[str, str],
    text: str,
    *,
    timeout_ms: int | None = None,
    min_confidence: float | None = None,
) -> Decision:
    """Ask the decision model to pick one of ``labels`` for ``text``.

    ``labels`` maps each answer to what it means; only those answers are
    accepted back. Only ``text`` is sent -- never history, secrets or a
    whole inbox -- trimmed to what one forward pass reads.
    """
    started = time.monotonic()
    truncated = len(text or "") > MAX_TEXT_CHARS

    def done(label=None, confidence=None, abstained=None) -> Decision:
        return Decision(
            label=label,
            confidence=confidence,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            abstained=abstained,
            truncated=truncated,
        )

    if not constants.LAYA_URL:
        return done(abstained="off")

    body = {
        "state": {"body": (text or "")[:MAX_TEXT_CHARS]},
        "questions": {
            "decision": {
                "type": "choice",
                "instructions": question,
                "criteria": labels,
            }
        },
        "model": constants.LAYA_MODEL,
        "max_len": 1024,
    }
    headers = (
        {"Authorization": f"Bearer {constants.LAYA_API_KEY}"}
        if constants.LAYA_API_KEY
        else {}
    )
    timeout = (timeout_ms or constants.LAYA_TIMEOUT_MS) / 1000
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{constants.LAYA_URL}/v1/systemone", json=body, headers=headers
            )
        response.raise_for_status()
        answer = (response.json().get("answers") or {}).get("decision") or {}
    except httpx.TimeoutException:
        return done(abstained="timeout")
    except Exception as exc:  # noqa: BLE001 -- a decision must never break a reply
        logger.warning("Decision model unavailable: {}", exc)
        return done(abstained="error")

    label = answer.get("choice")
    confidence = answer.get("confidence")
    if label not in labels or not isinstance(confidence, (int, float)):
        return done(abstained="malformed")
    floor = constants.LAYA_MIN_CONFIDENCE if min_confidence is None else min_confidence
    if answer.get("low_confidence") or confidence < floor:
        return done(confidence=float(confidence), abstained="low_confidence")
    return done(label=label, confidence=float(confidence))
