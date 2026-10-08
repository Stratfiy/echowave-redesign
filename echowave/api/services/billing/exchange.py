"""The exchange: what each thing a bot does costs in credits (D-1).

Decided 25 September 2026 ("go ahead and have the credits charging done"),
and switched on by ``CHARGE_RULE_2026_09_ENABLED``. Off, and every charge is
exactly what ``events.EVENT_CREDITS`` and the bundle rates said before; on,
and the figures below are the price list.

Three multipliers, written down as code rather than remembered:

* ``M_COMPUTE`` (3.4) -- what a credit sells compute at: the language model,
  an agent's events, and every token a premium model uses.
* ``M_PASS_THROUGH`` (1.7) -- what a credit sells a vendor's own metered
  line at: Meta's WhatsApp template fee, batch transcription.
* Voice is market-priced: a minute is a number a customer compares with
  another vendor's minute, not a cost times a factor. The floor it has to
  clear is ``VOICE_MANAGED_STACK_PAISE_PER_MINUTE`` below.

The table is data (``EXCHANGE_TABLE``), versioned, and the one place a
figure lives. ``events``, ``messaging_charges``, ``costing`` and the run
estimate read their credit figures from here while the flag is on, the rate
card endpoint serves it, and a test fails when a shipped figure disagrees
with it.

**Model allowance.** Every event's credits include the model cost those
credits pay for at ``M_COMPUTE``: 50 / 3.4 = 14.7 paise of vendor cost per
credit (``INCLUDED_MODEL_PAISE_PER_CREDIT``). A reply on a cheap model sits
inside it and costs its 1 credit; model cost past it, on any model, is
charged as extra credits on the *same* ledger row -- one row, one rounding:
``ceil(excess_cost_paise * M_COMPUTE / 50)``. The allowance is money, not a
token count, on purpose: a token count sized for the dearest cheap model
overcharges the cheapest by ten times, and one sized for the cheapest gives
the dearest away. Money is fair to both and holds the margin on every model.
The cost is what the turn's recorded usage prices to in the rate book, the
same resolver the run's receipt is costed with. No rate on file, or anything
else going wrong, charges the event credit alone: a reply is never refused
over a model line. A BYOK model (``key_sources.llm == "byok"``) produces no
usage item and so never has a model line, and a voice minute never adds
tokens.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from fractions import Fraction
from typing import Any

from loguru import logger

from api import constants
from api.services.billing.credits import PAISE_PER_CREDIT

#: The version of the table, and the day it took effect. A change to any
#: figure below is a new version, with a new date.
VERSION = "2026-09-25"
EFFECTIVE_FROM = date(2026, 9, 25)

#: Compute (model, agent events, premium tokens).
M_COMPUTE = 3.4
#: A vendor's own metered line passed through (WhatsApp templates,
#: transcription).
M_PASS_THROUGH = 1.7
#: Premium-model tokens are sold at the compute multiple.
PREMIUM_MODEL_MULTIPLIER = M_COMPUTE

#: Vendor model cost one credit includes, in paise: what a credit sold at
#: ``M_COMPUTE`` pays for. An event of N credits includes N times this.
#: (A token allowance of 1,500 was tried first; real turns carry a system
#: prompt, tools and memory at 6-16k tokens, so it charged 2-4 credits for a
#: plain reply on the cheapest model. See the module docstring.)
INCLUDED_MODEL_PAISE_PER_CREDIT: Fraction = Fraction(PAISE_PER_CREDIT) / Fraction(
    str(M_COMPUTE)
)

#: Tokens a reply is assumed to use when the picker estimates a model's
#: price per reply before anything has run.
TOKENS_PER_REPLY_ESTIMATE = 1_400

#: The standard tier: models whose tokens a text event includes, up to the
#: allowance. Chosen from the managed tiers and the catalogue: the cheap,
#: fast models a reply normally runs on -- Claude Haiku (the Everyday tier),
#: OpenAI's mini and nano families, Google's Flash-Lite, and Sarvam's 105B
#: (the Fast tier). Everything else (Claude Sonnet and Opus, GPT-4.1, GPT-5,
#: Gemini Flash, DeepSeek, Mistral...) is premium and pays for its tokens.
#: Model ids as the pipeline records them, lower-case; a dated snapshot
#: (``gpt-4.1-mini-2025-04-14``) counts as its model.
STANDARD_MODELS: frozenset[str] = frozenset(
    {
        "claude-haiku-4-5",
        "gpt-4o-mini",
        "gpt-4.1-mini",
        "gpt-4.1-nano",
        "gpt-5-mini",
        "gpt-5-nano",
        "gemini-2.5-flash-lite",
        "gemini-3.5-flash-lite",
        "sarvam-105b",
        "sarvam-105b-conversations",
    }
)

#: TTS vendors whose minute is the premium voice rate. ``PREMIUM_VOICE_
#: PROVIDERS=elevenlabs,cartesia`` replaces the list without a release.
PREMIUM_VOICE_PROVIDERS: frozenset[str] = frozenset(
    s.strip().lower()
    for s in os.getenv("PREMIUM_VOICE_PROVIDERS", "").split(",")
    if s.strip()
) or frozenset({"elevenlabs"})

# --- Cost floors the margin test holds the table against -------------------
#: Meta's India marketing-template fee, per message: Rs0.8631.
META_MARKETING_TEMPLATE_PAISE = 86.31
#: Meta's India utility / authentication template fee, per message: Rs0.115.
META_UTILITY_TEMPLATE_PAISE = 11.5
#: The measured cost of a managed voice minute (speech in, model, voice out
#: and carriage) on the standard stack: about Rs2.51.
VOICE_MANAGED_STACK_PAISE_PER_MINUTE = 251
#: Deepgram's pre-recorded (batch) price, $0.0052 a minute, which is what an
#: uploaded file or imported call is transcribed at on Deepgram. The rate
#: book carries the dearer *streaming* rows a live call uses; Sarvam, the
#: managed default, is one Rs30/hour figure for both.
DEEPGRAM_BATCH_USD_PER_MINUTE = 0.0052


# --- Action keys ------------------------------------------------------------
TEXT_REPLY = "text_reply"
KNOWLEDGE_ANSWER = "knowledge_answer"
TOOL_CALL = "tool_call"
TOOL_CALL_PREMIUM = "tool_call_premium"
TRIGGER_RUN = "trigger_run"
TASK_RUN = "task_run"
ROUTINE_RUN = "routine_run"
SCRIPT_RUN = "script_run"
TRANSLATION = "translation"
NUMBER_VERIFICATION = "number_verification"
BUILDER_MESSAGE = "builder_message"
WHATSAPP_UTILITY = "whatsapp_utility"
WHATSAPP_AUTHENTICATION = "whatsapp_authentication"
WHATSAPP_MARKETING = "whatsapp_marketing"
WHATSAPP_SERVICE_REPLY = "whatsapp_service_reply"
VOICE_MINUTE = "voice_minute"
VOICE_MINUTE_PREMIUM = "voice_minute_premium"
TRANSCRIPTION_MINUTE = "transcription_minute"
PREMIUM_MODEL_TOKENS = "premium_model_tokens"

COMPUTE = "compute"
PASS_THROUGH = "pass_through"
MARKET = "market"


@dataclass(frozen=True)
class ExchangeLine:
    key: str
    label: str
    #: None for a line priced by formula rather than a figure (model tokens).
    credits: int | None
    unit: str
    notes: str
    #: Which multiple the figure is set against: compute, pass_through or
    #: market.
    basis: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


#: The published table. Order is the order a customer reads it in.
EXCHANGE_TABLE: tuple[ExchangeLine, ...] = (
    ExchangeLine(
        TEXT_REPLY,
        "Text reply (chat, email, web, WhatsApp)",
        1,
        "per reply",
        "Includes the AI model cost of a normal reply; longer or premium-model work adds credits.",
        COMPUTE,
    ),
    ExchangeLine(
        KNOWLEDGE_ANSWER,
        "Knowledge answer",
        1,
        "per answer",
        "A reply that found something in your documents.",
        COMPUTE,
    ),
    ExchangeLine(
        TOOL_CALL,
        "Tool call (read, or an ordinary write)",
        1,
        "per call",
        "Reading from any app, and writing to one that is not a system of record.",
        COMPUTE,
    ),
    ExchangeLine(
        TOOL_CALL_PREMIUM,
        "Write into a system of record",
        2,
        "per write",
        "Creating, updating or sending in a CRM, ERP, accounting, payments, "
        "commerce or helpdesk app. Reads from those apps are 1.",
        COMPUTE,
    ),
    ExchangeLine(TRIGGER_RUN, "Trigger run", 1, "per run", "", COMPUTE),
    ExchangeLine(TASK_RUN, "Task run", 1, "per run", "", COMPUTE),
    ExchangeLine(ROUTINE_RUN, "Routine run", 2, "per run", "", COMPUTE),
    ExchangeLine(
        SCRIPT_RUN,
        "Script run",
        4,
        "per run",
        "App calls made from inside the script are not counted.",
        COMPUTE,
    ),
    ExchangeLine(
        TRANSLATION, "Translation", 1, "per 100 characters", "Rounded up.", COMPUTE
    ),
    ExchangeLine(
        NUMBER_VERIFICATION,
        "Number verification",
        2,
        "per number",
        "The first two numbers are free.",
        COMPUTE,
    ),
    ExchangeLine(
        BUILDER_MESSAGE,
        "Builder message past the allowance",
        5,
        "per message",
        "Nothing extra on the 21 September plans.",
        COMPUTE,
    ),
    ExchangeLine(
        WHATSAPP_UTILITY,
        "WhatsApp utility template",
        1,
        "per message",
        "Sent from the platform's WhatsApp number.",
        PASS_THROUGH,
    ),
    ExchangeLine(
        WHATSAPP_AUTHENTICATION,
        "WhatsApp authentication template",
        1,
        "per message",
        "Sent from the platform's WhatsApp number.",
        PASS_THROUGH,
    ),
    ExchangeLine(
        WHATSAPP_MARKETING,
        "WhatsApp marketing template",
        3,
        "per message",
        "Sent from the platform's WhatsApp number.",
        PASS_THROUGH,
    ),
    ExchangeLine(
        WHATSAPP_SERVICE_REPLY,
        "WhatsApp reply in a conversation",
        0,
        "per message",
        "The reply's own credit covers it.",
        PASS_THROUGH,
    ),
    ExchangeLine(
        VOICE_MINUTE,
        "Voice minute, standard voice",
        12,
        "per minute",
        "Inbound or outbound, on every plan.",
        MARKET,
    ),
    ExchangeLine(
        VOICE_MINUTE_PREMIUM,
        "Voice minute, premium voice",
        18,
        "per minute",
        "ElevenLabs and other premium voices.",
        MARKET,
    ),
    ExchangeLine(
        TRANSCRIPTION_MINUTE,
        "Transcription",
        2,
        "per minute",
        "Uploaded recordings and imported calls; rounded up per file, 1 credit "
        "minimum.",
        PASS_THROUGH,
    ),
    ExchangeLine(
        PREMIUM_MODEL_TOKENS,
        "Premium model",
        None,
        "per event",
        "The model's tokens at cost x 3.4, added to the event's own credit. "
        "Not charged when you bring your own key.",
        COMPUTE,
    ),
)

TABLE_BY_KEY: dict[str, ExchangeLine] = {line.key: line for line in EXCHANGE_TABLE}

#: The figure for every line priced by a figure.
CREDITS: dict[str, int] = {
    line.key: line.credits for line in EXCHANGE_TABLE if line.credits is not None
}

#: Events whose credit includes the token allowance and carries the model
#: line.
TOKEN_EVENTS: frozenset[str] = frozenset(
    {TEXT_REPLY, KNOWLEDGE_ANSWER, TRIGGER_RUN, TASK_RUN, ROUTINE_RUN}
)


def enabled() -> bool:
    """Read at call time, so a test can switch it with monkeypatch."""
    return bool(constants.CHARGE_RULE_2026_09_ENABLED)


def credits_of(key: str) -> int:
    return CREDITS[key]


def credits_for_cost(cost_paise: float | int | Fraction, multiplier: float) -> int:
    """Whole credits for a vendor cost sold at ``multiplier``: ``ceil(cost x
    M / 50)``. Exact arithmetic, so 86.31 x 1.7 is never 146.72700000001."""
    cost = Fraction(str(cost_paise)) if isinstance(cost_paise, float) else cost_paise
    cost = Fraction(cost)
    if cost <= 0:
        return 0
    return math.ceil(cost * Fraction(str(multiplier)) / PAISE_PER_CREDIT)


# --- Models -----------------------------------------------------------------
_DATED_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$|-\d{8}$")


def normalise_model(model: str | None) -> str:
    name = (model or "").strip().lower()
    if name.startswith("models/"):
        name = name[len("models/") :]
    return _DATED_SNAPSHOT.sub("", name)


def is_standard_model(model: str | None) -> bool:
    return normalise_model(model) in STANDARD_MODELS


# --- Reads and writes -------------------------------------------------------
#: Words that make an action a write. Conservative in the customer's favour
#: only where it is certain: an action is a write when its name says so, and
#: anything else -- including a name nobody recognises -- is an ordinary 1.
WRITE_WORDS: frozenset[str] = frozenset(
    {
        "create",
        "update",
        "delete",
        "remove",
        "send",
        "post",
        "add",
        "insert",
        "upsert",
        "cancel",
        "refund",
        "charge",
        "issue",
        "set",
        "patch",
        "put",
        "edit",
        "modify",
        "write",
        "archive",
        "close",
        "void",
        "capture",
    }
)


#: Words that make an action a read. The first verb in a name decides, so
#: ``ZENDESK_GET_ISSUE`` and ``RAZORPAY_FETCH_REFUND`` are reads although
#: their object is a word that is also a write.
READ_WORDS: frozenset[str] = frozenset(
    {
        "get",
        "list",
        "search",
        "fetch",
        "find",
        "read",
        "retrieve",
        "lookup",
        "query",
        "count",
        "describe",
        "view",
        "show",
        "check",
        "download",
        "export",
    }
)


def is_write_action(name: str | None) -> bool:
    """Whether an action or tool name is a write into the app.

    Split into words on anything that is not a letter and on camel case
    (``HUBSPOT_CREATE_CONTACT``, ``salesforce.update-lead``,
    ``createInvoice``); the first word that is a known read or write verb
    decides. A name with no such word -- empty, or nobody knows what it
    does -- is not a write, and costs the ordinary 1.
    """
    text = re.sub(r"([a-z])([A-Z])", r"\1_\2", name or "")
    for word in re.split(r"[^a-zA-Z]+", text.lower()):
        if word in READ_WORDS:
            return False
        if word in WRITE_WORDS:
            return True
    return False


# --- WhatsApp ---------------------------------------------------------------
WHATSAPP_CATEGORIES: dict[str, str] = {
    "utility": WHATSAPP_UTILITY,
    "authentication": WHATSAPP_AUTHENTICATION,
    "marketing": WHATSAPP_MARKETING,
    "service": WHATSAPP_SERVICE_REPLY,
}


def whatsapp_credits(category: str | None) -> int:
    """Credits for one platform WhatsApp message of ``category``. An unknown
    category is a utility template, the ordinary business message."""
    key = WHATSAPP_CATEGORIES.get((category or "").strip().lower(), WHATSAPP_UTILITY)
    return CREDITS[key]


# --- Voice and transcription ------------------------------------------------
def voice_credits_per_minute(tts_providers: Iterable[str] = ()) -> int:
    premium = any(
        (p or "").strip().lower() in PREMIUM_VOICE_PROVIDERS for p in tts_providers
    )
    return CREDITS[VOICE_MINUTE_PREMIUM if premium else VOICE_MINUTE]


def transcription_credits(seconds: float | int | None) -> int:
    """2 credits a minute, rounded up per file, 1 credit minimum."""
    try:
        secs = max(float(seconds or 0), 0.0)
    except (TypeError, ValueError):
        secs = 0.0
    minutes = math.ceil(secs / 60) if secs else 0
    return max(1, minutes * CREDITS[TRANSCRIPTION_MINUTE])


# --- The model line ---------------------------------------------------------
@dataclass(frozen=True)
class ModelTokens:
    """One model's tokens on an event and what they cost us, in paise
    (fractional: a small turn costs a fraction of a paisa)."""

    model: str
    tokens: int
    cost_paise: Fraction


def model_line_credits(
    entries: Iterable[ModelTokens],
    *,
    event_credits: int = 1,
) -> int:
    """Extra credits for an event's model cost. Pure.

    The event's own credits include ``event_credits`` times
    ``INCLUDED_MODEL_PAISE_PER_CREDIT`` of model cost, on any model; the rest
    is charged at ``M_COMPUTE``. One rounding, at the end.
    """
    total = sum(
        (entry.cost_paise for entry in entries if entry.cost_paise > 0), Fraction(0)
    )
    included = INCLUDED_MODEL_PAISE_PER_CREDIT * max(int(event_credits), 0)
    return credits_for_cost(
        max(total - included, Fraction(0)), PREMIUM_MODEL_MULTIPLIER
    )


def _llm_components() -> set[str]:
    from api.enums import CostComponent

    return {
        CostComponent.LLM.value,
        CostComponent.LLM_INPUT.value,
        CostComponent.LLM_CACHED.value,
        CostComponent.LLM_CACHE_WRITE.value,
        CostComponent.LLM_OUTPUT.value,
    }


async def model_tokens_of(
    session, usages: Iterable[dict[str, Any] | None], *, at: datetime | None = None
) -> list[ModelTokens]:
    """Price the model usage recorded on an event's turns.

    Each ``usage`` is a turn's recorded usage (``usage_info`` shape). A BYOK
    model produces no usage item (``usage.usage_items_from_usage_info``) and
    so no entry. A line with no rate on file is priced at nothing -- the
    reply is charged its event credit and the receipt reports it uncosted.
    """
    from api.services.billing.money import MPAISE_PER_PAISE, quantity_per_rate_unit
    from api.services.billing.rates import resolve_provider_rate
    from api.services.billing.usage import usage_items_from_usage_info

    at = at or datetime.now(UTC)
    llm = _llm_components()
    tokens: dict[str, int] = {}
    cost: dict[str, Fraction] = {}
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        for item in usage_items_from_usage_info(usage):
            component = getattr(item.component, "value", str(item.component))
            if component not in llm:
                continue
            model = item.model or ""
            tokens[model] = tokens.get(model, 0) + int(item.quantity)
            rate = await resolve_provider_rate(
                session,
                provider=item.provider,
                component=item.component,
                at=at,
                model=item.model,
            )
            if rate is None:
                continue
            cost[model] = cost.get(model, Fraction(0)) + Fraction(
                int(item.quantity) * int(rate.rate_mpaise),
                quantity_per_rate_unit(rate.unit) * MPAISE_PER_PAISE,
            )
    return [
        ModelTokens(model=m, tokens=n, cost_paise=cost.get(m, Fraction(0)))
        for m, n in tokens.items()
    ]


async def model_credits(
    session,
    usages: Iterable[dict[str, Any] | None],
    *,
    event_credits: int = 1,
    at: datetime | None = None,
) -> int:
    """The model line for an event, in credits. Never raises: anything that
    goes wrong charges the event credit alone."""
    try:
        return model_line_credits(
            await model_tokens_of(session, usages, at=at), event_credits=event_credits
        )
    except Exception as exc:  # noqa: BLE001 - never fail a reply on this
        logger.warning("Could not price the model line: {}", exc)
        return 0


def estimated_reply_credits(provider: str, model: str) -> int:
    """Credits a reply on this model is expected to cost, for the picker:
    the reply's credit plus the model line for ``TOKENS_PER_REPLY_ESTIMATE``
    tokens at the rate book's blended list price."""
    reply = CREDITS[TEXT_REPLY]
    from api.services.billing import default_rates

    prices = {(p.provider, p.model): p for p in default_rates.LLM_MODEL_PRICES}
    price = prices.get((provider, normalise_model(model))) or prices.get((provider, ""))
    if price is None:
        return reply
    share = default_rates.LLM_INPUT_SHARE
    usd_per_million = Fraction(str(price.input_per_million)) * Fraction(
        str(share)
    ) + Fraction(str(price.output_per_million)) * (1 - Fraction(str(share)))
    cost_paise = (
        usd_per_million
        * TOKENS_PER_REPLY_ESTIMATE
        / 1_000_000
        * Fraction(str(default_rates.REFERENCE_USD_INR))
        * 100
    )
    return reply + model_line_credits(
        [ModelTokens(model, TOKENS_PER_REPLY_ESTIMATE, cost_paise)], event_credits=reply
    )


