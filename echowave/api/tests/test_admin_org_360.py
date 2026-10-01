"""Org 360 on the staff accounts screens (ADMIN-2, A2).

The list and the drill-down gained plan, trial, agents, linked apps, KYC, own
keys and recent failures, so staff can tell whether an account is healthy
without SSH. Each fact must belong to the account it is shown on -- another
account's agents or failures leaking into a row is the tenancy bug this file
guards against -- and the list must stay grouped queries, not one per row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event

from api.db import billing_dashboard_client as dash
from api.db import org_health_client
from api.db.channel_identity_models import ChannelIdentityModel
from api.db.models import (
    AgentEventModel,
    OrganizationKycModel,
    OrganizationModel,
    OrganizationProviderCredentialModel,
    PaymentMandateModel,
    UserModel,
    WorkflowModel,
)
from api.enums import AgentEventKind, MandateStatus, WorkflowStatus
from api.services.billing import trial

TODAY = datetime.now(UTC).date()
RANGE = {"start": TODAY - timedelta(days=30), "end": TODAY}


async def _org(session, slug: str, **kwargs) -> OrganizationModel:
    org = OrganizationModel(
        provider_id=f"org-360-{slug}", quota_decibyl_tokens=0, **kwargs
    )
    session.add(org)
    await session.flush()
    return org


async def _user(session, slug: str) -> UserModel:
    user = UserModel(provider_id=f"user-360-{slug}", email=f"{slug}@x.example")
    session.add(user)
    await session.flush()
    return user


async def _furnish(session, org, user, *, agents=2, live=1, failures=0):
    for i in range(agents):
        session.add(
            WorkflowModel(
                name=f"agent-{org.id}-{i}",
                organization_id=org.id,
                is_live=i < live,
            )
        )
    session.add(
        WorkflowModel(
            name=f"archived-{org.id}",
            organization_id=org.id,
            status=WorkflowStatus.ARCHIVED.value,
            is_live=True,
        )
    )
    session.add(
        ChannelIdentityModel(
            organization_id=org.id,
            user_id=user.id,
            channel="whatsapp",
            external_id=f"+9100000{org.id}",
        )
    )
    base = datetime.now(UTC) - timedelta(hours=1)
    for i in range(failures):
        session.add(
            AgentEventModel(
                organization_id=org.id,
                kind=AgentEventKind.COULD_NOT.value,
                actor="agent",
                summary=f"failure {i} in {org.id}",
                at=base + timedelta(minutes=i),
            )
        )
    await session.flush()


@pytest.fixture
def trial_on(monkeypatch):
    monkeypatch.setattr(trial, "applies", lambda organization_id: True)


@pytest.fixture
def trial_off(monkeypatch):
    monkeypatch.setattr(trial, "applies", lambda organization_id: False)


async def _row(session, org):
    rows = await dash.accounts_summary(session, **RANGE)
    return next(r for r in rows if r["organization_id"] == org.id)


@pytest.mark.asyncio
class TestTheListRow:
    async def test_counts_belong_to_their_own_account(self, async_session, trial_on):
        mine = await _org(async_session, "mine")
        theirs = await _org(async_session, "theirs")
        me = await _user(async_session, "mine")
        them = await _user(async_session, "theirs")
        await _furnish(async_session, mine, me, agents=3, live=2)
        await _furnish(async_session, theirs, them, agents=1, live=0)
        async_session.add(
            ChannelIdentityModel(
                organization_id=theirs.id,
                user_id=them.id,
                channel="slack",
                external_id=f"T1:U{theirs.id}",
            )
        )
        await async_session.flush()

        row = await _row(async_session, mine)

        assert row["agents_count"] == 3  # the archived one is not counted
        assert row["live_agents_count"] == 2
        assert row["channels"]["whatsapp"] == 1
        assert row["channels"]["slack"] == 0  # theirs, not mine
        assert row["channels_linked"] == 1
        other = await _row(async_session, theirs)
        assert other["agents_count"] == 1
        assert other["channels"]["slack"] == 1

    async def test_every_known_app_shows_even_at_zero(self, async_session, trial_off):
        org = await _org(async_session, "bare")
        row = await _row(async_session, org)
        assert set(row["channels"]) >= {"whatsapp", "telegram", "slack", "teams"}
        assert row["channels_linked"] == 0
        assert row["agents_count"] == 0

    async def test_a_new_app_is_shown_under_its_own_name(
        self, async_session, trial_off
    ):
        """Counted from the rows, not an allowlist: a channel added next
        month must not vanish from the screen."""
        org = await _org(async_session, "new-app")
        user = await _user(async_session, "new-app")
        async_session.add(
            ChannelIdentityModel(
                organization_id=org.id,
                user_id=user.id,
                channel="signal",
                external_id=f"sig-{org.id}",
            )
        )
        await async_session.flush()
        row = await _row(async_session, org)
        assert row["channels"]["signal"] == 1

    async def test_plan_trial_kyc_and_own_keys(self, async_session, trial_on):
        ends = datetime.now(UTC) + timedelta(days=2)
        org = await _org(async_session, "trial", trial_ends_at=ends)
        async_session.add(
            OrganizationKycModel(organization_id=org.id, status="submitted")
        )
        async_session.add(
            OrganizationProviderCredentialModel(
                organization_id=org.id,
                component="llm",
                provider="openai",
                encrypted_key="x",
                key_last_four="abcd",
            )
        )
        await async_session.flush()

        row = await _row(async_session, org)

        assert row["plan"] == "trial"
        assert row["plan_is_paid"] is False
        assert row["trial"]["on_trial"] is True
        assert row["trial"]["stage"] == "ending_soon"
        assert row["trial"]["override"] is True
        assert row["trial_ends_at"] == ends.isoformat()
        assert row["kyc_status"] == "submitted"
        assert row["byok_keys_present"] is True
        assert row["byok_providers"] == ["openai"]

    async def test_an_ended_trial_reads_as_ended(self, async_session, trial_on):
        org = await _org(
            async_session,
            "ended",
            trial_ends_at=datetime.now(UTC) - timedelta(hours=1),
        )
        row = await _row(async_session, org)
        assert row["trial"]["stage"] == "ended"
        assert row["trial"]["active"] is False
        assert row["trial"]["notice_stage"] == "ended"

    async def test_a_paid_plan_is_not_on_trial(self, async_session, trial_on):
        org = await _org(async_session, "paid")
        async_session.add(
            PaymentMandateModel(
                organization_id=org.id,
                provider="razorpay",
                purpose="starter_plan",
                subscription_id=f"sub_360_{org.id}",
                plan_id="plan_x",
                status=MandateStatus.ACTIVE.value,
                price_paise=99900,
                plan_code="business",
            )
        )
        await async_session.flush()
        row = await _row(async_session, org)
        assert row["plan"] == "business"
        assert row["plan_is_paid"] is True
        assert row["trial"]["on_trial"] is False
        assert row["trial"]["stage"] == "not_on_trial"

    async def test_without_the_trial_flag_the_plan_is_free(
        self, async_session, trial_off
    ):
        org = await _org(async_session, "free")
        row = await _row(async_session, org)
        assert row["plan"] == "free"
        assert row["kyc_status"] == "not_started"
        assert row["byok_keys_present"] is False

    async def test_the_list_costs_the_same_queries_for_many_accounts(
        self, async_session, trial_off
    ):
        """No N+1: the health columns are grouped queries over the page."""

        async def count_for(n_orgs: int) -> int:
            for i in range(n_orgs):
                await _org(async_session, f"n1-{n_orgs}-{i}")
            seen = []

            def _count(*_args, **_kwargs):
                seen.append(1)

            engine = async_session.bind.sync_engine
            event.listen(engine, "before_cursor_execute", _count)
            try:
                await org_health_client.health_for(
                    async_session,
                    [
                        o
                        for (o,) in (
                            await async_session.execute(
                                OrganizationModel.__table__.select()
                                .with_only_columns(OrganizationModel.id)
                                .where(
                                    OrganizationModel.provider_id.like(
                                        f"org-360-n1-{n_orgs}-%"
                                    )
                                )
                            )
                        ).all()
                    ],
                )
            finally:
                event.remove(engine, "before_cursor_execute", _count)
            return len(seen)

        few = await count_for(2)
        many = await count_for(12)
        assert few > 0
        assert few == many


@pytest.mark.asyncio
class TestTheDrillDown:
    async def test_recent_failures_are_the_last_five_of_this_account(
        self, async_session, trial_off
    ):
        mine = await _org(async_session, "fail-mine")
        theirs = await _org(async_session, "fail-theirs")
        me = await _user(async_session, "fail-mine")
        them = await _user(async_session, "fail-theirs")
        await _furnish(async_session, mine, me, failures=7)
        await _furnish(async_session, theirs, them, failures=3)

        detail = await dash.account_detail(async_session, organization_id=mine.id)

        failures = detail["recent_failures"]
        assert len(failures) == 5
        assert all(f"in {mine.id}" in f["summary"] for f in failures)
        # newest first
        assert failures[0]["summary"] == f"failure 6 in {mine.id}"
        assert detail["agents_count"] == 2
        assert detail["plan"] == "free"

    async def test_the_owner_id_is_there_for_impersonate_owner(
        self, async_session, trial_off
    ):
        from api.db.models import OrganizationMembershipModel

        org = await _org(async_session, "owner-id")
        owner = await _user(async_session, "owner-id")
        async_session.add(
            OrganizationMembershipModel(
                user_id=owner.id, organization_id=org.id, role="owner"
            )
        )
        await async_session.flush()

        detail = await dash.account_detail(async_session, organization_id=org.id)

        assert detail["owner_user_id"] == owner.id
        assert detail["members"][0]["user_id"] == owner.id
