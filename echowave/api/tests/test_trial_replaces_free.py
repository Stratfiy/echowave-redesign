"""PLAN-1 (KAN-255): no Free plan; a time-boxed trial replaces it.

With ``trial_plan`` off nothing changes. With it on, a plan-less account is
on ``trial``; inside the window it can run (including the phone and the
sandbox); after it, reading stays open and every new run is refused at the
one place runs start, with a message that offers the plans.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api import constants
from api.services import quota_service
from api.services.billing import plan_limits, subscription_plans, trial
from api.services.sandbox import code_mode


@pytest.fixture
def trial_on(monkeypatch):
    monkeypatch.setattr(constants, "TRIAL_PLAN_ENABLED", True)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")


@pytest.fixture
def trial_off(monkeypatch):
    monkeypatch.setattr(constants, "TRIAL_PLAN_ENABLED", False)
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", "")


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def test_window_table(monkeypatch):
    monkeypatch.setenv("TRIAL_DAYS", "14")
    monkeypatch.setenv("TRIAL_PLAN_STARTS_AT", "2026-10-04T00:00:00+05:30")
    floor = trial.starts_at_floor()

    # An account from before launch gets its full window from launch day.
    old = datetime(2026, 8, 1, tzinfo=UTC)
    starts, ends = trial.window(created_at=old, override_ends_at=None)
    assert starts == floor and ends == floor + timedelta(days=14)

    # An account made after launch starts when it was made.
    new = floor + timedelta(days=3)
    starts, ends = trial.window(created_at=new, override_ends_at=None)
    assert starts == new and ends == new + timedelta(days=14)

    # Staff override wins.
    override = floor + timedelta(days=60)
    _, ends = trial.window(created_at=new, override_ends_at=override)
    assert ends == override

    # A naive timestamp is read as UTC rather than crashing.
    starts, _ = trial.window(created_at=datetime(2026, 11, 1), override_ends_at=None)
    assert starts.tzinfo is not None


def test_bad_settings_fall_back_rather_than_break(monkeypatch):
    monkeypatch.setenv("TRIAL_DAYS", "abc")
    monkeypatch.setenv("TRIAL_PLAN_STARTS_AT", "not a date")
    assert trial.trial_days() == 14
    assert trial.starts_at_floor().year == 2026


def test_the_ended_message_names_the_day_and_keeps_the_data():
    msg = trial.ended_message(datetime(2026, 10, 18, tzinfo=UTC))
    assert "18 October" in msg
    assert "still here" in msg


# ---------------------------------------------------------------------------
# Which plan a plan-less account is on
# ---------------------------------------------------------------------------


async def _org(db_session, name: str, *, created_at: datetime | None = None):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"trial-{name}")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        org_provider_id=f"org-trial-{name}", user_id=user.id
    )
    if created_at is not None:
        from api.db.models import OrganizationModel

        async with db_session.async_session() as session:
            row = await session.get(OrganizationModel, org.id)
            row.created_at = created_at
            await session.flush()
    return org


async def test_flag_off_keeps_free(db_session, trial_off):
    org = await _org(db_session, "off")
    async with db_session.async_session() as session:
        plan = await subscription_plans.plan_for_organization(
            session, organization_id=org.id
        )
        status = await trial.status(session, organization_id=org.id)
    assert plan.code == subscription_plans.FREE
    assert status.on_trial is False


async def test_flag_on_puts_a_planless_account_on_the_trial(db_session, trial_on):
    org = await _org(db_session, "on")
    async with db_session.async_session() as session:
        await subscription_plans.ensure_seeded(session)
        plan = await subscription_plans.plan_for_organization(
            session, organization_id=org.id
        )
        status = await trial.status(session, organization_id=org.id)
    assert plan.code == subscription_plans.TRIAL
    assert plan.voice_allowed is True
    assert status.on_trial is True
    assert status.active is True
    assert status.ends_at is not None and status.days_left >= 1


async def test_per_org_override_turns_the_trial_on_for_one_account(
    db_session, monkeypatch
):
    monkeypatch.setattr(constants, "TRIAL_PLAN_ENABLED", False)
    listed = await _org(db_session, "listed")
    other = await _org(db_session, "other")
    monkeypatch.setattr(constants, "FEATURE_ORG_OVERRIDES", f"trial_plan:{listed.id}")
    async with db_session.async_session() as session:
        a = await subscription_plans.plan_for_organization(
            session, organization_id=listed.id
        )
        b = await subscription_plans.plan_for_organization(
            session, organization_id=other.id
        )
    assert a.code == subscription_plans.TRIAL
    assert b.code == subscription_plans.FREE


async def test_an_expired_window_reads_as_ended(db_session, trial_on, monkeypatch):
    monkeypatch.setenv("TRIAL_PLAN_STARTS_AT", "2020-01-01T00:00:00+00:00")
    org = await _org(db_session, "expired", created_at=datetime(2020, 1, 2, tzinfo=UTC))
    async with db_session.async_session() as session:
        status = await trial.status(session, organization_id=org.id)
    assert status.on_trial is True
    assert status.active is False
    assert status.days_left == 0


async def test_a_staff_extension_reopens_the_window(db_session, trial_on, monkeypatch):
    monkeypatch.setenv("TRIAL_PLAN_STARTS_AT", "2020-01-01T00:00:00+00:00")
    org = await _org(
        db_session, "extended", created_at=datetime(2020, 1, 2, tzinfo=UTC)
    )
    from api.db.models import OrganizationModel

    async with db_session.async_session() as session:
        row = await session.get(OrganizationModel, org.id)
        row.trial_ends_at = datetime.now(UTC) + timedelta(days=7)
        await session.flush()
        status = await trial.status(session, organization_id=org.id)
    assert status.active is True


# ---------------------------------------------------------------------------
# The trial's shape: caps, phone, sandbox
# ---------------------------------------------------------------------------


def test_the_trial_can_run_scripts_and_free_still_cannot():
    assert "trial" in code_mode.ALLOWED_PLANS
    assert "free" not in code_mode.ALLOWED_PLANS


def test_the_trial_has_caps_for_every_limit_and_one_concurrent_call():
    caps = plan_limits.SEED["trial"]
    assert set(caps) == {spec.key for spec in plan_limits.LIMITS}
    assert caps["concurrent_calls"] == 1


def test_the_trial_is_seeded_but_never_sold():
    seed = subscription_plans.TRIAL_SEED
    assert seed["code"] == "trial"
    assert all(s["code"] != "trial" for s in subscription_plans.LADDER_SEED)
    assert seed["purchasable"] is False
    assert seed["enabled"] is False
    assert seed["voice_allowed"] is True
    assert seed["price_paise"] == 0


# ---------------------------------------------------------------------------
# Where runs start: an ended trial refuses, with the plans in the message
# ---------------------------------------------------------------------------


async def test_an_ended_trial_refuses_new_runs(monkeypatch):
    workflow = SimpleNamespace(id=5, user_id=9, workflow_configurations={})
    monkeypatch.setattr(
        quota_service.db_client, "get_workflow", AsyncMock(return_value=workflow)
    )
    monkeypatch.setattr(
        quota_service.db_client,
        "get_user_by_id",
        AsyncMock(return_value=SimpleNamespace(id=9)),
    )
    monkeypatch.setattr(quota_service, "_has_credit", AsyncMock(return_value=True))
    monkeypatch.setattr(
        quota_service.budgets,
        "evaluate_in_own_session",
        AsyncMock(return_value=SimpleNamespace(allowed=True)),
    )
    ended = trial.TrialStatus(
        on_trial=True,
        active=False,
        starts_at=datetime(2026, 10, 4, tzinfo=UTC),
        ends_at=datetime(2026, 10, 18, tzinfo=UTC),
    )
    monkeypatch.setattr(
        quota_service.trial, "status_in_own_session", AsyncMock(return_value=ended)
    )
    minted = AsyncMock()
    monkeypatch.setattr(quota_service, "_mint_managed_model_correlation", minted)

    result = await quota_service._authorize_workflow_run_start(
        workflow_id=5, organization_id=1
    )

    assert result.has_quota is False
    assert result.error_code == "trial_ended"
    assert "18 October" in result.error_message
    minted.assert_not_awaited()
