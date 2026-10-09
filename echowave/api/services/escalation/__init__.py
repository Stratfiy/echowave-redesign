"""Escalation v2: when a call goes to a person, decided in code.

Roadmap item 2. A voice agent used to hand a caller to a person when the
model chose to call its transfer tool. That puts the decision a business
cares most about -- "a person must take this" -- inside a prompt, where it is
probabilistic and unreviewable. Here the model is one input and the decision
is code, read off the agent's own policy:

* ``policy`` -- what one agent's owner set: who to ring, when they are there,
  which topics always go to a person, how many tries the agent gets.
* ``signals`` -- what was said, read deterministically: an explicit request
  for a person, a policy topic, a refund over the limit.
* ``evaluator`` -- hard triggers transfer now; soft signals add to a score
  with one repair attempt first; no person free means a callback instead.
* ``record`` -- one idempotent row per escalation, moved through
  ``requested -> dialling -> briefing -> bridged -> completed | failed``.
* ``card`` -- the handoff card: who, what, what was done, why, in two
  sentences; spoken as the private briefing and shown in the app.
* ``ladder`` -- ring each person with a timeout, spoken updates while the
  caller waits, a total hold cap, and a machine on the line is never bridged.
* ``fallbacks`` -- after the ladder: callback task, message ticket, voicemail.
* ``runtime`` -- the glue to a live call.
* ``actions`` -- Accept, Decline and Hand back to AI from the card.

Everything here is behind ``escalation_v2``; off, transfers run exactly as
they did.
"""

from __future__ import annotations

from enum import Enum

from api.services import features

FLAG = "escalation_v2"


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


class ReasonCode(str, Enum):
    """Why a call went to a person. An enum, because these are counted."""

    EXPLICIT_REQUEST = "explicit_request"
    POLICY = "policy"
    REPAIR_LOOP = "repair_loop"
    LOW_CONFIDENCE = "low_confidence"
    FRUSTRATION = "frustration"


#: What each reason reads as on the card and in the thread.
REASON_LABELS: dict[str, str] = {
    ReasonCode.EXPLICIT_REQUEST.value: "Asked for a person",
    ReasonCode.POLICY.value: "Always goes to a person",
    ReasonCode.REPAIR_LOOP.value: "The agent could not get past a step",
    ReasonCode.LOW_CONFIDENCE.value: "The agent was not sure of key details",
    ReasonCode.FRUSTRATION.value: "The caller was getting frustrated",
}


def reason_label(code: str | None) -> str:
    # An unknown code shows as itself rather than as nothing (silent absence).
    return REASON_LABELS.get(code or "", code or "Handed to a person")


__all__ = ["FLAG", "REASON_LABELS", "ReasonCode", "enabled", "reason_label"]
