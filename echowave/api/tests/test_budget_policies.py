"""S-1: spend caps on the workspace and on one agent, after paperclip's shape.

A cap is a policy: scope, window, amount, a warning at a share of it, a hard
stop at it. Spend is read from the ledger's usage rows, which is why every
debit now carries the agent that made it; a crossed threshold is an incident,
once per window; and the check sits in the one place every run starts.
Nothing reads a policy until ``BUDGET_POLICIES_ENABLED`` is on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from api import constants
from api.db.models import (
    BudgetIncidentModel,
    CreditLedgerModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CreditLedgerKind
from api.services.billing import budgets, events, reservations
from api.services.billing.credits import PAISE_PER_CREDIT

pytestmark = pytest.mark.asyncio

CREDIT = PAISE_PER_CREDIT


@pytest.fixture
def budgets_on(monkeypatch):
    monkeypatch.setattr(constants, "BUDGET_POLICIES_ENABLED", True)


@pytest.fixture
def budgets_off(monkeypatch):
    monkeypatch.setattr(constants, "BUDGET_POLICIES_ENABLED", False)


async def _org(session, slug: str, *, credits: int = 1_000):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    session.add(org)
    await session.flush()
    session.add(
        CreditLedgerModel(
            organization_id=org.id,
            delta_paise=credits * CREDIT,
            kind=CreditLedgerKind.TOPUP.value,
            balance_after_paise=credits * CREDIT,
        )
    )
    await session.flush()
    return org


async def _user(session, slug: str):
    user = UserModel(provider_id=f"user-{slug}", email=f"{slug}@example.com")
    session.add(user)
    await session.flush()
    return user


async def _agent(session, org, user, name: str):
    wf = WorkflowModel(name=name, user_id=user.id, organization_id=org.id)
    session.add(wf)
    await session.flush()
    return wf


async def _spend(session, org, *, credits: int, workflow_id=None, when=None):
    """A usage debit the way any charge writes one."""
    await events.charge(
        session,
        organization_id=org.id,
        event=events.TEXT_REPLY,
        ref_id=f"turn-{datetime.now(UTC).timestamp()}-{credits}-{workflow_id}",
        quantity=credits,
        workflow_id=workflow_id,
    )
    if when is not None:
        row = await session.scalar(
            select(CreditLedgerModel)
            .where(CreditLedgerModel.organization_id == org.id)
            .order_by(CreditLedgerModel.id.desc())
        )
        row.created_at = when
        await session.flush()


class TestTheLedgerKnowsTheAgent:
    async def test_an_event_debit_carries_the_bot_that_did_the_work(
        self, async_session
    ):
        org = await _org(async_session, "stamp")
        user = await _user(async_session, "stamp")
        bot = await _agent(async_session, org, user, "Front Desk")
        await _spend(async_session, org, credits=2, workflow_id=bot.id)
        row = await async_session.scalar(
            select(CreditLedgerModel).where(
                CreditLedgerModel.organization_id == org.id,
                CreditLedgerModel.kind == CreditLedgerKind.USAGE.value,
            )
        )
        assert row.workflow_id == bot.id

    async def test_agent_spend_is_one_sum_over_the_ledger(self, async_session):
        org = await _org(async_session, "sum")
        user = await _user(async_session, "sum")
        a = await _agent(async_session, org, user, "A")
        b = await _agent(async_session, org, user, "B")
        await _spend(async_session, org, credits=3, workflow_id=a.id)
        await _spend(async_session, org, credits=5, workflow_id=b.id)
        await _spend(async_session, org, credits=7)
        start = datetime.now(UTC) - timedelta(days=1)
        assert (
            await budgets.spend_paise(
                async_session,
                organization_id=org.id,
                workflow_id=a.id,
                start=start,
                end=None,
            )
            == 3 * CREDIT
        )
        assert (
            await budgets.spend_paise(
                async_session,
                organization_id=org.id,
                workflow_id=None,
                start=start,
                end=None,
            )
            == 15 * CREDIT
        )


class TestFlagOff:
    async def test_no_policy_is_read_and_every_run_is_allowed(
        self, async_session, budgets_off
    ):
        org = await _org(async_session, "off")
        async_session.add(
            __import__(
                "api.db.models", fromlist=["BudgetPolicyModel"]
            ).BudgetPolicyModel(
                organization_id=org.id,
                window_kind=budgets.WINDOW_LIFETIME,
                amount_paise=0,
            )
        )
        await async_session.flush()
        verdict = await budgets.evaluate(
            async_session, organization_id=org.id, workflow_id=None
        )
        assert verdict.allowed
        assert verdict.standings == ()


class TestSettingACap:
    async def test_one_live_policy_per_scope_and_window(
        self, async_session, budgets_on
    ):
        org = await _org(async_session, "set")
        first = await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_CALENDAR_MONTH,
            amount_credits=500,
        )
        second = await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_CALENDAR_MONTH,
            amount_credits=800,
            warn_percent=50,
        )
        assert second.id == first.id
        assert second.amount_paise == 800 * CREDIT
        assert second.warn_percent == 50
        assert (
            len(await budgets.list_policies(async_session, organization_id=org.id)) == 1
        )

    async def test_an_agent_from_another_workspace_is_refused(
        self, async_session, budgets_on
    ):
        org = await _org(async_session, "mine")
        other = await _org(async_session, "theirs")
        user = await _user(async_session, "theirs")
        theirs = await _agent(async_session, other, user, "Theirs")
        with pytest.raises(budgets.BudgetError):
            await budgets.set_policy(
                async_session,
                organization_id=org.id,
                workflow_id=theirs.id,
                window_kind=budgets.WINDOW_LIFETIME,
                amount_credits=10,
            )

    async def test_bad_inputs_are_refused(self, async_session, budgets_on):
        org = await _org(async_session, "bad")
        with pytest.raises(budgets.BudgetError):
            await budgets.set_policy(
                async_session,
                organization_id=org.id,
                workflow_id=None,
                window_kind="fortnight",
                amount_credits=10,
            )
        with pytest.raises(budgets.BudgetError):
            await budgets.set_policy(
                async_session,
                organization_id=org.id,
                workflow_id=None,
                window_kind=budgets.WINDOW_LIFETIME,
                amount_credits=-1,
            )


class TestTheCapBites:
    async def test_an_agent_at_its_cap_is_refused_and_its_siblings_are_not(
        self, async_session, budgets_on
    ):
        org = await _org(async_session, "bite")
        user = await _user(async_session, "bite")
        prospecting = await _agent(async_session, org, user, "Prospecting")
        front_desk = await _agent(async_session, org, user, "Front Desk")
        await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=prospecting.id,
            window_kind=budgets.WINDOW_CALENDAR_MONTH,
            amount_credits=10,
        )
        await _spend(async_session, org, credits=10, workflow_id=prospecting.id)

        refused = await budgets.evaluate(
            async_session, organization_id=org.id, workflow_id=prospecting.id
        )
        assert not refused.allowed
        assert refused.stopped_by.policy.workflow_id == prospecting.id
        assert "10 of 10 credits this month" in refused.message

        allowed = await budgets.evaluate(
            async_session, organization_id=org.id, workflow_id=front_desk.id
        )
        assert allowed.allowed

    async def test_a_workspace_cap_covers_every_agent(self, async_session, budgets_on):
        org = await _org(async_session, "ws")
        user = await _user(async_session, "ws")
        a = await _agent(async_session, org, user, "A")
        b = await _agent(async_session, org, user, "B")
        await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_LIFETIME,
            amount_credits=8,
        )
        await _spend(async_session, org, credits=5, workflow_id=a.id)
        await _spend(async_session, org, credits=3, workflow_id=b.id)
        for agent in (a, b):
            verdict = await budgets.evaluate(
                async_session, organization_id=org.id, workflow_id=agent.id
            )
            assert not verdict.allowed

    async def test_a_warning_never_refuses(self, async_session, budgets_on):
        org = await _org(async_session, "warn")
        await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_LIFETIME,
            amount_credits=10,
            warn_percent=80,
        )
        await _spend(async_session, org, credits=8)
        verdict = await budgets.evaluate(
            async_session, organization_id=org.id, workflow_id=None
        )
        assert verdict.allowed
        assert verdict.standings[0].warned

    async def test_a_soft_cap_warns_at_the_limit_but_does_not_stop(
        self, async_session, budgets_on
    ):
        org = await _org(async_session, "soft")
        await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_LIFETIME,
            amount_credits=4,
            hard_stop=False,
        )
        await _spend(async_session, org, credits=6)
        verdict = await budgets.evaluate(
            async_session, organization_id=org.id, workflow_id=None
        )
        assert verdict.allowed
        assert verdict.standings[0].exhausted

    async def test_last_months_spend_does_not_count_this_month(
        self, async_session, budgets_on
    ):
        org = await _org(async_session, "month")
        await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_CALENDAR_MONTH,
            amount_credits=5,
        )
        await _spend(
            async_session, org, credits=9, when=datetime.now(UTC) - timedelta(days=45)
        )
        verdict = await budgets.evaluate(
            async_session, organization_id=org.id, workflow_id=None
        )
        assert verdict.allowed
        assert verdict.standings[0].observed_paise == 0

    async def test_a_lifetime_cap_starts_when_it_was_set(
        self, async_session, budgets_on
    ):
        org = await _org(async_session, "life")
        await _spend(
            async_session, org, credits=9, when=datetime.now(UTC) - timedelta(days=2)
        )
        await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_LIFETIME,
            amount_credits=5,
        )
        verdict = await budgets.evaluate(
            async_session, organization_id=org.id, workflow_id=None
        )
        assert verdict.allowed


class TestIncidents:
    async def test_a_charge_past_the_cap_opens_one_incident_per_threshold(
        self, async_session, budgets_on
    ):
        org = await _org(async_session, "inc")
        policy = await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_LIFETIME,
            amount_credits=10,
        )
        await _spend(async_session, org, credits=8)
        await _spend(async_session, org, credits=3)
        await _spend(async_session, org, credits=3)
        rows = await budgets.list_incidents(async_session, organization_id=org.id)
        assert {r.threshold for r in rows} == {
            budgets.THRESHOLD_WARN,
            budgets.THRESHOLD_HARD,
        }
        assert all(r.policy_id == policy.id for r in rows)
        assert len(rows) == 2

    async def test_raising_the_cap_resolves_the_stop(self, async_session, budgets_on):
        org = await _org(async_session, "raise")
        await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_LIFETIME,
            amount_credits=4,
        )
        await _spend(async_session, org, credits=4)
        assert not (
            await budgets.evaluate(
                async_session, organization_id=org.id, workflow_id=None
            )
        ).allowed
        await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_LIFETIME,
            amount_credits=100,
        )
        assert (
            await budgets.evaluate(
                async_session, organization_id=org.id, workflow_id=None
            )
        ).allowed
        open_rows = await budgets.list_incidents(async_session, organization_id=org.id)
        assert open_rows == []
        resolved = await async_session.scalars(
            select(BudgetIncidentModel).where(
                BudgetIncidentModel.organization_id == org.id,
                BudgetIncidentModel.status == budgets.STATUS_RESOLVED,
            )
        )
        assert len(list(resolved)) == 2

    async def test_removing_a_policy_keeps_its_incidents_and_lifts_the_stop(
        self, async_session, budgets_on
    ):
        org = await _org(async_session, "rm")
        policy = await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_LIFETIME,
            amount_credits=2,
        )
        await _spend(async_session, org, credits=2)
        assert await budgets.remove_policy(
            async_session, organization_id=org.id, policy_id=policy.id
        )
        assert (
            await budgets.evaluate(
                async_session, organization_id=org.id, workflow_id=None
            )
        ).allowed
        kept = await budgets.list_incidents(
            async_session, organization_id=org.id, open_only=False
        )
        assert kept and all(r.status == budgets.STATUS_RESOLVED for r in kept)

    async def test_a_dismissed_incident_stays_dismissed(
        self, async_session, budgets_on
    ):
        org = await _org(async_session, "dismiss")
        await budgets.set_policy(
            async_session,
            organization_id=org.id,
            workflow_id=None,
            window_kind=budgets.WINDOW_LIFETIME,
            amount_credits=2,
            hard_stop=False,
        )
        await _spend(async_session, org, credits=2)
        rows = await budgets.list_incidents(async_session, organization_id=org.id)
        for row in rows:
            assert await budgets.dismiss_incident(
                async_session, organization_id=org.id, incident_id=row.id
            )
        await _spend(async_session, org, credits=1)
        assert await budgets.list_incidents(async_session, organization_id=org.id) == []


class TestACallIsStampedToo:
    async def test_a_costed_run_debits_against_its_agent(
        self, async_session, budgets_on, monkeypatch
    ):
        from api.services.billing import costing

        monkeypatch.setattr(reservations, "BALANCE_ENFORCEMENT_ENABLED", False)
        org = await _org(async_session, "call")
        user = await _user(async_session, "call")
        bot = await _agent(async_session, org, user, "Front Desk")
        run = WorkflowRunModel(
            name="call", workflow_id=bot.id, mode="chat", usage_info={}, cost_info={}
        )
        async_session.add(run)
        await async_session.flush()
        await costing._debit_ledger(
            async_session,
            organization_id=org.id,
            workflow_run_id=run.id,
            workflow_id=bot.id,
            amount_paise=6 * CREDIT,
            recost=False,
        )
        row = await async_session.scalar(
            select(CreditLedgerModel).where(
                CreditLedgerModel.ref_type == "workflow_run",
                CreditLedgerModel.ref_id == str(run.id),
            )
        )
        assert row.workflow_id == bot.id
