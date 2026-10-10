"""The training loop, step 1: record what happened to each suggestion.

Decibyl will, in time, run models fine-tuned on how each workspace's owners
treat their agents' suggestions. That needs the record first, kept so that it
can be trained on, exported, and moved on-prem. This package is only that
record. It applies nothing, learns nothing and sends nothing anywhere.

The rules (the founder's, and final):

1. **Nothing learned ever ships without the owner's permission.** Every
   suggestion is a card the owner approves or rejects. This package has no
   path that changes an agent.
2. **Feedback on every suggestion and action is recorded** -- approved,
   rejected, edited then approved, undone, thumbs, an owner's correction, a
   failed eval, an escalation -- so it can be trained on.
3. **The weekly loop budget per agent** is the smaller of 2% of that agent's
   model spend over the last four weeks and a fixed ceiling (``budget.py``).
   Nothing runs a loop yet; this is only the number.
4. **Nothing is learned or shared across workspaces.** Every row, read and
   export names one workspace. There is no query that spans two.

What is recorded, and when:

* ``hooks.py`` is the only place the existing features (edit cards, action
  cards, thumbs, evals, escalations) call into this package, each with one
  line, and none of those lines can raise.
* ``record.py`` is the one writer: flag check, consent check, redaction, one
  idempotent insert.
* ``consent.py`` is the workspace's "Use my feedback to improve my agents"
  setting: on by default for the workspace itself, never shared. Off, nothing
  new is written. "Stop collecting" keeps what is there; "Stop and delete"
  archives it (out of export and training, kept only as the law requires,
  never hard-deleted).
* ``redact.py`` takes phone numbers, emails, Aadhaar and PAN numbers out of
  the text before it is stored.
* ``export.py`` writes a workspace's rows as JSONL: SFT examples and
  preference pairs.

Private things are never recorded: a person's own cards (their identity,
their care circle, their meeting follow-ups, their orders) and anything
marked private to one person. ``hooks.RECORDED_ACTIONS`` names the action
cards that are; a new kind of card is recorded only once somebody decides it
should be (``test_training_loop`` fails until they do).

Behind the ``training_loop`` flag (``TRAINING_LOOP_ENABLED``). Off, nothing
here reads or writes anything.
"""

from __future__ import annotations

from api.services import features

FLAG = "training_loop"

# Event types (``learning_events.event_type``).
SUGGESTION_SHOWN = "suggestion_shown"
APPROVED = "approved"
REJECTED = "rejected"
EDITED_THEN_APPROVED = "edited_then_approved"
UNDONE = "undone"
THUMBS_UP = "thumbs_up"
THUMBS_DOWN = "thumbs_down"
OWNER_CORRECTION = "owner_correction"
EVAL_FAIL = "eval_fail"
ESCALATION = "escalation"
#: Auto's routing: which model a piece of work was sent to, and why.
ROUTING_DECISION = "routing_decision"
#: What the next-best model would have said, run in the background on a safe
#: sample (``services/routing/explore.py``) and never shown to anybody.
ROUTING_COUNTERFACTUAL = "routing_counterfactual"

EVENT_TYPES = (
    SUGGESTION_SHOWN,
    APPROVED,
    REJECTED,
    EDITED_THEN_APPROVED,
    UNDONE,
    THUMBS_UP,
    THUMBS_DOWN,
    OWNER_CORRECTION,
    EVAL_FAIL,
    ESCALATION,
    ROUTING_DECISION,
    ROUTING_COUNTERFACTUAL,
)

#: The owner took what was offered, as offered or after changing it.
ACCEPTED_TYPES = (APPROVED, EDITED_THEN_APPROVED)
#: The owner ended up with a version of their own, to learn from.
OWNER_VERSION_TYPES = (APPROVED, EDITED_THEN_APPROVED, OWNER_CORRECTION)
#: The owner turned the model's output down.
TURNED_DOWN_TYPES = (REJECTED, UNDONE)

# Where an event came from (``learning_events.source``).
EDIT_CARD = "edit_card"
ACTION_CARD = "action_card"
REPLY = "reply"
EVAL = "eval"
ESCALATION_SOURCE = "escalation"
ROUTING = "routing"

GRANTED = "granted"
DECLINED = "declined"

# What a row is about (``learning_events.scope``).
SCOPE_AGENT = "agent"
#: Decibyl's own thread: no agent, ``workflow_id`` is NULL.
SCOPE_DECIBYL = "decibyl"
SCOPES = (SCOPE_AGENT, SCOPE_DECIBYL)

#: How long rows archived by "Stop and delete" are kept, in days, before they
#: may be removed. Kept only as the law requires, and never hard-deleted by
#: this package: nothing here purges, the constant is the one place the period
#: is written down and what the settings screen reports.
#:
#: PLACEHOLDER VALUE: counsel has not given the period yet. Change this one
#: line when they do.
ARCHIVE_RETENTION_DAYS = 365


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)
