"""The handoff card: everything a person needs so the caller never repeats
themselves.

Structured rather than a paragraph, because a person picking up a phone reads
fields, not prose: who it is and whether they are verified (so nobody asks for
the date of birth twice), what they want, what was captured, what the agent
already did and how that went, why it is coming to a person, in two
sentences, in which language, and what they agreed to.

Two of the fields deserve a word.

**The summary is a separate model call**, made at transfer time with a short
timeout. The rest of the card is copied from what the call already holds and
cannot be wrong; the summary is the one paraphrase, so it is bounded to two
sentences and, if the model is slow or fails, replaced by a sentence built
from the fields. A card is never held up for its summary.

**The transcript is a link, not a snapshot.** What the caller says while on
hold matters, and a copy taken at transfer time cannot show it.

Delivered twice: spoken as the private briefing on the person's leg (the
existing path in ``telephony/escalation.py``) and posted on the agent's thread
for whoever opens the app.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Iterable, Mapping

from loguru import logger

from api.services.escalation import reason_label
from api.services.telephony.escalation import MAX_BRIEFING_CHARS
from api.utils.phone_masking import last_four

#: Keys on a call's gathered context that are the platform's bookkeeping,
#: not something the caller said. Blocklist, not allowlist: a field nobody
#: listed still reaches the card (api/AGENTS.md, silent absence).
INTERNAL_KEYS = frozenset(
    {
        "call_id",
        "call_disposition",
        "mapped_call_disposition",
        "call_tags",
        "escalation",
        "caller_verified",
        "verified",
    }
)
MAX_FIELDS = 20
MAX_VALUE_CHARS = 160
MAX_ACTIONS = 10
MAX_TRANSCRIPT_TURNS = 30
SUMMARY_TIMEOUT_SECONDS = 3.0
#: The model's own escalation tools are not "actions taken".
OWN_TOOLS = frozenset({"report_escalation_signal", "escalation_fallback"})

Summariser = Callable[[str], Awaitable[str | None]]


def _clip(value: Any, limit: int = MAX_VALUE_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def extracted_fields(gathered: Mapping[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (gathered or {}).items():
        if key in INTERNAL_KEYS or str(key).startswith("_") or value in (None, ""):
            continue
        out[str(key)] = _clip(value)
        if len(out) >= MAX_FIELDS:
            break
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts)
    return ""


def actions_taken(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each tool the agent called on this call, and whether it worked.

    Read from the conversation itself (OpenAI-shaped tool calls and their
    results), so it covers every kind of tool without each one reporting in.
    """
    calls: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        for call in message.get("tool_calls") or []:
            function = (call or {}).get("function") or {}
            name = str(function.get("name") or "")
            if not name or name in OWN_TOOLS:
                continue
            call_id = str(call.get("id") or len(order))
            calls[call_id] = {"tool": name, "ok": None, "result": ""}
            order.append(call_id)
        if message.get("role") == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if call_id in calls:
                text = _text_of(message.get("content"))
                calls[call_id]["result"] = _clip(text, 120)
                calls[call_id]["ok"] = _looks_ok(text)
    return [calls[c] for c in order][-MAX_ACTIONS:]


_FAILED = re.compile(
    r'"(status|result)"\s*:\s*"(failed|error|not_[a-z_]+)"|"error"\s*:'
)


def _looks_ok(text: str) -> bool:
    return not _FAILED.search(text or "")


def transcript_lines(messages: Iterable[Mapping[str, Any]]) -> list[str]:
    lines = []
    for message in messages:
        if not isinstance(message, Mapping):
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _text_of(message.get("content")).strip()
        # Instructions the platform appended as user turns are not the caller.
        if not text or text.startswith("[") or text.startswith("The user has"):
            continue
        lines.append(f"{'Caller' if role == 'user' else 'Agent'}: {text}")
    return lines[-MAX_TRANSCRIPT_TURNS:]


def two_sentences(text: str) -> str:
    text = " ".join((text or "").split())
    sentences = re.split(r"(?<=[.!?।])\s+", text)
    return " ".join(sentences[:2]).strip()


def fallback_summary(intent: str | None, reason: str | None, caller: str) -> str:
    want = f"{caller} wants help with {intent}." if intent else f"{caller} needs help."
    return f"{want} {reason_label(reason)}."


