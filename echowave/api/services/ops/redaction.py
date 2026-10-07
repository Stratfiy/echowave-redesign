"""What may leave the application, and in what shape (handoff 35).

The privacy rule is short: no prompts, transcripts, audio, email bodies,
keys, KYC documents or card details in analytics, logs or replay, and
normalized reason codes in place of raw error strings. Masking is a
baseline, not proof, so this module does two different things:

* **Properties are allowlisted by shape and blocklisted by name.** An
  analytics property must be a scalar (str, int, float, bool, None) under a
  name that is not on the sensitive list; anything else is dropped and the
  drop is counted in ``_redacted`` so a missing property can be noticed
  rather than silently absent. String values are capped and scrubbed.
* **Free text is scrubbed** of the shapes secrets and personal data usually
  have -- the same rules as ``scripts/ops/redact.sed`` on the ops side, plus
  email addresses and phone numbers.

``reason_code`` turns any exception or vendor message into one of a fixed
vocabulary, so a dashboard groups failures without carrying the text of the
request that failed.
"""

from __future__ import annotations

import re
from typing import Any

from api.utils.phone_masking import mask_phone_numbers

#: Property names whose values are content, never metadata. Matched against
#: the lower-cased name and every ``_``-separated part of it, so
#: ``email_body`` and ``user_prompt_text`` are both caught.
SENSITIVE_NAMES = frozenset(
    {
        "prompt",
        "prompts",
        "transcript",
        "transcripts",
        "audio",
        "recording",
        "body",
        "content",
        "message",
        "messages",
        "text",
        "html",
        "subject",
        "email",
        "phone",
        "password",
        "passwd",
        "secret",
        "token",
        "key",
        "apikey",
        "authorization",
        "cookie",
        "kyc",
        "aadhaar",
        "pan",
        "passport",
        "document",
        "card",
        "cvv",
        "otp",
        "iban",
        "account",
        "address",
        "name",
        "query",
        "url",
    }
)

#: Names that look sensitive by the rule above but are metadata we want.
#: Kept explicit so the exception is reviewable.
ALLOWED_NAMES = frozenset(
    {
        "event_id",
        "schema_version",
        "occurred_at",
        "environment",
        "release",
        "user_id",
        "workspace_id",
        "task_id",
        "trace_id",
        "configuration_version",
        "channel",
        "language",
        "agent_type",
        "status",
        "reason_code",
        "duration_ms",
        "provider_name",
        "message_count",
        "key_count",
        "text_length",
        "content_type",
        "account_type",
    }
)

MAX_STRING = 120
REDACTED = "[redacted]"

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://user:password@host
    (
        re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://[^:/@\s]*):[^@\s]+@"),
        r"\1:[redacted]@",
    ),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.I), r"\1[redacted]"),
    (re.compile(r"(Basic\s+)[A-Za-z0-9+/=]{8,}", re.I), r"\1[redacted]"),
    (
        re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"),
        "[jwt-redacted]",
    ),
    (re.compile(r"xox[abposr]-[A-Za-z0-9-]+"), "xox*-[redacted]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "sk-[redacted]"),
    (re.compile(r"rzp_(live|test)_[A-Za-z0-9]{8,}"), r"rzp_\1_[redacted]"),
    (re.compile(r"(AKIA|ASIA)[0-9A-Z]{16}"), r"\1[redacted]"),
    (
        re.compile(
            r"((?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
            r"private[_-]?key|client[_-]?secret|auth[_-]?token|\bkey)[\"']?\s*[=:]\s*[\"']?)"
            r"[^\"'&\s,;}]+",
            re.I,
        ),
        r"\1[redacted]",
    ),
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+_-]{32,}={0,2}")


def _long_token(match: re.Match[str]) -> str:
    value = match.group(0)
    # Long identifiers in a traceback are letters only; a token has digits.
    if not any(ch.isdigit() for ch in value):
        return value
    return "[token-redacted]"


def redact_text(text: str | None) -> str | None:
    """Scrub secrets, emails and phone numbers out of free text."""
    if not text:
        return text
    out = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    out = _EMAIL.sub("[email-redacted]", out)
    out = mask_phone_numbers(out)
    # UUIDs are what an operator greps for: protect them from the long-token
    # rule by swapping their hyphens out and back.
    out = _UUID.sub(lambda m: m.group(0).replace("-", "\x03"), out)
    out = _LONG_TOKEN.sub(_long_token, out)
    return out.replace("\x03", "-")


def is_sensitive_name(name: str) -> bool:
    lowered = (name or "").strip().lower()
    if lowered in ALLOWED_NAMES:
        return False
    if lowered in SENSITIVE_NAMES:
        return True
    parts = re.split(r"[_\-.\s]+", lowered)
    return any(part in SENSITIVE_NAMES for part in parts)


