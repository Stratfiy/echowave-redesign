"""Triggers: a sentence becomes a doorbell, and the doorbell starts a run.

The operator does not write a webhook. They write "when a Shopify order over
Rs 5,000 comes in, check stock and message the customer", and this module
turns that into what the machinery needs: a name, the fields the event
must carry, a filter over them, and the instruction the bot is handed each
time. That compile runs on the builder model -- the same key the agent
builder uses -- because the alternative is a form with a field picker and a
condition editor, which is n8n, which is the thing this product exists to
not be.

Three rules, in order of how much they cost when wrong:

**Anything unsaid is asked, not guessed and not errored.** The user's own
words: "if any field is missed that api or webhook needs AI should ask and
fill rather than getting error." At setup the compile returns *questions*
when the sentence leaves out something it needs (which field carries the
amount? what counts as a big order?), and nothing is saved until they are
answered. At run time an event missing a required field is still run: the
bot is told what is missing and may ask the team on a decision card. A
webhook that answers 400 is a webhook whose sender quietly stops retrying.

**The filter is evaluated before anything is charged.** An event the
operator did not ask about -- an order under the threshold -- costs nothing
and starts nothing. The public route does this check itself, before the
job is queued.

**No model is not an error either.** A deployment without a builder key
still gets a trigger: the sentence becomes the instruction verbatim, with
no filter and no fields, and the response says so. Worse than the
compiled version; better than a feature that is switched off.
"""

from __future__ import annotations

import json
import secrets
import uuid as uuid_module
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from loguru import logger

from api.constants import (
    BACKEND_API_ENDPOINT,
    INBOUND_EMAIL_DOMAIN,
)
from api.services.agent_builder import client as builder_client
from api.services.agent_builder.client import Conversation
from api.services.agent_builder.settings import BuilderUnavailable, resolve_model

SOURCE_WEBHOOK = "webhook"
#: An address per bot (KAN-138): mail to it fires the bot, the mail is the
#: payload. Same run, filter, dedupe and price as a webhook event.
SOURCE_EMAIL = "email"
SOURCES = (SOURCE_WEBHOOK, SOURCE_EMAIL)

#: A bot with more doorbells than this is not a bot anybody can reason about.
MAX_PER_WORKFLOW = 10
MAX_FIELDS = 20
MAX_RULES = 10
MAX_QUESTIONS = 5
#: Text runs are cheap and events come in bursts (a CSV import fires one
#: per row), so the ceiling is well above the call trigger's twenty. It is
#: still a ceiling: a stuck sender must not spend the account's credits all
#: night.
MAX_TRIGGERS_PER_HOUR = 120
#: How much of the event the bot is shown. A Shopify order is a few
#: kilobytes; anything past this is somebody posting a file.
MAX_PAYLOAD_CHARS = 6_000

#: Comparisons a rule may make. Numbers compare as numbers when both sides
#: parse; otherwise as strings, case-insensitively.
OPS = ("eq", "ne", "contains", "gt", "gte", "lt", "lte", "exists", "missing")

COMPILE_TOOL_NAME = "describe_trigger"


def public_url(trigger_uuid: str) -> str:
    return f"{BACKEND_API_ENDPOINT}/api/v1/public/triggers/{trigger_uuid}"


def inbound_address(trigger_uuid: str) -> str:
    """The email address that fires this trigger (KAN-138)."""
    return f"{trigger_uuid}@{INBOUND_EMAIL_DOMAIN}"


def address_uuid(recipient: str) -> Optional[str]:
    """The trigger uuid inside a recipient address, or None.

    Handles a bare address, a display-name form ("Ops <uuid@dom>"), and
    plus-addressing ("uuid+tag@dom"). The local part before ``+`` is the
    uuid; the domain is not checked here because the uuid is already unique.
    """
    raw = (recipient or "").strip()
    if "<" in raw and ">" in raw:
        raw = raw[raw.rfind("<") + 1 : raw.rfind(">")]
    raw = raw.strip().strip("\"'")
    if "@" not in raw:
        return None
    local = raw.split("@", 1)[0].split("+", 1)[0].strip().lower()
    return local or None


