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
stop sending emergencies to a person. Saving is strict: a field the policy
does not have is refused by name, never accepted and dropped (the
silent-absence rule in api/AGENTS.md).

**Never-transfer phrases** ("opening hours", "price list") are what the agent
answers itself. On a sentence that mentions one, a *topic* transfer is held
back -- an owner's phrase, an out-of-scope topic, a policy topic -- and
nothing else: a caller who asks for a person, or says something that reads
as an emergency, still reaches one.

**Shadow mode.** Each rule runs ``on`` or in ``shadow``. A rule in shadow is
evaluated on every call and what it would have done is written on the call's
escalation outcome, but it never acts -- the way a new rule earns trust
before it is switched on. Emergencies and an explicit request for a person
cannot be put in shadow.

**India only.** The carrier's India rules need both legs of a call to begin
and end in India (Plivo refuses the call with ``violates_media_anchoring``
otherwise), so a call can only be handed to an Indian number. An overseas
number is refused when it is typed, not mid-call with a caller on hold.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Mapping

from loguru import logger
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

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
    "vulnerable_caller",
)
TOPIC_LABELS: dict[str, str] = {
    "emergency": "Emergencies and safety",
    "fraud": "Fraud or a scam",
    "legal_threat": "Legal threats and formal complaints",
    "refund_over_limit": "Refunds over the limit",
    "regulated_advice": "Regulated advice (money, insurance, medical)",
    "vulnerable_caller": "Callers in distress or who may be vulnerable",
}
DEFAULT_TOPICS: tuple[str, ...] = ("emergency", "fraud", "legal_threat")
#: Topics handled like an emergency: never held back by a never-transfer
#: phrase, and a caller nobody could be reached for is told to ring 112.
EMERGENCY_CLASS: frozenset[str] = frozenset({"emergency", "vulnerable_caller"})

#: Every rule that runs ``on`` or in ``shadow``: the topics, the owner's
#: phrases, out-of-scope routing, an explicit request and the soft signals.
RULES: tuple[str, ...] = (
    *TOPICS,
    "custom",
    "out_of_scope",
    "explicit_request",
    "repair_loop",
    "low_confidence",
    "frustration",
)
#: Rules that always act. A caller in an emergency or asking for a person is
#: never an experiment.
ALWAYS_ON: frozenset[str] = frozenset({"emergency", "explicit_request"})
RuleMode = Literal["on", "shadow"]

#: The languages a person can be briefed in, by code. The briefing is spoken
#: by the carrier, so a language is listed only where it can be spoken.
BRIEFING_LANGUAGES: dict[str, str] = {"en": "English", "hi": "Hindi"}

MAX_NUMBERS = 5
MAX_CUSTOM_TOPICS = 20
MAX_TEAMS = 5

INDIA_ONLY = (
    "Both legs of a call must be in India (the carrier refuses the call "
    "otherwise), so callers can only be handed to an Indian +91 number."
)


def indian_number(value: Any) -> str:
    """``value`` as a dialable Indian number, or ValueError saying why not."""
    from api.services.compliance import dnd

    normalised = dnd.normalise_number(str(value or ""))
    if normalised is None:
        raise ValueError(f"{value!r} is not a phone number")
    if not (normalised.startswith("91") and len(normalised) == 12):
        raise ValueError(f"{value!r} is not an Indian number. {INDIA_ONLY}")
    return dnd.to_dialable(normalised) or str(value)


def _phrase_list(value: list[str]) -> list[str]:
    out: list[str] = []
    for phrase in value:
        phrase = " ".join(str(phrase).split())[:80]
        if phrase and phrase.lower() not in (p.lower() for p in out):
            out.append(phrase)
    return out


