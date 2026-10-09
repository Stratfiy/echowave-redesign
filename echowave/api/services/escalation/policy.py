"""One agent's escalation policy: who to ring, when, and for what.

Stored on the agent's configuration under ``escalation_policy``, beside
``agent_schedule``, so it is versioned with everything else the agent does:
a change goes into the draft and reaches live calls when it is published,
and "when did we start sending refunds to Priya" is answered from the same
version history as "when did we change the prompt".

**Defaults are the safe reading of "nobody set this".** A caller who says
"chest pain", "I've been scammed" or "I'm calling my lawyer" goes to a person
on every agent with the flag on, because an agent that tries one more time on
those is the failure a business cannot explain. Refund limits and regulated
advice are off until somebody says what their limit or their regulator is.

Parsing is forgiving and loud: a malformed field falls back to its default
with a warning, never to "off". A policy nobody can read must not quietly
stop sending emergencies to a person.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Mapping

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from api.schemas.workflow_configurations import AgentSchedule

CONFIG_KEY = "escalation_policy"

#: The topics the policy knows how to recognise. Order is the order the
#: settings card lists them in.
TOPICS: tuple[str, ...] = (
    "emergency",
    "fraud",
    "legal_threat",
    "refund_over_limit",
    "regulated_advice",
)
TOPIC_LABELS: dict[str, str] = {
    "emergency": "Emergencies and safety",
    "fraud": "Fraud or a scam",
    "legal_threat": "Legal threats and formal complaints",
    "refund_over_limit": "Refunds over the limit",
    "regulated_advice": "Regulated advice (money, insurance, medical)",
}
DEFAULT_TOPICS: tuple[str, ...] = ("emergency", "fraud", "legal_threat")

MAX_NUMBERS = 5
MAX_CUSTOM_TOPICS = 20


class TransferTarget(BaseModel):
    """One person (or desk) to ring, in order."""

    model_config = ConfigDict(extra="ignore")

    number: str
    #: Who the caller is told is joining ("Priya from billing"). Optional:
    #: unnamed, the caller hears "someone from the team".
    name: str | None = Field(default=None, max_length=60)

    @field_validator("number")
    @classmethod
    def _dialable(cls, value: str) -> str:
        from api.services.compliance import dnd

        normalised = dnd.normalise_number(value)
        if normalised is None:
            raise ValueError(f"{value!r} is not a phone number")
        return dnd.to_dialable(normalised) or value

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        value = " ".join((value or "").split())
        return value or None


class EscalationPolicy(BaseModel):
    """What an agent's owner decided about handing callers to people."""

    model_config = ConfigDict(extra="ignore")

    #: Rung in order. Empty means the agent's transfer tool's own destination,
    #: else the workspace's "Hand callers to" number.
    transfer_numbers: list[TransferTarget] = Field(
        default_factory=list, max_length=MAX_NUMBERS
    )
    #: When people are there to answer. Off means "use the agent's own
    #: hours", and no hours at all means always.
    transfer_hours: AgentSchedule = Field(default_factory=AgentSchedule)
    always_transfer_topics: list[str] = Field(
        default_factory=lambda: list(DEFAULT_TOPICS)
    )
    #: The owner's own phrases ("cancel my membership"), matched literally.
    custom_topics: list[str] = Field(default_factory=list, max_length=MAX_CUSTOM_TOPICS)
    #: Refunds above this many rupees go to a person. None: no limit set,
    #: so the refund topic never fires.
    refund_limit: int | None = Field(default=None, ge=0)
    #: How many times the agent may fail the same step before a person takes
    #: over. Two, after the research brief: a second failure on one step is a
    #: loop, not bad luck.
    max_ai_attempts: int = Field(default=2, ge=1, le=5)
    ring_timeout_seconds: int = Field(default=25, ge=10, le=60)
    hold_cap_seconds: int = Field(default=75, ge=30, le=180)
    hold_update_seconds: int = Field(default=25, ge=15, le=45)
    #: Rungs of the no-answer ladder after "back to the agent".
    offer_callback: bool = True
    ticket_channel: Literal["off", "sms", "whatsapp"] = "off"
    team_voicemail: bool = True

    @field_validator("always_transfer_topics")
    @classmethod
    def _known_topics(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for topic in value:
            topic = str(topic).strip()
            if topic not in TOPICS:
                raise ValueError(
                    f"{topic!r} is not a topic; one of {', '.join(TOPICS)}"
                )
            if topic not in out:
                out.append(topic)
        return out

    @field_validator("custom_topics")
    @classmethod
    def _phrases(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for phrase in value:
            phrase = " ".join(str(phrase).split())[:80]
            if phrase and phrase.lower() not in (p.lower() for p in out):
                out.append(phrase)
        return out


def default_policy() -> EscalationPolicy:
    return EscalationPolicy()


def parse(raw: Any) -> EscalationPolicy:
    """The policy stored on an agent, or the default.

    Field by field: one bad field (a number somebody typed wrong) costs that
    field, not the whole policy. Losing the whole policy would switch the
    emergency topic off for an agent whose owner mistyped a phone number.
    """
    if not isinstance(raw, Mapping):
        return default_policy()
    try:
        return EscalationPolicy.model_validate(dict(raw))
    except ValidationError as exc:
        logger.warning(
            "Escalation policy has invalid fields; keeping the rest: {}", exc
        )
    kept: dict[str, Any] = {}
    for key, value in raw.items():
        try:
            EscalationPolicy.model_validate({key: value})
        except ValidationError:
            if key == "transfer_numbers" and isinstance(value, list):
                good = []
                for entry in value:
                    try:
                        good.append(TransferTarget.model_validate(entry))
                    except ValidationError:
                        continue
                kept[key] = [g.model_dump() for g in good]
            continue
        kept[key] = value
    return EscalationPolicy.model_validate(kept)


def from_configurations(configurations: Mapping[str, Any] | None) -> EscalationPolicy:
    return parse((configurations or {}).get(CONFIG_KEY))


def validate_changes(changes: Mapping[str, Any]) -> EscalationPolicy:
    """Strictly: what a person sends from the settings card or the chat.

    Unlike ``parse``, a bad field here is an error to show them, because they
    are looking at the form and can fix it.
    """
    return EscalationPolicy.model_validate(dict(changes))


def humans_available(
    policy: EscalationPolicy,
    agent_schedule: Any = None,
    *,
    now: datetime | None = None,
) -> bool:
    """Is anybody there to take a call right now?

    The policy's transfer hours when set, else the agent's own hours, else
    always. Fail-open like ``agent_hours.is_open``: unreadable hours mean
    "try", and the ladder's fallbacks catch a person who does not pick up.
    """
    from api.services.workflow import agent_hours

    schedule: Any = policy.transfer_hours.model_dump()
    if not schedule.get("enabled"):
        schedule = agent_schedule
    return agent_hours.is_open(schedule, now)


def summary_lines(policy: EscalationPolicy) -> list[str]:
    """The policy in plain lines, for a proposal card and the chat."""
    numbers = ", ".join(
        f"{t.name} ({t.number})" if t.name else t.number
        for t in policy.transfer_numbers
    )
    topics = ", ".join(TOPIC_LABELS[t] for t in policy.always_transfer_topics)
    if policy.custom_topics:
        topics = ", ".join(filter(None, [topics, *policy.custom_topics]))
    hours = policy.transfer_hours
    return [
        f"Ring: {numbers or 'the transfer tool or the workspace number'}",
        "Hours: "
        + (
            f"{len(hours.slots)} window(s), {hours.timezone}"
            if hours.enabled
            else "the agent's own hours"
        ),
        f"Always to a person: {topics or 'nothing'}",
        f"Refund limit: {'none' if policy.refund_limit is None else f'Rs {policy.refund_limit}'}",
        f"Tries before a person takes over: {policy.max_ai_attempts}",
    ]


__all__ = [
    "CONFIG_KEY",
    "DEFAULT_TOPICS",
    "EscalationPolicy",
    "TOPICS",
    "TOPIC_LABELS",
    "TransferTarget",
    "default_policy",
    "from_configurations",
    "humans_available",
    "parse",
    "summary_lines",
    "validate_changes",
]