def _bare_address(value: Any) -> str:
    """``"Meera <meera@shop.example>"`` -> ``meera@shop.example``, lowercased."""
    raw = str(value or "")
    if "<" in raw and ">" in raw:
        raw = raw[raw.rfind("<") + 1 : raw.rfind(">")]
    return raw.strip().lower()


def is_own_mail(sender: Any) -> bool:
    """Whether a message came from the platform itself, so must not fire a bot.

    A bot's reply to the address that triggered it, an auto-reply bouncing
    between two bots, a receipt from billing forwarded to a trigger: each is
    mail *we* sent arriving at an address *we* own, and a bot that acts on it
    acts on itself -- a loop that costs a credit a turn until the hourly cap.
    Dedupe by message id catches an exact redelivery, not a fresh reply. So
    anything from our inbound domain or one of our sending addresses is
    dropped before the filter, the counter and the queue.
    """
    from api import constants

    address = _bare_address(sender)
    if not address or "@" not in address:
        return False
    domain = address.rsplit("@", 1)[1]
    if domain == INBOUND_EMAIL_DOMAIN.lower():
        return True
    ours = {
        str(a).strip().lower()
        for a in (
            constants.EMAIL_FROM_ADDRESS,
            constants.EMAIL_FROM_BILLING,
            constants.EMAIL_FROM_NOTIFICATIONS,
        )
        if a
    }
    return address in ours


#: Where each provider keeps the fields we need. First hit wins.
_EMAIL_KEYS = {
    "recipient": ("recipient", "to", "To", "OriginalRecipient", "envelope_to"),
    "from": ("from", "sender", "From", "from_email"),
    "subject": ("subject", "Subject"),
    "text": ("text", "body-plain", "TextBody", "stripped-text", "plain"),
    "html": ("html", "body-html", "HtmlBody"),
    "message_id": ("message_id", "Message-Id", "MessageID", "message-id", "id"),
}


def normalise_email(data: Mapping[str, Any]) -> dict[str, Any]:
    """One shape from whatever the mail provider POSTed.

    Providers disagree on every field name (SendGrid, Postmark, Mailgun,
    SES→SNS, and our own normalised form). Rather than a parser per vendor,
    pull each field from a prioritised list of keys; the recipient is also
    read out of an ``envelope`` JSON blob when a provider only puts it there.
    """
    out: dict[str, Any] = {}
    for field_name, keys in _EMAIL_KEYS.items():
        for key in keys:
            if key in data and str(data.get(key) or "").strip():
                out[field_name] = data[key]
                break
    if "recipient" not in out:
        envelope = data.get("envelope")
        if isinstance(envelope, str):
            try:
                envelope = json.loads(envelope)
            except (TypeError, ValueError):
                envelope = None
        if isinstance(envelope, dict):
            to = envelope.get("to")
            out["recipient"] = to[0] if isinstance(to, list) and to else to
    # Keep the body bounded, as the webhook payload is.
    for key in ("text", "html"):
        if isinstance(out.get(key), str) and len(out[key]) > MAX_PAYLOAD_CHARS:
            out[key] = out[key][:MAX_PAYLOAD_CHARS] + "\n… (truncated)"
    return out


def new_uuid() -> str:
    return str(uuid_module.uuid4())


def new_secret() -> str:
    return secrets.token_urlsafe(24)


# --- the filter -------------------------------------------------------------


