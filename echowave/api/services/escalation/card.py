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
hold matters, and a copy taken at transfer time cannot show it. The link opens
the call's run page in live mode (``?live=1``), which refreshes the card's
``live_transcript`` -- the last few lines, kept current by the runtime while
the escalation is open -- until the call ends and the full transcript is
there. (The live-supervision listen panel is not on main yet; when it is,
this link can point at it instead.)

**The person is briefed in their own language.** Each number on the policy
carries a briefing language (English unless set); the card holds one
briefing per language its people need, and each person hears theirs.

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
#: Lines of the live transcript kept on the card for the live view.
MAX_LIVE_LINES = 12
SUMMARY_TIMEOUT_SECONDS = 3.0

#: The briefing's fixed words, per language. A language listed in
#: ``policy.BRIEFING_LANGUAGES`` must have an entry here (a test checks).
_BRIEFING_WORDS: dict[str, dict[str, str]] = {
    "en": {"caller": "A caller", "verified": "verified"},
    "hi": {"caller": "एक कॉलर", "verified": "सत्यापित"},
}
_REASONS_HI: dict[str, str] = {
    "explicit_request": "किसी व्यक्ति से बात करना चाहते हैं",
    "policy": "यह विषय हमेशा किसी व्यक्ति के पास जाता है",
    "repair_loop": "एजेंट एक चरण से आगे नहीं बढ़ पाया",
    "low_confidence": "एजेंट को ज़रूरी जानकारी पर भरोसा नहीं था",
    "frustration": "कॉलर परेशान हो रहे थे",
}
_LANGUAGE_NAMES = {"en": "English", "hi": "Hindi"}
#: The model's own escalation tools are not "actions taken".
OWN_TOOLS = frozenset({"report_escalation_signal", "escalation_fallback"})

Summariser = Callable[[str], Awaitable[str | None]]
#: (transcript, language code) -> two sentences in that language.
LanguageSummariser = Callable[[str, str], Awaitable[str | None]]


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


def fallback_summary(
    intent: str | None, reason: str | None, caller: str, language: str = "en"
) -> str:
    if language == "hi":
        want = (
            f"{caller} को {intent} में मदद चाहिए।"
            if intent
            else f"{caller} को मदद चाहिए।"
        )
        return f"{want} {_reason_in(reason, 'hi')}।"
    want = f"{caller} wants help with {intent}." if intent else f"{caller} needs help."
    return f"{want} {reason_label(reason)}."


def _reason_in(reason: str | None, language: str) -> str:
    if language == "hi" and reason in _REASONS_HI:
        return _REASONS_HI[reason]
    return reason_label(reason)


def live_url(
    workflow_id: int | None, workflow_run_id: int | None, escalation_uuid: str
) -> str | None:
    """The call's run page in live mode, following this escalation."""
    if not (workflow_id and workflow_run_id):
        return None
    return (
        f"/workflow/{workflow_id}/run/{workflow_run_id}"
        f"?live=1&escalation={escalation_uuid}"
    )


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
    team: str | None = None,
    briefing_languages: Iterable[str] = (),
    summarise_in: LanguageSummariser | None = None,
) -> dict[str, Any]:
    messages = list(messages or [])
    transcript = transcript_lines(messages)
    caller = caller_identity(call_context, gathered)
    intent = intent_of(gathered, transcript)
    others = [
        code
        for code in dict.fromkeys(briefing_languages)
        if code != "en" and code in _BRIEFING_WORDS
    ]
    for code in briefing_languages:
        if code not in _BRIEFING_WORDS:
            logger.warning("No briefing words for {!r}; briefing in English", code)

    async def _bounded(coroutine) -> str | None:
        try:
            return await asyncio.wait_for(coroutine, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - a card is never held up
            logger.info("Handoff summary not written in time: {}", exc)
            return None

    joined = "\n".join(transcript)
    jobs: dict[str, Any] = {}
    if summarise is not None and transcript:
        jobs["en"] = _bounded(summarise(joined))
    if summarise_in is not None and transcript:
        for code in others:
            jobs[code] = _bounded(summarise_in(joined, code))
    written = dict(zip(jobs, await asyncio.gather(*jobs.values()))) if jobs else {}
    name = caller["name"]
    summary = two_sentences(written.get("en") or "") or fallback_summary(
        intent, reason_code, name or "The caller"
    )
    card: dict[str, Any] = {
        "escalation_uuid": escalation_uuid,
        "caller": caller,
        "intent": intent,
        "team": team,
        "fields": extracted_fields(gathered),
        "actions": actions_taken(messages),
        "reason_code": reason_code,
        "reason": reason_label(reason_code),
        "reason_detail": reason_detail,
        "summary": summary,
        "language": language,
        "consent": dict(consent or {}),
        "transcript_url": live_url(workflow_id, workflow_run_id, escalation_uuid),
        "live_transcript": transcript[-MAX_LIVE_LINES:],
        "workflow_id": workflow_id,
        "workflow_run_id": workflow_run_id,
        "created_at": datetime.now(UTC).isoformat(),
    }
    summaries = {"en": summary}
    for code in others:
        summaries[code] = two_sentences(written.get(code) or "") or fallback_summary(
            intent, reason_code, name or _BRIEFING_WORDS[code]["caller"], code
        )
    card["briefings"] = {
        code: briefing_in(card, code, summary=text) for code, text in summaries.items()
    }
    return card


def briefing_in(
    card: Mapping[str, Any], language: str = "en", *, summary: str | None = None
) -> str:
    """The briefing in one language: team, who, whether verified, why, then
    the summary -- cut to the briefing cap at a sentence."""
    from api.services.telephony.escalation import _truncate

    words = _BRIEFING_WORDS.get(language) or _BRIEFING_WORDS["en"]
    caller = card.get("caller") or {}
    who = caller.get("name") or words["caller"]
    verified = f" ({words['verified']})" if caller.get("verified") else ""
    if language == "en":
        reason = str(card.get("reason") or "").rstrip(".")
    else:
        reason = _reason_in(card.get("reason_code"), language)
    stop = "।" if language == "hi" else "."
    head = (
        f"{who}{verified}{stop} {reason}{stop}" if reason else f"{who}{verified}{stop}"
    )
    team = card.get("team")
    if team:
        head = f"{team}: {head}"
    text = f"{head} {summary if summary is not None else card.get('summary') or ''}"
    return _truncate(" ".join(text.split()))[:MAX_BRIEFING_CHARS]


def spoken_briefing(card: Mapping[str, Any], language: str = "en") -> str:
    """The card as the person hears it before the caller is put through.

    In the person's language when the card holds a briefing in it, else in
    English. Spoken to the person only.
    """
    briefings = card.get("briefings") or {}
    if isinstance(briefings, Mapping) and briefings.get(language):
        return str(briefings[language])
    return briefing_in(card, "en")


def summary_prompt(transcript: str, language: str | None = None) -> str:
    written_in = (
        f" Write it in {_LANGUAGE_NAMES.get(language, language)}."
        if language and language != "en"
        else ""
    )
    return (
        "Summarise this phone call for the colleague about to take it over, "
        "in exactly two short sentences: what the caller wants, and where "
        "things stand. No greeting, no lists, no guesses beyond the "
        f"transcript.{written_in}\n\n" + transcript
    )


__all__ = [
    "INTERNAL_KEYS",
    "actions_taken",
    "briefing_in",
    "build",
    "caller_identity",
    "extracted_fields",
    "fallback_summary",
    "live_url",
    "spoken_briefing",
    "summary_prompt",
    "transcript_lines",
    "two_sentences",
]