def redact_properties(properties: dict[str, Any] | None) -> dict[str, Any]:
    """The properties that may be sent, plus ``_redacted``: the names that
    were dropped, so a missing property is visible rather than absent."""
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    for name, value in (properties or {}).items():
        if not isinstance(name, str) or is_sensitive_name(name):
            dropped.append(str(name))
            continue
        if value is None or isinstance(value, (bool, int, float)):
            kept[name] = value
        elif isinstance(value, str):
            kept[name] = redact_text(value[:MAX_STRING])
        else:
            # Lists and dicts are where content hides; metadata is scalar.
            dropped.append(name)
    if dropped:
        kept["_redacted"] = sorted(dropped)
    return kept


#: The fixed vocabulary of failure reasons. Anything else is ``other``.
REASON_CODES = (
    "timeout",
    "rate_limited",
    "unauthorized",
    "forbidden",
    "not_found",
    "conflict",
    "invalid_input",
    "provider_unavailable",
    "network",
    "quota_exceeded",
    "cost_stopped",
    "cancelled",
    "malformed",
    "not_configured",
    "other",
)

_REASON_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"time[d ]?\s*out|deadline", re.I), "timeout"),
    (re.compile(r"\b429\b|rate.?limit|too many requests", re.I), "rate_limited"),
    (re.compile(r"\b401\b|unauthori[sz]ed|invalid api key|auth", re.I), "unauthorized"),
    (re.compile(r"\b403\b|forbidden|permission", re.I), "forbidden"),
    (re.compile(r"\b404\b|not found", re.I), "not_found"),
    (re.compile(r"\b409\b|conflict|already exists", re.I), "conflict"),
    (re.compile(r"\b(400|422)\b|invalid|validation", re.I), "invalid_input"),
    (
        re.compile(r"\b50[0234]\b|unavailable|server error", re.I),
        "provider_unavailable",
    ),
    (re.compile(r"connect|dns|network|unreachable|reset by peer", re.I), "network"),
    (re.compile(r"quota|insufficient|credit", re.I), "quota_exceeded"),
    (re.compile(r"cancel", re.I), "cancelled"),
    (re.compile(r"malformed|decode|json|parse", re.I), "malformed"),
    (re.compile(r"not configured|not set|missing", re.I), "not_configured"),
)


def reason_code(error: BaseException | str | None) -> str:
    """One word from ``REASON_CODES`` for any error or message."""
    if error is None:
        return "other"
    if isinstance(error, BaseException):
        name = type(error).__name__
        if "Timeout" in name:
            return "timeout"
        if "Cancelled" in name:
            return "cancelled"
        text = f"{name} {error}"
    else:
        text = str(error)
    if text in REASON_CODES:
        return text
    for pattern, code in _REASON_RULES:
        if pattern.search(text):
            return code
    return "other"


def redact_record(record: dict[str, Any]) -> None:
    """loguru patcher: scrub every message while ``telemetry_redaction`` is on.

    Reads the flag per record (a dict lookup), so switching it from the
    console takes effect without a restart. Never raises.
    """
    try:
        from api.services import features

        if not features.is_on("telemetry_redaction"):
            return
        record["message"] = redact_text(record["message"])
    except Exception:  # noqa: BLE001 - masking must never break logging
        pass


def scrub_sentry_event(event: dict[str, Any]) -> dict[str, Any]:
    """The deep half of the Sentry scrub: messages, exception values,
    breadcrumbs and extras. Called by ``utils.sentry_scrub`` while
    ``telemetry_redaction`` is on."""
    if isinstance(event.get("message"), str):
        event["message"] = redact_text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        if isinstance(logentry.get("message"), str):
            logentry["message"] = redact_text(logentry["message"])
        if "params" in logentry:
            logentry["params"] = REDACTED
    for exc in (event.get("exception") or {}).get("values") or []:
        if isinstance(exc, dict) and isinstance(exc.get("value"), str):
            exc["value"] = redact_text(exc["value"])
        # Local variables in frames carry whatever the function held -- a
        # prompt, a key. The stack trace is the useful part.
        for frame in ((exc or {}).get("stacktrace") or {}).get("frames") or []:
            if isinstance(frame, dict) and "vars" in frame:
                frame["vars"] = {}
    crumbs = event.get("breadcrumbs")
    values = crumbs.get("values") if isinstance(crumbs, dict) else crumbs
    for crumb in values or []:
        if not isinstance(crumb, dict):
            continue
        if isinstance(crumb.get("message"), str):
            crumb["message"] = redact_text(crumb["message"])
        if isinstance(crumb.get("data"), dict):
            crumb["data"] = redact_properties(crumb["data"])
    extra = event.get("extra")
    if isinstance(extra, dict):
        event["extra"] = redact_properties(extra)
    return event


__all__ = [
    "REASON_CODES",
    "is_sensitive_name",
    "reason_code",
    "redact_properties",
    "redact_record",
    "redact_text",
    "scrub_sentry_event",
]