def lookup(payload: Any, path: str) -> tuple[bool, Any]:
    """``(found, value)`` for a dotted path like ``order.total``.

    Two values rather than a sentinel, so a field that is present and null
    is told apart from one that is absent -- ``exists`` needs the difference.
    """
    current = payload
    for part in str(path or "").split("."):
        if not part:
            return False, None
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return False, None
    return True, current


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _holds(op: str, found: bool, actual: Any, expected: Any) -> bool:
    if op == "exists":
        return found and actual is not None
    if op == "missing":
        return not found or actual is None
    if not found:
        return False
    a_num, e_num = _number(actual), _number(expected)
    if op in ("gt", "gte", "lt", "lte"):
        if a_num is None or e_num is None:
            return False
        return {
            "gt": a_num > e_num,
            "gte": a_num >= e_num,
            "lt": a_num < e_num,
            "lte": a_num <= e_num,
        }[op]
    a_text = str(actual).strip().lower()
    e_text = str(expected).strip().lower()
    if op == "eq":
        return (
            a_num == e_num
            if a_num is not None and e_num is not None
            else a_text == e_text
        )
    if op == "ne":
        return not _holds("eq", found, actual, expected)
    if op == "contains":
        if isinstance(actual, list):
            return any(str(item).strip().lower() == e_text for item in actual)
        return e_text in a_text
    logger.warning("Trigger rule uses an unknown op {!r}; treating as no match", op)
    return False


def matches(rules: Any, payload: Any) -> bool:
    """Every rule must hold. No rules means every event is wanted."""
    for rule in list(rules or []):
        if not isinstance(rule, dict):
            return False
        found, actual = lookup(payload, str(rule.get("field") or ""))
        if not _holds(str(rule.get("op") or "eq"), found, actual, rule.get("value")):
            return False
    return True


def missing_fields(fields: Any, payload: Any) -> list[str]:
    """Required fields the event did not carry, by name."""
    missing: list[str] = []
    for spec in list(fields or []):
        if not isinstance(spec, dict) or not spec.get("required"):
            continue
        name = str(spec.get("name") or "")
        found, value = lookup(payload, name)
        if not found or value in (None, ""):
            missing.append(name)
    return missing


def describe(rules: Any) -> str:
    """One line for the card: ``total > 5000 and status = paid``."""
    words = {
        "eq": "=",
        "ne": "≠",
        "contains": "contains",
        "gt": ">",
        "gte": "≥",
        "lt": "<",
        "lte": "≤",
    }
    parts = []
    for rule in list(rules or []):
        if not isinstance(rule, dict):
            continue
        op = str(rule.get("op") or "eq")
        fld = str(rule.get("field") or "?")
        if op == "exists":
            parts.append(f"{fld} is present")
        elif op == "missing":
            parts.append(f"{fld} is missing")
        else:
            parts.append(f"{fld} {words.get(op, op)} {rule.get('value')}")
    return " and ".join(parts) if parts else "every event"


# --- the compile ------------------------------------------------------------


@dataclass
class Compiled:
    """What a sentence became. ``questions`` non-empty means: not yet."""

    name: str
    instruction: str
    fields: list[dict[str, Any]] = field(default_factory=list)
    filter: list[dict[str, Any]] = field(default_factory=list)
    questions: list[dict[str, str]] = field(default_factory=list)
    #: Why the compile is what it is, for the screen. Empty when the model did it.
    note: str = ""

    @property
    def ready(self) -> bool:
        return not self.questions

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "instruction": self.instruction,
            "fields": self.fields,
            "filter": self.filter,
            "questions": self.questions,
            "ready": self.ready,
            "note": self.note,
        }