class TransferTarget(BaseModel):
    """One person (or desk) to ring, in order."""

    model_config = ConfigDict(extra="forbid")

    number: str
    #: Who the caller is told is joining ("Priya from billing"). Optional:
    #: unnamed, the caller hears "someone from the team".
    name: str | None = Field(default=None, max_length=60)
    #: The language this person is briefed in before the caller joins.
    language: str = "en"

    @field_validator("number")
    @classmethod
    def _dialable(cls, value: str) -> str:
        return indian_number(value)

    @field_validator("name")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        value = " ".join((value or "").split())
        return value or None

    @field_validator("language")
    @classmethod
    def _briefing_language(cls, value: str) -> str:
        code = str(value or "en").strip().lower().split("-")[0]
        if code not in BRIEFING_LANGUAGES:
            raise ValueError(
                f"{value!r} is not a briefing language; one of "
                + ", ".join(f"{k} ({v})" for k, v in BRIEFING_LANGUAGES.items())
            )
        return code


class Team(BaseModel):
    """A named group of people, rung first for the topics routed to it."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    numbers: list[TransferTarget] = Field(default_factory=list, max_length=MAX_NUMBERS)

    @field_validator("name")
    @classmethod
    def _tidy_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("a team needs a name")
        return value


class OutOfScopeTopic(BaseModel):
    """Something this agent does not handle, and the team that does."""

    model_config = ConfigDict(extra="forbid")

    phrase: str = Field(min_length=1, max_length=80)
    #: None: the agent's general numbers.
    team: str | None = None

    @field_validator("phrase")
    @classmethod
    def _tidy_phrase(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("an out-of-scope topic needs a phrase")
        return value


class EscalationPolicy(BaseModel):
    """What an agent's owner decided about handing callers to people."""

    model_config = ConfigDict(extra="forbid")

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
    #: Phrases the agent answers itself ("opening hours"): a sentence that
    #: mentions one is not transferred on a topic. Never holds back an
    #: explicit request for a person or an emergency.
    never_transfer_topics: list[str] = Field(
        default_factory=list, max_length=MAX_CUSTOM_TOPICS
    )
    #: Rule -> ``on`` | ``shadow``. A rule not listed is on.
    rule_modes: dict[str, RuleMode] = Field(default_factory=dict)
    #: Named teams, and which topics go to which. A topic with no team rings
    #: ``transfer_numbers``.
    teams: list[Team] = Field(default_factory=list, max_length=MAX_TEAMS)
    topic_teams: dict[str, str] = Field(default_factory=dict)
    #: Things this agent does not handle, routed to the team that does.
    out_of_scope_topics: list[OutOfScopeTopic] = Field(
        default_factory=list, max_length=MAX_CUSTOM_TOPICS
    )
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

    @field_validator("custom_topics", "never_transfer_topics")
    @classmethod
    def _phrases(cls, value: list[str]) -> list[str]:
        return _phrase_list(value)

    @field_validator("rule_modes")
    @classmethod
    def _known_rules(cls, value: dict[str, str]) -> dict[str, str]:
        for rule, mode in value.items():
            if rule not in RULES:
                raise ValueError(f"{rule!r} is not a rule; one of {', '.join(RULES)}")
            if rule in ALWAYS_ON and mode != "on":
                raise ValueError(
                    f"{rule!r} always acts: an emergency or a caller asking "
                    "for a person cannot run in shadow"
                )
        return dict(value)

    @model_validator(mode="after")
    def _consistent(self) -> "EscalationPolicy":
        both = {p.lower() for p in self.custom_topics} & {
            p.lower() for p in self.never_transfer_topics
        }
        if both:
            raise ValueError(
                f"{sorted(both)[0]!r} cannot both always and never go to a person"
            )
        names = [t.name.lower() for t in self.teams]
        if len(names) != len(set(names)):
            raise ValueError("two teams have the same name")
        known = set(names)
        routable = (*TOPICS, "custom")
        for topic, team in self.topic_teams.items():
            if topic not in routable:
                raise ValueError(
                    f"{topic!r} is not a topic; one of {', '.join(routable)}"
                )
            if str(team).lower() not in known:
                raise ValueError(f"{team!r} is not one of this agent's teams")
        for entry in self.out_of_scope_topics:
            if entry.team is not None and entry.team.lower() not in known:
                raise ValueError(f"{entry.team!r} is not one of this agent's teams")
        return self

    # --- reading it -----------------------------------------------------------

    def mode(self, rule: str | None) -> str:
        if not rule or rule in ALWAYS_ON:
            return "on"
        return self.rule_modes.get(rule, "on")

    def shadow_rules(self) -> frozenset[str]:
        return frozenset(r for r, m in self.rule_modes.items() if m == "shadow")

    def team(self, name: str | None) -> Team | None:
        if not name:
            return None
        for team in self.teams:
            if team.name.lower() == name.lower():
                return team
        return None

    def team_for(self, topic: str | None, phrase: str | None = None) -> Team | None:
        """The team a decision on ``topic`` goes to, if the owner named one."""
        if topic == "out_of_scope":
            for entry in self.out_of_scope_topics:
                if phrase and entry.phrase.lower() == phrase.lower():
                    return self.team(entry.team)
            return None
        return self.team(self.topic_teams.get(topic or ""))


