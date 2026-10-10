"""Personal adaptation: Decibyl learns the person (``evolve_personal``).

The plan's first learning loop (research-intelligence-plan section 7,
"Personal adaptation"): an explicit preference is saved within that person's
scope straight away and applied, with no model retraining and no model
trusted to remember it. Four parts:

* ``capture`` -- reads an explicit preference out of a line, with no model.
* ``preferences`` -- the person's own store (``personal_preferences``):
  owner, tenant, kind, value, source line, observed and effective time,
  review time, status and supersession; and the deterministic readings code
  applies (language for a call, when calls may ring, a reminder's channel).
* ``cards`` -- what the thread shows: "what you know about me", a preference
  just saved, and one offered from feedback, each with Correct and Forget.
* ``context_control`` -- what a conversation uses, and leaving a source out.

Authorisation, tenant boundaries, recipients and spend limits are not here
and cannot be reached from here: no preference kind names a recipient, a
permission or an amount, and every reading code applies can only narrow
(``preferences.held_until`` never returns a time the calling window would
not already allow).
"""

from __future__ import annotations

from api.services import features

FLAG = "evolve_personal"


def enabled(organization_id: int | None = None) -> bool:
    return features.is_on(FLAG, organization_id)


__all__ = ["FLAG", "enabled"]
