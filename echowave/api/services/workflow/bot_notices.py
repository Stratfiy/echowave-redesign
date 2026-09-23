"""Which of a bot's events are worth interrupting somebody for.

The bell and its inbox have existed for a while, and everything in them is
about money or the account -- a low balance, an auto top-up, a number's
rent. Nothing in them is about what a bot *did*, which is the thing an
owner actually wants told while they are not looking at the screen.

The events already exist. ``agent_timeline`` writes one row per thing a bot
does, with a kind, a summary and the bot it belongs to, so this is not new
detection: it is deciding which of those rows also ring a bell, per bot.

**Deliberately not every kind.** A notification is an interruption, and the
timeline carries messages, call starts, credit holds and activity lines --
kinds that are right to keep and wrong to be told about. Offering all
twenty-two as checkboxes would be a settings screen nobody finishes
reading, and an inbox nobody reads either.

The eight below are the ones that mean *something has happened that you may
need to do something about*. Four are on by default because they say the
bot is stuck, and a bot that quietly stopped working is the failure this
whole feature exists for; the rest are off, because they fire on a working
bot and an inbox that fills up on success is one people learn to ignore.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from api.enums import AgentEventKind

#: The key inside ``workflow_configurations``. Stored there rather than in a
#: column of its own for the same reason the channel is: no migration, and
#: nothing about telephony has to learn a new field.
CONFIG_KEY = "notify_on"


class Notice:
    """One event a bot can be set to tell somebody about."""

    __slots__ = ("kind", "label", "when", "default")

    def __init__(self, kind: AgentEventKind, label: str, when: str, default: bool):
        self.kind = kind
        self.label = label
        #: What the operator is told this means. A checkbox list of enum
        #: names is a list nobody can choose from honestly.
        self.when = when
        self.default = default

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "label": self.label,
            "when": self.when,
            "default": self.default,
        }


#: Ordered the way somebody reads them: what has gone wrong first, what the
#: bot achieved second. Every kind here is one the runtime actually emits --
#: an offered checkbox that can never fire is worse than no checkbox.
CATALOGUE: tuple[Notice, ...] = (
    Notice(
        AgentEventKind.NEEDS_ATTENTION,
        "It needs attention",
        "The agent has stopped and cannot carry on by itself.",
        True,
    ),
    Notice(
        AgentEventKind.COULD_NOT,
        "It could not do something",
        "It tried a step and the step failed.",
        True,
    ),
    Notice(
        AgentEventKind.ROUTINE_SKIPPED,
        "A scheduled run did not happen",
        "Out of hours, a connector down, or a rule refused it.",
        True,
    ),
    Notice(
        AgentEventKind.NEEDS_SECRET,
        "It is waiting on a credential",
        "A password or key it needs is missing or expired.",
        True,
    ),
    Notice(
        AgentEventKind.NEEDS_DECISION,
        "It is waiting on you",
        "It has asked a question and stopped until somebody answers.",
        False,
    ),
    Notice(
        AgentEventKind.ESCALATED,
        "It handed over to a person",
        "A conversation was passed to a human.",
        False,
    ),
    Notice(
        AgentEventKind.DELIVERABLE,
        "It produced something",
        "A file, a report, a booking -- something you can open.",
        False,
    ),
    Notice(
        AgentEventKind.OUTCOME_FILED,
        "A call was sorted",
        "Every classified call. Busy on an agent that takes many.",
        False,
    ),
)

#: The kinds that may notify at all, as a set for the membership test. A kind
#: outside this is never a notification however it is stored -- somebody
#: hand-editing the configuration cannot opt into being told about every
#: message the bot sends.
NOTIFIABLE: frozenset[str] = frozenset(n.kind.value for n in CATALOGUE)

DEFAULTS: frozenset[str] = frozenset(n.kind.value for n in CATALOGUE if n.default)


def catalogue() -> list[dict[str, Any]]:
    """The offer, for the settings screen."""
    return [notice.as_dict() for notice in CATALOGUE]


def normalise(raw: Any) -> set[str]:
    """The stored selection, reduced to kinds that can actually fire.

    Tolerant, because this comes out of a JSON column: anything unreadable
    falls back to the defaults rather than to silence. Silence is the wrong
    failure here -- the point of the feature is being told when a bot has
    stopped, and a malformed blob should not be how somebody stops being
    told.
    """
    if raw is None:
        return set(DEFAULTS)
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return set(DEFAULTS)
    chosen = {
        entry.strip()
        for entry in raw
        if isinstance(entry, str) and entry.strip() in NOTIFIABLE
    }
    # An explicit empty list is a decision: "tell me nothing about this bot".
    # Only an unreadable value falls back.
    return chosen


def selected_for(configurations: Any) -> set[str]:
    """Which kinds this bot notifies on."""
    if not isinstance(configurations, Mapping):
        return set(DEFAULTS)
    if CONFIG_KEY not in configurations:
        return set(DEFAULTS)
    return normalise(configurations.get(CONFIG_KEY))


def wants(configurations: Any, kind: str) -> bool:
    """Whether this bot should ring the bell for this event."""
    if kind not in NOTIFIABLE:
        return False
    return kind in selected_for(configurations)


def store(kinds: Iterable[str]) -> list[str]:
    """What to write back, in catalogue order so the column is stable.

    Sorted by the catalogue rather than alphabetically: a diff of two saved
    configurations should show what changed, not a reshuffle.
    """
    chosen = {k for k in kinds if k in NOTIFIABLE}
    return [n.kind.value for n in CATALOGUE if n.kind.value in chosen]
