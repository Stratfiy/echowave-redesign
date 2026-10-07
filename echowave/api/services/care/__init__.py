"""Care for older people and their families (launch stream `care`).

* ``circle`` -- the family circle: who is in it, what each one sees, and the
  consent cards that add a member or widen what is shared.
* ``medicines`` and ``calls`` -- medicine reminders by phone call in the
  person's language, and the family alert when a dose is missed.
* ``scam`` -- is this message or call a scam? A plain answer and why.
* ``tech_help`` -- phone help one step at a time, with "did that work?".
* ``tools`` -- the same, offered to Decibyl in Chat while switched on.

Every part has its own flag (``FLAGS`` below) and is invisible while off.
See ``CARE.md`` at the repository's ``echowave/`` root.
"""

from __future__ import annotations

from api.services import features

SIMPLE_MODE = "care_simple_mode"
MEDICINE_CALLS = "care_medicine_calls"
SCAM_CHECK = "care_scam_check"
TECH_HELP = "care_tech_help"
FAMILY_CIRCLE = "care_family_circle"
FLAGS = (SIMPLE_MODE, MEDICINE_CALLS, SCAM_CHECK, TECH_HELP, FAMILY_CIRCLE)


def on(flag: str, organization_id: int | None = None) -> bool:
    return features.is_on(flag, organization_id)


class CareError(ValueError):
    """Refused, with a sentence the person can read."""


class NotFound(CareError):
    pass


class NeedsSetup(CareError):
    """The capability is not ready here; the sentence says what is missing."""
