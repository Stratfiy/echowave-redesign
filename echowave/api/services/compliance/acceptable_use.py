"""Read a bot's instructions against the acceptable use policy.

Seven of the twelve prohibitions the terms carry are contractual only: they
say what a customer may not build, and nothing looks at what they built. A
policy nobody checks catches the people who read it, which is the people who
were not going to break it.

So one model call over the instructions at save time. It catches the honest
mistake -- "tell them their package is held by customs and take the fee" is
written by somebody who thinks they are describing a courier bot -- and the
low-effort bad actor, and it is cheap because the instructions are short.

**It warns; it does not refuse.** A refusal has to be right every time or it
blocks a paying customer from working, and this is a language model reading
prose. A warning that names the clause is useful even when it is wrong, and
the false positive costs somebody one glance. Enforcement stays with the
suspension power the terms already carry, exercised by a person.

**It fails open.** No key installed, vendor down, malformed reply -- the save
goes through with no findings. A compliance check that can take the product
down is a worse problem than the one it solves, and a bot whose instructions
were never screened is exactly the position every bot was in before this.

The carve-outs are in the clause text on purpose. A clinic bot that books,
reminds, confirms and answers factual questions is the single most common
thing built on this platform, and the policy itself permits it; a screen that
flagged it would be worse than no screen, because the warning nobody can act
on is the warning everybody learns to dismiss.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.agent_builder import settings
from api.services.agent_builder.client import BuilderClientError, Conversation, complete
from api.services.billing import model_usage


@dataclass(frozen=True)
class Clause:
    """One prohibition, in the words the published terms use."""

    key: str
    title: str
    #: What the model is asked to look for, carve-outs included. This is the
    #: whole specification -- there is no second list somewhere that says what
    #: "medical advice" means.
    covers: str


#: The twelve, in the order the terms give them. Keys are stable: they end up
#: in stored findings and in whatever a staff screen renders.
CLAUSES: tuple[Clause, ...] = (
    Clause(
        key="fraud",
        title="Deception and extraction of money",
        covers=(
            "Fraudulent, deceptive or scam calls. Pressuring, frightening or "
            "misleading somebody into making a payment or disclosing a payment "
            "instrument, an OTP, a password or any credential. Impersonating a "
            "bank, a government body, a courier, a law enforcement agency or a "
            "utility."
        ),
    ),
    Clause(
        key="impersonation",
        title="Impersonating a person or organisation",
        covers=(
            "Claiming to be a named person or a company without their "
            "authority. Speaking as a business the customer plainly operates "
            "is not impersonation."
        ),
    ),
    Clause(
        key="undisclosed_bot",
        title="Concealing that the recipient is speaking with an automated agent",
        covers=(
            "Instructions to deny being a bot, to claim to be human, or to "
            "evade the question when asked. Simply having a human name is not "
            "concealment; refusing to answer 'are you a robot' honestly is."
        ),
    ),
    Clause(
        key="medical",
        title="Medical advice",
        covers=(
            "Diagnosing, offering treatment advice, or telling anybody whether "
            "to take, change or stop a medication. Booking, reminding, "
            "confirming appointments and answering factual questions about a "
            "practice -- its hours, address, services, prices, doctors -- are "
            "NOT advice and are expressly permitted."
        ),
    ),
    Clause(
        key="regulated_advice",
        title="Financial, investment, insurance or legal advice",
        covers=(
            "Recommending an investment, a policy or a legal course of action. "
            "Stating a fact about the customer's own product, price, or "
            "process is not advice; recommending what the recipient should do "
            "with their money or their case is."
        ),
    ),
    Clause(
        key="emergency",
        title="Emergency or life-safety use",
        covers=(
            "Relied on to reach emergency services, or placed where a failure "
            "to connect could cause harm. Telling a caller to hang up and dial "
            "an emergency number is the opposite of this and is fine."
        ),
    ),
    Clause(
        key="debt_collection",
        title="Debt collection without a licence, or with pressure",
        covers=(
            "Recovery calls that apply pressure, threaten, contact third "
            "parties about somebody's debt, or fall outside permitted hours. "
            "A payment reminder to the customer's own debtor, stating the "
            "amount and the due date, is not this."
        ),
    ),
    Clause(
        key="political",
        title="Political campaigning or election calls",
        covers="Campaigning, canvassing, or turning out a vote.",
    ),
    Clause(
        key="children",
        title="Calls to children",
        covers=(
            "Aimed at children, or collecting a child's personal data. A bot "
            "that speaks to a parent about their child -- a school calling "
            "about attendance, a clinic about a paediatric appointment -- is "
            "not this."
        ),
    ),
    Clause(
        key="recording",
        title="Recording without disclosure",
        covers="Instructions to hide, deny or suppress the recording disclosure.",
    ),
    Clause(
        key="restricted_goods",
        title="Adult content, gambling, or restricted goods",
        covers=(
            "Selling or promoting adult content, gambling, or goods whose sale "
            "is restricted in the recipient's market."
        ),
    ),
    Clause(
        key="harassment",
        title="Harassment or threats",
        covers=(
            "Threatening, abusing or repeatedly contacting somebody who has "
            "asked to stop, and circumventing do-not-call or revoked consent."
        ),
    ),
)

CLAUSE_KEYS: frozenset[str] = frozenset(c.key for c in CLAUSES)
CLAUSE_TITLES: dict[str, str] = {c.key: c.title for c in CLAUSES}

#: Bounds one screening. Instructions longer than this are read up to it: a
#: prompt nobody will read in full is not made safer by paying to read all of
#: it, and the opening is where the persona and the objective live.
MAX_CHARS = 12_000

_SYSTEM = """\
You read the instructions given to an automated calling agent and report only \
what breaks the platform's acceptable use policy.