COMPILE_TOOL: dict[str, Any] = {
    "name": COMPILE_TOOL_NAME,
    "description": (
        "Describe the trigger the operator asked for. Fill every part you "
        "can from their sentence. Where the sentence leaves out something "
        "the trigger cannot work without -- which field carries a value the "
        "condition needs, what a threshold is, who to message -- put a "
        "question in `questions` instead of guessing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Two to six words. 'Big Shopify order', not 'Trigger 1'.",
            },
            "fields": {
                "type": "array",
                "description": (
                    "Fields the incoming event should carry, as the sender's "
                    "JSON keys (dotted for nested). Mark required only what "
                    "the job cannot be done without."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "required": {"type": "boolean"},
                    },
                    "required": ["name"],
                },
            },
            "filter": {
                "type": "array",
                "description": (
                    "Conditions that must all hold for the bot to act. Empty "
                    "means act on every event."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "op": {"type": "string", "enum": list(OPS)},
                        "value": {"type": "string"},
                    },
                    "required": ["field", "op"],
                },
            },
            "instruction": {
                "type": "string",
                "description": (
                    "What the bot should do each time, in the second person, "
                    "referring to event fields by name. The event's JSON is "
                    "shown to the bot alongside this."
                ),
            },
            "questions": {
                "type": "array",
                "description": (
                    "What you need answered before this trigger can be saved. "
                    "Empty when the sentence was enough."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {
                            "type": "string",
                            "description": "A short key for the answer, e.g. amount_field.",
                        },
                        "question": {"type": "string"},
                    },
                    "required": ["field", "question"],
                },
            },
        },
        "required": ["name", "instruction"],
    },
}

SYSTEM = (
    "You turn one sentence from a business operator into a webhook trigger "
    "for an AI co-worker. The operator is not technical. Call "
    f"`{COMPILE_TOOL_NAME}` exactly once. Prefer asking over guessing: a "
    "wrong filter silently ignores real events or charges for junk ones. "
    "Field names should be the keys a typical sender of that event uses "
    "(Shopify, Razorpay, Zoho, a Google Form, Zapier). When the operator's "
    "answers are given, use them and do not ask again."
)


def _plain(sentence: str, note: str) -> Compiled:
    """No model: the sentence is the instruction. Said, not hidden."""
    text = " ".join(str(sentence or "").split())
    name = text[:60].rstrip(" ,.;:") or "Trigger"
    return Compiled(name=name[:1].upper() + name[1:], instruction=text, note=note)


def _clean_fields(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(raw or [])[:MAX_FIELDS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "name": name[:80],
                "description": str(item.get("description") or "").strip()[:200],
                "required": bool(item.get("required")),
            }
        )
    return out


