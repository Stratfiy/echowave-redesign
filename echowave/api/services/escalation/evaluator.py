"""The decision: keep going, repair once, transfer now, or offer a callback.

Pure and synchronous -- no database, no carrier, no model -- so every rule
here is a unit test, and the same inputs always give the same answer.

**Hard triggers bypass the score** and transfer at once: an explicit request
for a person (or a mention repeated, which is insistence), and a topic the
policy always sends to a person. No repair after an explicit request: a caller
who asked for a human and is asked to rephrase has been refused.

**A never-transfer phrase holds back a topic, never a person.** On a
sentence that mentions something the owner said the agent answers itself, a
topic transfer (policy topic, owner's phrase, out-of-scope topic) is held
back. An explicit request for a person and the emergency-class topics are
not: the owner cannot opt a caller out of reaching a human.

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

**Shadow rules** are evaluated by a second, identical evaluator that has
every rule on (the *shadow lane*). Whenever the lane escalates on a rule the
owner put in shadow and the real one does not, the hit is kept in
``shadow_hits`` -- "would have escalated, at caller turn n, because ..." --
for the call's outcome record. The lane never acts.

**Turns are counted** (each final caller utterance is one), and every
decision carries the turn it was made on, so a reviewer can say whether a
transfer came on time or five turns late (Liu et al., "Time to Transfer").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from api.services.escalation import ReasonCode
from api.services.escalation.policy import EMERGENCY_CLASS, EscalationPolicy
from api.services.escalation.signals import Reading, read

#: Points a call may collect before the agent tries a repair.
BASE_THRESHOLD = 3
#: Added when no person is there to take the call.
NO_HUMAN_RAISE = 2
#: Frustration at or above this level lowers the threshold by one.
FRUSTRATION_LEVEL = 2
#: Shadow hits kept per call, so a long call cannot grow a row without bound.
MAX_SHADOW_HITS = 20


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
    #: The caller turn (1-based) the decision was made on; 0 before any.
    turn: int | None = None
    #: The owner's phrase that decided it (out-of-scope routing reads it).
    phrase: str | None = None

    @property
    def escalates(self) -> bool:
        return self.action in (Action.TRANSFER, Action.CALLBACK)

    @property
    def rule(self) -> str | None:
        """The policy rule this decision came from, as ``rule_modes`` names it."""
        if self.reason is None:
            return None
        if self.reason == ReasonCode.POLICY:
            return self.topic
        return self.reason.value


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
    #: True for the shadow lane itself: every rule on, nothing recorded.
    shadow_lane: bool = False

    weak_mentions: int = 0
    points: Counter = field(default_factory=Counter)
    step_failures: Counter = field(default_factory=Counter)
    frustration: int = 0
    #: Score when the repair was offered; None until one is.
    repaired_at: int | None = None
    #: Set once an escalation has been decided, so duplicate signals while
    #: one is under way decide nothing.
    decided: Decision | None = None
    #: Final caller utterances seen so far.
    caller_turns: int = 0
    #: What the shadow rules would have done on this call.
    shadow_hits: list[dict[str, Any]] = field(default_factory=list)
    #: The turn whose sentence named a never-transfer phrase, if any.
    _held_turn: int | None = field(default=None, init=False)
    _lane: "EscalationEvaluator | None" = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._off = frozenset() if self.shadow_lane else self.policy.shadow_rules()
        if self._off and not self.shadow_lane:
            self._lane = EscalationEvaluator(
                self.policy,
                humans_available=self.humans_available,
                shadow_lane=True,
            )

    # --- the shadow lane ----------------------------------------------------

    def _shadow(self, real: Decision, lane: Decision | None) -> Decision:
        """Keep what a shadow rule would have done, then answer for real."""
        if lane is None or not lane.escalates or self._lane is None:
            return real
        if lane.rule in self._off and self.decided is None:
            if len(self.shadow_hits) < MAX_SHADOW_HITS:
                self.shadow_hits.append(
                    {
                        "rule": lane.rule,
                        "reason_code": lane.reason.value if lane.reason else None,
                        "topic": lane.topic,
                        "detail": lane.detail,
                        "would": lane.action.value,
                        "caller_turn": lane.turn,
                    }
                )
        # The lane is an observer: re-arm it so a later hit is seen too.
        self._lane.rearm()
        return real

    # --- inputs -----------------------------------------------------------

    def observe_text(self, text: str) -> Decision:
        """The caller's own words, read for hard triggers. One caller turn."""
        lane = self._lane.observe_text(text) if self._lane else None
        self.caller_turns += 1
        reading = read(
            text,
            custom_topics=self.policy.custom_topics,
            refund_limit=self.policy.refund_limit,
            never_topics=self.policy.never_transfer_topics,
            out_of_scope=[o.phrase for o in self.policy.out_of_scope_topics],
        )
        return self._shadow(self._observe_reading(reading), lane)

    def observe_reading(self, reading: Reading) -> Decision:
        lane = self._lane.observe_reading(reading) if self._lane else None
        return self._shadow(self._observe_reading(reading), lane)

    def _observe_reading(self, reading: Reading) -> Decision:
        if self.decided is not None:
            return NOTHING
        held = reading.never is not None
        if held:
            self._held_turn = self.caller_turns
        for topic in reading.topics:
            if topic not in self.policy.always_transfer_topics or topic in self._off:
                continue
            if held and topic not in EMERGENCY_CLASS:
                continue
            return self._escalate(ReasonCode.POLICY, f"Topic: {topic}", topic=topic)
        if not held:
            if reading.custom and "custom" not in self._off:
                return self._escalate(
                    ReasonCode.POLICY,
                    f"Topic: {reading.custom}",
                    topic="custom",
                    phrase=reading.custom,
                )
            if reading.out_of_scope and "out_of_scope" not in self._off:
                return self._escalate(
                    ReasonCode.POLICY,
                    f"Not this agent's job: {reading.out_of_scope}",
                    topic="out_of_scope",
                    phrase=reading.out_of_scope,
                )
        if "explicit_request" in self._off:
            return NOTHING
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
        lane = (
            self._lane.observe_signal(
                kind, topic=topic, level=level, step=step, detail=detail
            )
            if self._lane
            else None
        )
        return self._shadow(
            self._observe_signal(
                kind, topic=topic, level=level, step=step, detail=detail
            ),
            lane,
        )

    def _observe_signal(
        self,
        kind: str,
        *,
        topic: str | None,
        level: int | None,
        step: str | None,
        detail: str,
    ) -> Decision:
        if self.decided is not None:
            return NOTHING
        if kind == "explicit_request":
            return self._escalate(
                ReasonCode.EXPLICIT_REQUEST,
                detail or "The caller asked for a person",
            )
        if kind == "policy_topic":
            if (
                topic
                and topic in self.policy.always_transfer_topics
                and topic not in self._off
            ):
                if (
                    self._held_turn == self.caller_turns
                    and topic not in EMERGENCY_CLASS
                ):
                    # The sentence was about something the agent answers.
                    return NOTHING
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
            return self._observe_failure(kind, step=step)
        return NOTHING

    def observe_failure(self, kind: str, *, step: str | None = None) -> Decision:
        lane = self._lane.observe_failure(kind, step=step) if self._lane else None
        return self._shadow(self._observe_failure(kind, step=step), lane)

    def _observe_failure(self, kind: str, *, step: str | None = None) -> Decision:
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
        if self._lane is not None:
            self._lane.observe_quiet(kind)
        if self.decided is None:
            self.points[kind] += 1

    # --- the decision -----------------------------------------------------

    @property
    def score(self) -> int:
        looped = any(
            n >= self.policy.max_ai_attempts for n in self.step_failures.values()
        )
        return sum(self.points.values()) + (BASE_THRESHOLD if looped else 0)

    @property
    def _frustrated(self) -> bool:
        return self.frustration >= FRUSTRATION_LEVEL and "frustration" not in self._off

    def threshold(self) -> int:
        value = BASE_THRESHOLD
        if not self.humans_available():
            value += NO_HUMAN_RAISE
        if self._frustrated:
            value -= 1
        return max(1, value)

    def _soft_reason(self) -> ReasonCode:
        unfrustrated = self.threshold() + (1 if self._frustrated else 0)
        # Without the frustration, this score would at most have earned the
        # repair: the mood is what tipped it.
        if self._frustrated and self.score <= unfrustrated:
            return ReasonCode.FRUSTRATION
        failures = sum(self.points[k] for k in FAILURE_KINDS)
        if self.points["low_confidence"] > failures:
            return ReasonCode.LOW_CONFIDENCE
        return ReasonCode.REPAIR_LOOP

    def _evaluate_score(self) -> Decision:
        score = self.score
        if score < self.threshold():
            return NOTHING
        reason = self._soft_reason()
        if reason.value in self._off:
            # The rule is only watching: no repair and no transfer.
            return NOTHING
        if self.repaired_at is None:
            self.repaired_at = score
            return Decision(
                Action.REPAIR, reason, "One repair first", turn=self.caller_turns
            )
        if score > self.repaired_at:
            return self._escalate(reason, f"Score {score} after a repair")
        return NOTHING

    def _escalate(
        self,
        reason: ReasonCode,
        detail: str,
        *,
        topic: str | None = None,
        phrase: str | None = None,
    ) -> Decision:
        action = Action.TRANSFER if self.humans_available() else Action.CALLBACK
        self.decided = Decision(
            action, reason, detail, topic, turn=self.caller_turns, phrase=phrase
        )
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
    "MAX_SHADOW_HITS",
    "NO_HUMAN_RAISE",
    "NOTHING",
]