def default_policy() -> EscalationPolicy:
    return EscalationPolicy()


#: Fields validated together with ``teams``, because they name one.
_NEEDS_TEAMS = frozenset({"topic_teams", "out_of_scope_topics"})
#: Fields that only make sense together, dropped one at a time (with a
#: warning) when the rest of a stored policy will not read with them.
_CROSS_FIELDS = ("topic_teams", "out_of_scope_topics", "never_transfer_topics", "teams")


def parse(raw: Any) -> EscalationPolicy:
    """The policy stored on an agent, or the default.

    Field by field: one bad field (a number somebody typed wrong) costs that
    field, not the whole policy. Losing the whole policy would switch the
    emergency topic off for an agent whose owner mistyped a phone number.
    Every field dropped is logged.
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
        if key not in EscalationPolicy.model_fields:
            logger.warning("Escalation policy field {!r} is not known; ignored", key)
            continue
        probe = {key: value}
        if key in _NEEDS_TEAMS and "teams" in raw:
            probe["teams"] = raw["teams"]
        try:
            EscalationPolicy.model_validate(probe)
        except ValidationError:
            if key == "transfer_numbers" and isinstance(value, list):
                good = []
                for entry in value:
                    try:
                        good.append(TransferTarget.model_validate(entry))
                    except ValidationError as exc:
                        logger.warning("Escalation number dropped: {}", exc)
                kept[key] = [g.model_dump() for g in good]
            continue
        kept[key] = value
    for dropping in (None, *_CROSS_FIELDS):
        if dropping is not None:
            if dropping not in kept:
                continue
            logger.warning("Escalation policy field {!r} dropped to read", dropping)
            kept.pop(dropping)
        try:
            return EscalationPolicy.model_validate(kept)
        except ValidationError:
            continue
    return default_policy()


def from_configurations(configurations: Mapping[str, Any] | None) -> EscalationPolicy:
    return parse((configurations or {}).get(CONFIG_KEY))


def validate_changes(changes: Mapping[str, Any]) -> EscalationPolicy:
    """Strictly: what a person sends from the settings card or the chat.

    Unlike ``parse``, a bad field here is an error to show them, because they
    are looking at the form and can fix it -- and so is a field the policy
    does not have, which is refused by name rather than accepted and dropped.
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
    lines = [
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
    if policy.never_transfer_topics:
        lines.append(
            "The agent answers itself: " + ", ".join(policy.never_transfer_topics)
        )
    if policy.teams:
        lines.append("Teams: " + ", ".join(t.name for t in policy.teams))
    if policy.out_of_scope_topics:
        lines.append(
            "Not this agent's job: "
            + ", ".join(
                f"{o.phrase} ({o.team})" if o.team else o.phrase
                for o in policy.out_of_scope_topics
            )
        )
    shadow = sorted(policy.shadow_rules())
    if shadow:
        lines.append("Watching only (shadow): " + ", ".join(shadow))
    return lines


__all__ = [
    "ALWAYS_ON",
    "BRIEFING_LANGUAGES",
    "CONFIG_KEY",
    "DEFAULT_TOPICS",
    "EMERGENCY_CLASS",
    "EscalationPolicy",
    "INDIA_ONLY",
    "OutOfScopeTopic",
    "RULES",
    "TOPICS",
    "TOPIC_LABELS",
    "Team",
    "TransferTarget",
    "default_policy",
    "from_configurations",
    "humans_available",
    "indian_number",
    "parse",
    "summary_lines",
    "validate_changes",
]