def _clean_rules(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Valid rules, and a question for each one that could not be understood.

    A rule that is dropped silently would widen the filter to every event,
    which costs the account credits for things it never asked about. So a
    bad rule becomes a question instead.
    """
    rules: list[dict[str, Any]] = []
    questions: list[dict[str, str]] = []
    for item in list(raw or [])[:MAX_RULES]:
        if not isinstance(item, dict):
            continue
        fld = str(item.get("field") or "").strip()
        op = str(item.get("op") or "").strip().lower()
        value = item.get("value")
        if not fld or op not in OPS:
            questions.append(
                {
                    "field": f"rule:{fld or 'unknown'}",
                    "question": (
                        f"How should '{fld or 'this'}' be checked? Say the "
                        "field, the comparison and the value."
                    ),
                }
            )
            continue
        rule: dict[str, Any] = {"field": fld[:80], "op": op}
        if op not in ("exists", "missing"):
            rule["value"] = "" if value is None else str(value)[:120]
        rules.append(rule)
    return rules, questions


def from_arguments(arguments: dict[str, Any], sentence: str) -> Compiled:
    """The model's tool call, made safe for storage."""
    base = _plain(sentence, "")
    name = str(arguments.get("name") or "").strip()[:120] or base.name
    instruction = str(arguments.get("instruction") or "").strip()[:4_000]
    rules, rule_questions = _clean_rules(arguments.get("filter"))
    questions: list[dict[str, str]] = []
    for item in list(arguments.get("questions") or []):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        questions.append(
            {
                "field": str(item.get("field") or f"q{len(questions) + 1}")[:40],
                "question": question[:300],
            }
        )
    questions = (questions + rule_questions)[:MAX_QUESTIONS]
    return Compiled(
        name=name,
        instruction=instruction or base.instruction,
        fields=_clean_fields(arguments.get("fields")),
        filter=rules,
        questions=questions,
    )


def _prompt(sentence: str, answers: dict[str, str] | None) -> str:
    lines = [f"Operator's sentence: {sentence.strip()}"]
    if answers:
        lines.append("")
        lines.append("Answers to your earlier questions:")
        for key, value in answers.items():
            if str(value).strip():
                lines.append(f"- {key}: {str(value).strip()}")
    return "\n".join(lines)


async def compile(
    sentence: str, *, answers: dict[str, str] | None = None, session: Any
) -> Compiled:
    """Turn a sentence into a trigger, or into the questions that stand in its way.

    Never raises. Every failure mode degrades to the plain compile with a
    note, because the operator has typed a sentence and the worst answer to
    that is an error page.
    """
    sentence = " ".join(str(sentence or "").split())
    if not sentence:
        return Compiled(
            name="Trigger",
            instruction="",
            questions=[
                {"field": "sentence", "question": "What should happen, and when?"}
            ],
        )
    try:
        model = await resolve_model(session)
    except BuilderUnavailable as exc:
        logger.info("Trigger compiled without a model: {}", exc)
        return _plain(
            sentence,
            "No builder model is set up, so the sentence is used as-is. "
            "The bot will act on every event.",
        )

    conversation = Conversation()
    conversation.add_user(_prompt(sentence, answers))
    try:
        reply = await builder_client.complete(
            provider=model.provider,
            model=model.model,
            api_key=model.api_key,
            system=SYSTEM,
            conversation=conversation,
            tools=[COMPILE_TOOL],
        )
    except builder_client.BuilderClientError as exc:
        logger.warning("Trigger compile failed on {}: {}", model.provider, exc)
        return _plain(
            sentence,
            "The model could not be reached, so the sentence is used as-is. "
            "The bot will act on every event.",
        )
    call = next((c for c in reply.tool_calls if c.name == COMPILE_TOOL_NAME), None)
    if call is None:
        return _plain(
            sentence,
            "The model answered in prose rather than a plan, so the sentence "
            "is used as-is.",
        )
    return from_arguments(dict(call.arguments or {}), sentence)


# --- the run ----------------------------------------------------------------


def event_text(payload: Any) -> str:
    try:
        text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    if len(text) > MAX_PAYLOAD_CHARS:
        text = text[:MAX_PAYLOAD_CHARS] + "\n… (event truncated)"
    return text


def run_message(
    *, name: str, instruction: str, fields: Any, payload: Any
) -> tuple[str, list[str]]:
    """The one user message the bot gets, and which required fields were absent.

    The absence is stated to the bot rather than refused to the sender, so
    the bot can ask the team for what it needs (``ask_for_decision`` is on
    every text run) instead of the event being lost.
    """
    missing = missing_fields(fields, payload)
    lines = [
        f'An event arrived for the trigger "{name}".',
        "",
        "Event data:",
        "```json",
        event_text(payload),
        "```",
        "",
        "What to do:",
        instruction or name,
    ]
    if missing:
        lines += [
            "",
            "The event did not include: " + ", ".join(missing) + ".",
            (
                "If you cannot do the job without them, do not guess: use "
                "ask_for_decision with allow_other set to true to ask the team "
                "for the missing value, say that you have asked, and stop. If "
                "you can do the job without them, carry on and say what was "
                "missing."
            ),
        ]
    return "\n".join(lines), missing


__all__ = [
    "COMPILE_TOOL",
    "MAX_PAYLOAD_CHARS",
    "MAX_PER_WORKFLOW",
    "MAX_TRIGGERS_PER_HOUR",
    "OPS",
    "SOURCE_EMAIL",
    "SOURCE_WEBHOOK",
    "SOURCES",
    "address_uuid",
    "inbound_address",
    "normalise_email",
    "Compiled",
    "compile",
    "describe",
    "from_arguments",
    "lookup",
    "matches",
    "missing_fields",
    "new_secret",
    "new_uuid",
    "public_url",
    "run_message",
]
