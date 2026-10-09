"""The decision: keep going, repair once, transfer now, or offer a callback.

Pure and synchronous -- no database, no carrier, no model -- so every rule
here is a unit test, and the same inputs always give the same answer.

**Hard triggers bypass the score** and transfer at once: an explicit request
for a person (or a mention repeated, which is insistence), and a topic the
policy always sends to a person. No repair after an explicit request: a caller
who asked for a human and is asked to rephrase has been refused.

**Soft signals add points** -- the same step failed, no match, silence, a
tool error, a key detail heard with low confidence. Crossing the threshold
the first time earns one **repair** (rephrase, confirm, offer a choice);
crossing it again with a new signal after that transfers.

**Frustration only lowers the threshold.** The model's judgement of a
caller's mood is the least reliable signal there is, so it never escalates
by itself; it makes the agent give up sooner on a call already going badly.

**Nobody there raises the threshold** and turns a transfer into a callback:
ringing an empty office and holding the caller for seventy-five seconds is
worse than one more try and a promise of a call back.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from api.services.escalation import ReasonCode
from api.services.escalation.policy import EscalationPolicy
from api.services.escalation.signals import Reading, read

#: Points a call may collect before the agent tries a repair.
BASE_THRESHOLD = 3
#: Added when no person is there to take the call.
NO_HUMAN_RAISE = 2
#: Frustration at or above this level lowers the threshold by one.
FRUSTRATION_LEVEL = 2


class Action(str, Enum):
    NONE = "none"
    REPAIR = "repair"
    TRANSFER = "transfer"
    CALLBACK = "callback"


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: ReasonCode | None = None
    detail: str = ""
    topic: str | None = None

    @property
    def escalates(self) -> bool:
        return self.action in (Action.TRANSFER, Action.CALLBACK)


NOTHING = Decision(Action.NONE)

#: Soft-signal kinds and the reason each counts towards.
FAILURE_KINDS = ("step_failed", "no_match", "no_input", "tool_error")
SOFT_KINDS = (*FAILURE_KINDS, "low_confidence")


@dataclass
class EscalationEvaluator:
    policy: EscalationPolicy
    #: Asked at decision time, not once: a call that starts at 5:58pm may
    #: still be going when the office closes.
    humans_available: Callable[[], bool] = lambda: True

    weak_mentions: int = 0
    points: Counter = field(default_factory=Counter)
    step_failures: Counter = field(default_factory=Counter)
    frustration: int = 0
    #: Score when the repair was offered; None until one is.
    repaired_at: int | None = None
    #: Set once an escalation has been decided, so duplicate signals while
    #: one is under way decide nothing.
    decided: Decision | None = None

    # --- inputs -----------------------------------------------------------

    def observe_text(self, text: str) -> Decision:
        """The caller's own words, read for hard triggers."""
        reading = read(
            text,
            custom_topics=self.policy.custom_topics,
            refund_limit=self.policy.refund_limit,
        )
        return self.observe_reading(reading)

    def observe_reading(self, reading: Reading) -> Decision:
        if self.decided is not None:
            return NOTHING
        for topic in reading.topics:
            if topic in self.policy.always_transfer_topics:
                return self._escalate(ReasonCode.POLICY, f"Topic: {topic}", topic=topic)
        if reading.custom:
            return self._escalate(
                ReasonCode.POLICY, f"Topic: {reading.custom}", topic="custom"
            )
        if reading.request == "strong":
            return self._escalate(
                ReasonCode.EXPLICIT_REQUEST, "The caller asked for a person"
            )
        if reading.request == "weak":
            self.weak_mentions += 1
            if self.weak_mentions >= 2:
                return self._escalate(
                    ReasonCode.EXPLICIT_REQUEST,
                    "The caller kept asking for a person",
                )
        return NOTHING

    def observe_signal(
        self,
        kind: str,
        *,
        topic: str | None = None,
        level: int | None = None,
        step: str | None = None,
        detail: str = "",
    ) -> Decision:
        """What the model reported through its tool. An input, not a verdict."""
        if self.decided is not None:
            return NOTHING
        if kind == "explicit_request":
            return self._escalate(
                ReasonCode.EXPLICIT_REQUEST,
                detail or "The caller asked for a person",
            )
        if kind == "policy_topic":
            if topic and topic in self.policy.always_transfer_topics:
                return self._escalate(
                    ReasonCode.POLICY, detail or f"Topic: {topic}", topic=topic
                )
            # A topic the owner did not choose to send to a person is noted
            # and nothing more -- the model does not get to widen the policy.
            return NOTHING
        if kind == "frustration":
            self.frustration = max(self.frustration, int(level or 0))
            return self._evaluate_score()
        if kind in SOFT_KINDS:
            return self.observe_failure(kind, step=step)
        return NOTHING

    def observe_failure(self, kind: str, *, step: str | None = None) -> Decision:
        if self.decided is not None:
            return NOTHING
        if kind not in SOFT_KINDS:
            kind = "tool_error"
        self.points[kind] += 1
        if kind == "step_failed" and step:
            self.step_failures[step] += 1
        return self._evaluate_score()

    def observe_quiet(self, kind: str = "no_input") -> None:
        """Count a silence without deciding on it. Transferring a caller who
        has gone quiet hands a person an empty line; the next thing they do
        say is what gets evaluated."""
        if self.decided is None:
            self.points[kind] += 1

    # --- the decision -----------------------------------------------------

    @property
    def score(self) -> int:
        looped = any(
            n >= self.policy.max_ai_attempts for n in self.step_failures.values()
        )
        return sum(self.points.values()) + (BASE_THRESHOLD if looped else 0)

    def threshold(self) -> int:
        value = BASE_THRESHOLD
        if not self.humans_available():
            value += NO_HUMAN_RAISE
        if self.frustration >= FRUSTRATION_LEVEL:
            value -= 1
        return max(1, value)

    def _soft_reason(self) -> ReasonCode:
        unfrustrated = self.threshold() + (
            1 if self.frustration >= FRUSTRATION_LEVEL else 0
        )
        # Without the frustration, this score would at most have earned the
        # repair: the mood is what tipped it.
        if self.frustration >= FRUSTRATION_LEVEL and self.score <= unfrustrated:
            return ReasonCode.FRUSTRATION
        failures = sum(self.points[k] for k in FAILURE_KINDS)
        if self.points["low_confidence"] > failures:
            return ReasonCode.LOW_CONFIDENCE
        return ReasonCode.REPAIR_LOOP

    def _evaluate_score(self) -> Decision:
        score = self.score
        if score < self.threshold():
            return NOTHING
        if self.repaired_at is None:
            self.repaired_at = score
            return Decision(Action.REPAIR, self._soft_reason(), "One repair first")
        if score > self.repaired_at:
            reason = self._soft_reason()
            return self._escalate(reason, f"Score {score} after a repair")
        return NOTHING

    def _escalate(
        self, reason: ReasonCode, detail: str, *, topic: str | None = None
    ) -> Decision:
        action = Action.TRANSFER if self.humans_available() else Action.CALLBACK
        self.decided = Decision(action, reason, detail, topic)
        return self.decided

    def rearm(self) -> None:
        """After a ladder that reached nobody: the next escalation is a new
        one. Counters are kept; the call's history does not reset."""
        self.decided = None


__all__ = [
    "Action",
    "BASE_THRESHOLD",
    "Decision",
    "EscalationEvaluator",
    "NO_HUMAN_RAISE",
    "NOTHING",
]