def caller_identity(
    call_context: Mapping[str, Any] | None, gathered: Mapping[str, Any] | None
) -> dict[str, Any]:
    call_context = call_context or {}
    gathered = gathered or {}
    number = (
        call_context.get("caller_number")
        or call_context.get("phone_number")
        or call_context.get("to_number")
    )
    name = None
    for key in ("caller_name", "customer_name", "contact_name", "name", "full_name"):
        value = gathered.get(key) or call_context.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip()[:80]
            break
    verified = bool(gathered.get("caller_verified") or gathered.get("verified"))
    known = bool(call_context.get("contact_is_known"))
    return {
        "name": name,
        "number": number,
        "number_masked": last_four(str(number)) if number else None,
        "verified": verified,
        "verification": "verified"
        if verified
        else ("known number, not verified" if known else "not verified"),
    }


def intent_of(gathered: Mapping[str, Any] | None, transcript: list[str]) -> str | None:
    gathered = gathered or {}
    for key in ("intent", "caller_intent", "reason", "purpose", "issue", "query"):
        value = gathered.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value, 120)
    for line in reversed(transcript):
        if line.startswith("Caller: "):
            return _clip(line[len("Caller: ") :], 120)
    return None


async def build(
    *,
    escalation_uuid: str,
    workflow_id: int | None,
    workflow_run_id: int | None,
    reason_code: str,
    reason_detail: str,
    call_context: Mapping[str, Any] | None,
    gathered: Mapping[str, Any] | None,
    messages: Iterable[Mapping[str, Any]],
    language: str | None,
    consent: Mapping[str, Any] | None,
    summarise: Summariser | None = None,
    timeout: float = SUMMARY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    messages = list(messages or [])
    transcript = transcript_lines(messages)
    caller = caller_identity(call_context, gathered)
    intent = intent_of(gathered, transcript)
    summary = None
    if summarise is not None and transcript:
        try:
            summary = await asyncio.wait_for(
                summarise("\n".join(transcript)), timeout=timeout
            )
        except Exception as exc:  # noqa: BLE001 - a card is never held up
            logger.info("Handoff summary not written in time: {}", exc)
    summary = two_sentences(summary or "") or fallback_summary(
        intent, reason_code, caller["name"] or "The caller"
    )
    return {
        "escalation_uuid": escalation_uuid,
        "caller": caller,
        "intent": intent,
        "fields": extracted_fields(gathered),
        "actions": actions_taken(messages),
        "reason_code": reason_code,
        "reason": reason_label(reason_code),
        "reason_detail": reason_detail,
        "summary": summary,
        "language": language,
        "consent": dict(consent or {}),
        "transcript_url": f"/workflow/{workflow_id}/run/{workflow_run_id}"
        if workflow_id and workflow_run_id
        else None,
        "workflow_id": workflow_id,
        "workflow_run_id": workflow_run_id,
        "created_at": datetime.now(UTC).isoformat(),
    }


def spoken_briefing(card: Mapping[str, Any]) -> str:
    """The card as the person hears it before the caller is put through.

    Who, whether verified, why, then the summary -- cut to the briefing cap
    at a sentence. Spoken to the person only.
    """
    from api.services.telephony.escalation import _truncate

    caller = card.get("caller") or {}
    who = caller.get("name") or "A caller"
    verified = " (verified)" if caller.get("verified") else ""
    reason = str(card.get("reason") or "").rstrip(".")
    head = f"{who}{verified}. {reason}." if reason else f"{who}{verified}."
    text = f"{head} {card.get('summary') or ''}".strip()
    return _truncate(" ".join(text.split()))[:MAX_BRIEFING_CHARS]


def summary_prompt(transcript: str) -> str:
    return (
        "Summarise this phone call for the colleague about to take it over, "
        "in exactly two short sentences: what the caller wants, and where "
        "things stand. No greeting, no lists, no guesses beyond the "
        "transcript.\n\n" + transcript
    )


__all__ = [
    "INTERNAL_KEYS",
    "actions_taken",
    "build",
    "caller_identity",
    "extracted_fields",
    "fallback_summary",
    "spoken_briefing",
    "summary_prompt",
    "transcript_lines",
    "two_sentences",
]
