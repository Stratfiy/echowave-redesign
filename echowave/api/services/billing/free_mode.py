"""Free while we are early: no plans, nothing charged, nothing locked.

The founder's call (October 2026): every account gets everything, for free,
until pricing is decided again. One switch, ``free_mode`` in the feature
registry (``FREE_MODE_ENABLED``, on by default), so it can be turned off
from the environment or the staff console -- for everyone, or one account
-- and every plan, trial, limit and charge comes back as it was. Nothing
about plans is deleted; each place that would block on one asks here first.

What "free" means, check by check:

- The plan an account is on is Free with voice and the whole knowledge base
  (``subscription_plans.plan_for_organization``, and through it
  ``assert_voice_allowed`` and the sandbox's plan list).
- Every plan limit is unlimited (``plan_limits.limit_for_organization``).
- There is no trial, so none ends (``trial.applies``).
- Nothing is charged and no balance floor stops work: the account is
  treated like one of ours (``internal_accounts.is_internal``), so a usage
  event debits nothing and a run never waits on credit. What a call cost the
  providers is still recorded, which is how we see what free costs us.
- Bringing your own model keys is allowed (``routes/provider_keys``).

Still in place, because they are not plans: KYC and autopay before a number
is bought (a number is real rent to a carrier every month), and the spend
budgets a customer sets on their own agents.
"""

from __future__ import annotations

from api.services import features

FLAG = "free_mode"


def on(organization_id: int | None = None) -> bool:
    """Is this account (or, with no id, the deployment) in free mode?"""
    return features.is_on(FLAG, organization_id)