#: The version of the figures in force before this table: KAN-47.
PREVIOUS_VERSION = "2026-09-14"


def published() -> dict[str, Any]:
    """The table as the rate card endpoint serves it."""
    from api.services.billing import events

    lines = []
    for line in EXCHANGE_TABLE:
        row = line.as_dict()
        if line.key == BUILDER_MESSAGE:
            # The ladder retires the fee; the card says what is charged.
            row["credits"] = events.credits_for(events.BUILDER_MESSAGE)
        lines.append(row)
    return {
        "enabled": True,
        "version": VERSION,
        "effective_from": EFFECTIVE_FROM.isoformat(),
        "paise_per_credit": PAISE_PER_CREDIT,
        "included_model_paise_per_credit": round(
            float(INCLUDED_MODEL_PAISE_PER_CREDIT), 2
        ),
        "premium_model_multiplier": PREMIUM_MODEL_MULTIPLIER,
        "lines": lines,
    }


def todays_card() -> dict[str, Any]:
    """The figures charged with the rule off, in the same shape: the event
    table, the one platform WhatsApp price, and the Everyday minute at list."""
    from api.services.billing import events
    from api.services.billing.messaging_charges import price_paise

    lines: list[dict[str, Any]] = [
        ExchangeLine(
            key=event,
            label=events.EVENT_LABELS[event],
            credits=events.credits_for(event),
            unit="per 100 characters" if event == events.TRANSLATION else "per event",
            notes="",
            basis=COMPUTE,
        ).as_dict()
        for event in events.EVENT_CREDITS
    ]
    lines.append(
        ExchangeLine(
            key="whatsapp_message",
            label="WhatsApp message",
            credits=-(-price_paise() // PAISE_PER_CREDIT),
            unit="per message",
            notes="Sent from the platform's WhatsApp number.",
            basis=PASS_THROUGH,
        ).as_dict()
    )
    return {
        "enabled": False,
        "version": PREVIOUS_VERSION,
        "effective_from": PREVIOUS_VERSION,
        "paise_per_credit": PAISE_PER_CREDIT,
        "included_model_paise_per_credit": None,
        "premium_model_multiplier": None,
        "lines": lines,
    }


def rate_card() -> dict[str, Any]:
    """What things cost, as the app's rate card shows it."""
    return published() if enabled() else todays_card()


__all__ = [
    "CREDITS",
    "EFFECTIVE_FROM",
    "EXCHANGE_TABLE",
    "M_COMPUTE",
    "M_PASS_THROUGH",
    "PREMIUM_MODEL_MULTIPLIER",
    "PREMIUM_VOICE_PROVIDERS",
    "STANDARD_MODELS",
    "INCLUDED_MODEL_PAISE_PER_CREDIT",
    "VERSION",
    "ExchangeLine",
    "ModelTokens",
    "credits_for_cost",
    "credits_of",
    "enabled",
    "estimated_reply_credits",
    "is_standard_model",
    "is_write_action",
    "model_credits",
    "model_line_credits",
    "published",
    "transcription_credits",
    "voice_credits_per_minute",
    "whatsapp_credits",
]
