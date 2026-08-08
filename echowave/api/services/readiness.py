"""The shared vocabulary for "is this deployment actually ready".

`services/privacy/readiness.py` asked this question first, about data
protection. `services/billing/readiness.py` asks the same shape of question
about money. They are separate assessments — a deployment can be privacy-ready
and billing-broken — but they report in one format so an operator reads one
kind of answer, and so the two cannot drift into disagreeing about what
"ready" means.

The distinction that makes these checks worth anything is between:

* **Configuration** — is the setting present. Cheap to check and worth little
  alone: a configured supplier identity on a deployment whose worker is dead
  issues exactly as many invoices as no configuration at all.
* **Evidence** — did the thing actually happen. Are there captured payments
  with no tax document against them; is there a rate on file for the providers
  we are billing. These are the checks that catch a deployment where the code
  is correct and the outcome is still wrong.

A fresh install has no evidence either way, and that is ``UNKNOWN`` rather than
a pass. Reporting "ready" because nothing has happened yet is the specific
dishonesty this vocabulary exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: A check that is satisfied.
READY = "ready"
#: A check that is not satisfied and can be fixed by whoever runs the platform.
ACTION_REQUIRED = "action_required"
#: Nothing has happened yet that would prove this either way. Not a pass.
UNKNOWN = "unknown"
#: An obligation no code can discharge. Never reports ``ready``.
NEEDS_A_HUMAN = "needs_a_human"


@dataclass(frozen=True)
class Check:
    """One obligation, and whether this deployment is meeting it."""

    key: str
    title: str
    status: str
    #: What is true right now, in a sentence someone can act on.
    detail: str
    #: The legal or documentary hook, so a reviewer can follow it up rather
    #: than trust us.
    reference: str
    #: What to do about it. Empty when the check is satisfied.
    remedy: str = ""


@dataclass(frozen=True)
class Readiness:
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def action_required(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == ACTION_REQUIRED)

    @property
    def blocking(self) -> tuple[Check, ...]:
        """What must be fixed before going live.

        Excludes ``needs_a_human`` deliberately — those never clear, so folding
        them in would make the count permanently non-zero and therefore
        ignored. They are reported separately for exactly that reason.
        """
        return self.action_required

    @property
    def unresolvable(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == NEEDS_A_HUMAN)


def as_dict(assessment: Readiness) -> dict:
    """The wire shape every readiness endpoint returns."""
    return {
        "checks": [
            {
                "key": check.key,
                "title": check.title,
                "status": check.status,
                "detail": check.detail,
                "reference": check.reference,
                "remedy": check.remedy or None,
            }
            for check in assessment.checks
        ],
        # Split apart because they are worked differently: one list is a
        # deployment checklist, the other is a to-do that never empties.
        "action_required": len(assessment.blocking),
        "needs_a_human": len(assessment.unresolvable),
    }
