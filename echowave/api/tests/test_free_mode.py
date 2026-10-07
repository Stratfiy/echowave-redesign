"""Free while we are early: one switch opens every plan gate (free_mode.py)."""

import pytest

from api import constants
from api.services import features
from api.services.billing import (
    free_mode,
    internal_accounts,
    plan_limits,
    subscription_plans,
    trial,
)


@pytest.fixture
def free(monkeypatch):
    monkeypatch.setattr(constants, "FREE_MODE_ENABLED", True)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")
    features.clear_snapshot()


@pytest.fixture
def paid(monkeypatch):
    monkeypatch.setattr(constants, "FREE_MODE_ENABLED", False)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")
    features.clear_snapshot()


def test_registered_and_reported_to_the_ui(free):
    assert features.FLAGS["free_mode"] == "FREE_MODE_ENABLED"
    assert features.public()["free_mode"] is True


def test_the_suite_runs_with_it_off(paid):
    assert free_mode.on(7) is False


def test_no_trial_while_free(free, monkeypatch):
    monkeypatch.setattr(constants, "TRIAL_PLAN_ENABLED", True)
    assert trial.applies(7) is False


def test_the_trial_comes_back_when_switched_off(paid, monkeypatch):
    monkeypatch.setattr(constants, "TRIAL_PLAN_ENABLED", True)
    assert trial.applies(7) is True


async def test_every_limit_is_unlimited(free):
    # Free mode answers before the session is touched.
    limit = await plan_limits.limit_for_organization(
        None, organization_id=7, key="bots"
    )
    assert limit.unlimited
    assert limit.raise_to is None


async def test_an_unknown_limit_is_still_a_mistake(free):
    with pytest.raises(KeyError):
        await plan_limits.limit_for_organization(None, organization_id=7, key="nope")


async def test_every_account_is_billed_like_ours(free):
    assert await internal_accounts.is_internal(None, 7) is True
    # No organisation still asks for list pricing.
    assert await internal_accounts.is_internal(None, None) is False


async def test_the_plan_allows_voice_and_the_whole_knowledge_base(free, monkeypatch):
    async def no_rows(session, *, code):
        return None

    monkeypatch.setattr(subscription_plans, "get_plan", no_rows)
    plan = await subscription_plans.plan_for_organization(None, organization_id=7)
    assert plan.code == subscription_plans.FREE
    assert plan.voice_allowed is True
    assert plan.knowledge_base_bytes == constants.STAFF_KNOWLEDGE_BASE_BYTES
    assert (
        await subscription_plans.assert_voice_allowed(None, organization_id=7) == plan
    )


async def test_the_knowledge_base_is_not_capped(free):
    allowance = await subscription_plans.knowledge_base_allowance_for(
        None, organization_id=7
    )
    assert allowance == subscription_plans.STAFF_KNOWLEDGE_BASE
