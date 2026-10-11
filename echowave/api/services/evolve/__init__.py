"""Evolving skills: workflow learning with evaluation, approval and rollback.

Decibyl should get better at a person's work without ever changing itself
silently. Plan section 7 ("Workflow learning") and audit item R2, as code:

* ``experience`` -- a durable, content-minimised ledger of task attempts:
  which skill version and tools were used, and what the *persisted* records
  say happened (an app's own reply, the carrier's answer time, a person's
  message, a card they discarded or edited). A model saying "I booked it"
  is never evidence. Unique by organisation, run and kind; idempotent.
* ``lessons`` -- an offline job turns clusters of corrections and failures
  into small, itemised lesson deltas to a skill's playbook (ACE: generation,
  reflection, curation), each a new skill version with its evidence and
  cost. Never from the model's own self-assessment.
* ``evaluate`` -- the gate: the candidate against the current version on
  held-out related cases (a frozen split, never the cases that taught it)
  and on unrelated cases (negative transfer). Not offered unless it helps
  the first and harms none of the second.
* ``versions`` -- the card ("I've learned a better way to ..."), publish by
  a person only, version history with authors, one-click rollback.
* ``monitor`` -- after a promotion, later unseen tasks are compared with the
  version before; worse outcomes put a card up offering to roll back.
* ``remember`` -- "Remember this as my way of doing it": an editable draft
  from a conversation, through the same versions and the same card.
* ``guard`` -- the hard rule. A skill is a procedure; permissions, tenant
  boundaries, recipients, spend limits and calling hours stay in
  deterministic code, and a candidate that touches them is rejected.

Everything here is behind ``evolve_skills``; off, skills read exactly as
they did and nothing is recorded.
"""

from __future__ import annotations

from api.services import features

FLAG = "evolve_skills"

# Version states. A version only moves forward through these, by
# compare-and-swap (db/evolve_client.move_skill_version).
DRAFT = "draft"
REJECTED = "rejected"
OFFERED = "offered"
PUBLISHED = "published"
ROLLED_BACK = "rolled_back"
DISCARDED = "discarded"

# Where a version came from.
ORIGIN_PERSON = "person"
ORIGIN_LEARNED = "learned"
ORIGIN_REMEMBERED = "remembered"

# Experience kinds.
ATTEMPT = "attempt"
CORRECTION = "correction"
REJECTED_CARD = "rejected_card"
KINDS = (ATTEMPT, CORRECTION, REJECTED_CARD)


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


__all__ = [
    "ATTEMPT",
    "CORRECTION",
    "DISCARDED",
    "DRAFT",
    "FLAG",
    "KINDS",
    "OFFERED",
    "ORIGIN_LEARNED",
    "ORIGIN_PERSON",
    "ORIGIN_REMEMBERED",
    "PUBLISHED",
    "REJECTED",
    "REJECTED_CARD",
    "ROLLED_BACK",
    "enabled",
]
