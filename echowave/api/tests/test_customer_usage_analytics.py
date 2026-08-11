"""The spend and token views a customer can finally see for themselves.

The aggregation behind these already powered the superadmin screens. What is
new is that a customer can reach their own numbers — and the property that
matters most is the one that makes that safe: **the organization comes from the
authenticated user, never from the request.** The superadmin equivalents take
an `organization_id` parameter; these must not honour one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from api.db.models import (
    CallCostItemModel,
    CreditLedgerModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.enums import CostComponent, CreditLedgerKind


async def _account(session, slug: str, *, balance_paise: int = 0):
    org = OrganizationModel(provider_id=f"org-{slug}", quota_decibyl_tokens=0)
    user = UserModel(provider_id=f"user-{slug}")
    session.add_all([org, user])
    await session.flush()
    user.selected_organization_id = org.id
    if balance_paise:
        session.add(
            CreditLedgerModel(
                organization_id=org.id,
                delta_paise=balance_paise,
                kind=CreditLedgerKind.TOPUP.value,
                balance_after_paise=balance_paise,
            )
        )
    await session.flush()
    return org, user


async def _costed_call(session, org, user, *, when=None, items=None):
    """A completed, costed run with line items — what the charts read."""
    when = when or datetime.now(UTC) - timedelta(days=1)
    workflow = WorkflowModel(
        name="agent", organization_id=org.id, user_id=user.id, status="active"
    )
    session.add(workflow)
    await session.flush()

    run = WorkflowRunModel(
        name=f"run-{workflow.id}",
        workflow_id=workflow.id,
        mode="plivo",
        is_completed=True,
        created_at=when,
        ended_at=when,
        costed_at=when,
    )
    session.add(run)
    await session.flush()

    for component, provider, model, cost, qty in items or []:
        session.add(
            # Cost items carry no organization_id — they scope to an account
            # through workflow_runs -> workflows, which is how every dashboard
            # query joins them.
            CallCostItemModel(
                workflow_run_id=run.id,
                component=component,
                provider=provider,
                model=model,
                units=qty,
                unit_rate_mpaise=0,
                cost_paise=cost,
                created_at=when,
            )
        )
    await session.flush()
    return run


def _client(user):
    from contextlib import asynccontextmanager

    from api.app import app
    from api.services.auth.depends import get_user

    @asynccontextmanager
    async def _ctx():
        async def _override():
            return user

        app.dependency_overrides[get_user] = _override
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_user, None)

    return _ctx()


class TestTenantIsolation:
    async def test_another_orgs_id_in_the_query_is_ignored(
        self, db_session, async_session
    ):
        """The one property that makes a customer-facing analytics route safe.

        The superadmin version takes an organization_id. If this one honoured
        it, any customer could read any other customer's spend.
        """
        mine, my_user = await _account(async_session, "iso-mine")
        theirs, their_user = await _account(async_session, "iso-theirs")

        await _costed_call(
            async_session,
            theirs,
            their_user,
            items=[(CostComponent.LLM.value, "openai", "gpt-4o", 9999, 1000)],
        )

        async with _client(my_user) as client:
            response = await client.get(
                "/api/v1/organizations/usage/spend",
                params={"organization_id": theirs.id},
            )

        assert response.status_code == 200
        # Their spend must not appear in my breakdown.
        assert response.json()["spent_paise"] == 0

    async def test_no_organization_selected_is_a_400(
        self, db_session, async_session
    ):
        user = UserModel(provider_id="user-noorg")
        async_session.add(user)
        await async_session.flush()

        async with _client(user) as client:
            response = await client.get("/api/v1/organizations/usage/tokens")
        assert response.status_code == 400


class TestSpend:
    async def test_spend_is_broken_down_by_component(
        self, db_session, async_session
    ):
        """A total says how much. Only the split says whether the bill is
        speech, language or carriage — which have different fixes."""
        org, user = await _account(async_session, "spend", balance_paise=100000)
        await _costed_call(
            async_session,
            org,
            user,
            items=[
                (CostComponent.LLM.value, "openai", "gpt-4o", 300, 1000),
                (CostComponent.TTS.value, "sarvam", "bulbul", 500, 2000),
                (CostComponent.TELEPHONY.value, "plivo", "", 200, 60),
            ],
        )

        async with _client(user) as client:
            body = (
                await client.get("/api/v1/organizations/usage/spend")
            ).json()

        assert body["spent_paise"] == 1000
        totals = {c: sum(int(r.get(c) or 0) for r in body["series"]) for c in
                  ("llm", "tts", "telephony")}
        assert totals == {"llm": 300, "tts": 500, "telephony": 200}

    async def test_balance_and_days_remaining_are_reported(
        self, db_session, async_session
    ):
        """The question that always follows a spend chart."""
        org, user = await _account(async_session, "burn", balance_paise=100000)
        await _costed_call(
            async_session,
            org,
            user,
            items=[(CostComponent.LLM.value, "openai", "gpt-4o", 1000, 1000)],
        )

        async with _client(user) as client:
            body = (
                await client.get(
                    "/api/v1/organizations/usage/spend", params={"days": 10}
                )
            ).json()

        assert body["balance_paise"] == 100000
        assert body["burn"]["daily_average_paise"] > 0
        assert body["burn"]["days_remaining"] is not None

    async def test_an_account_with_no_spend_reports_zero_not_an_error(
        self, db_session, async_session
    ):
        org, user = await _account(async_session, "nospend", balance_paise=5000)

        async with _client(user) as client:
            body = (
                await client.get("/api/v1/organizations/usage/spend")
            ).json()

        assert body["spent_paise"] == 0
        # No spend means no meaningful projection — null, not a divide by zero.
        assert body["burn"]["days_remaining"] is None


class TestTokens:
    async def test_tokens_are_broken_down_by_model(
        self, db_session, async_session
    ):
        """The half customers ask for: a total says spend rose, the split says
        which model did it and whether a cheaper one would serve."""
        org, user = await _account(async_session, "tok")
        await _costed_call(
            async_session,
            org,
            user,
            items=[
                (CostComponent.LLM.value, "openai", "gpt-4o", 800, 4000),
                (CostComponent.LLM.value, "openai", "gpt-4o-mini", 100, 6000),
            ],
        )

        async with _client(user) as client:
            body = (
                await client.get("/api/v1/organizations/usage/tokens")
            ).json()

        models = {row["model"] for row in body["by_model"]}
        assert {"gpt-4o", "gpt-4o-mini"} <= models

    async def test_the_response_carries_series_and_context_growth(
        self, db_session, async_session
    ):
        org, user = await _account(async_session, "tok-shape")

        async with _client(user) as client:
            body = (
                await client.get("/api/v1/organizations/usage/tokens")
            ).json()

        assert {"range", "granularity", "series", "by_model", "context_growth"} <= set(
            body
        )

    @pytest.mark.parametrize("granularity", ["day", "week", "month"])
    async def test_each_granularity_is_accepted(
        self, db_session, async_session, granularity
    ):
        org, user = await _account(async_session, f"gran-{granularity}")

        async with _client(user) as client:
            response = await client.get(
                "/api/v1/organizations/usage/tokens",
                params={"granularity": granularity},
            )
        assert response.status_code == 200

    async def test_an_unknown_granularity_is_refused(
        self, db_session, async_session
    ):
        org, user = await _account(async_session, "gran-bad")

        async with _client(user) as client:
            response = await client.get(
                "/api/v1/organizations/usage/tokens",
                params={"granularity": "fortnight"},
            )
        assert response.status_code == 422

    async def test_the_window_is_bounded(self, db_session, async_session):
        """An unbounded range is a table scan a customer can trigger at will."""
        org, user = await _account(async_session, "window")

        async with _client(user) as client:
            response = await client.get(
                "/api/v1/organizations/usage/tokens", params={"days": 100000}
            )
        assert response.status_code == 422