You are not a style critic and not a safety filter. You report a clause only \
when the instructions actually direct the agent to do the prohibited thing. A \
topic being adjacent is not a finding: a clinic bot mentions medicine, a \
lending bot mentions money, a school bot mentions children, and none of those \
is a breach on its own.

Where a clause names an exception, the exception wins. Read it as written.

Call `report_findings` exactly once. Report nothing when nothing breaches -- \
an empty list is the expected answer for almost every agent you will read."""


def _tool_schema() -> dict[str, Any]:
    return {
        "name": "report_findings",
        "description": "Report every clause these instructions breach, or none.",
        "parameters": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "clause": {
                                "type": "string",
                                "enum": [c.key for c in CLAUSES],
                            },
                            "quote": {
                                "type": "string",
                                "description": (
                                    "The sentence from the instructions that "
                                    "breaches it, copied exactly."
                                ),
                            },
                            "why": {
                                "type": "string",
                                "description": "One sentence, for the author to read.",
                            },
                        },
                        "required": ["clause", "quote", "why"],
                    },
                }
            },
            "required": ["findings"],
        },
    }


@dataclass(frozen=True)
class Finding:
    clause: str
    title: str
    quote: str
    why: str

    def as_dict(self) -> dict[str, str]:
        return {
            "clause": self.clause,
            "title": self.title,
            "quote": self.quote,
            "why": self.why,
        }


def _prompt_for(instructions: str) -> str:
    clauses = "\n\n".join(f"{c.key} — {c.title}\n{c.covers}" for c in CLAUSES)
    return (
        "The policy clauses:\n\n"
        f"{clauses}\n\n"
        "The agent's instructions:\n\n"
        "<<<INSTRUCTIONS\n"
        f"{instructions[:MAX_CHARS]}\n"
        "INSTRUCTIONS>>>"
    )


def _findings_from(arguments: Any) -> list[Finding]:
    """Read the tool call's arguments, discarding anything malformed.

    The model names the clause from a fixed list, and a key outside it is
    dropped rather than surfaced: a finding nobody can look up is a warning
    with no clause behind it, which is the thing this is supposed to avoid.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return []
    if not isinstance(arguments, dict):
        return []

    found: list[Finding] = []
    for raw in arguments.get("findings") or []:
        if not isinstance(raw, dict):
            continue
        clause = str(raw.get("clause") or "")
        if clause not in CLAUSE_KEYS:
            continue
        found.append(
            Finding(
                clause=clause,
                title=CLAUSE_TITLES[clause],
                quote=str(raw.get("quote") or "")[:500],
                why=str(raw.get("why") or "")[:500],
            )
        )
    return found


#: Where a workflow definition keeps words the agent will say or follow. The
#: greeting is included because "say you are calling from the bank" is a
#: breach whether it is in the briefing or in the first line.
_TEXT_FIELDS = ("prompt", "greeting")


def instructions_in(definition: Any) -> str:
    """Every instruction in a definition, as one block of text.

    Node order, joined by blank lines. A flow's steps are read together
    because the policy is about what the agent is told to do, and splitting a
    scam across two steps should not split it past a reader.
    """
    if not isinstance(definition, dict):
        return ""
    parts: list[str] = []
    for node in definition.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        for field in _TEXT_FIELDS:
            value = data.get(field)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return "\n\n".join(parts)


async def screen(
    session: AsyncSession, *, instructions: str, organization_id: int | None = None
) -> list[Finding]:
    """What these instructions breach, or an empty list.

    Never raises. Every failure path -- no key installed, the vendor refusing,
    a reply that does not parse -- returns no findings, because a save that
    fails on a compliance check nobody asked for is worse than an unscreened
    bot, and an unscreened bot is where every bot was until now.

    ``organization_id`` is whose bot is being screened, so the model call is
    recorded against them rather than against nobody.
    """
    text = (instructions or "").strip()
    if not text:
        return []

    try:
        model = await settings.resolve_choice(
            session, settings.cheap_choice(organization_id)
        )
    except settings.BuilderUnavailable as exc:
        # Ordinary on a deployment with no platform LLM key. Debug, not a
        # warning: it would otherwise fire on every save, forever.
        logger.debug("Acceptable-use screening skipped: {}", exc)
        return []

    conversation = Conversation()
    conversation.add_user(_prompt_for(text))

    try:
        with model_usage.scope(
            organization_id=organization_id, feature="acceptable_use"
        ):
            reply = await complete(
                provider=model.provider,
                model=model.model,
                api_key=model.api_key,
                system=_SYSTEM,
                conversation=conversation,
                tools=[_tool_schema()],
            )
    except BuilderClientError as exc:
        logger.warning("Acceptable-use screening could not run: {}", exc)
        return []
    except Exception:  # noqa: BLE001 - a screen must never break a save
        logger.exception("Acceptable-use screening raised")
        return []

    for call in reply.tool_calls:
        if call.name == "report_findings":
            return _findings_from(call.arguments)
    return []
